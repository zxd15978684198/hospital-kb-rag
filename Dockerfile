# PyKnora API / Worker 共用镜像（api 与 worker 只差启动命令，见 docker-compose.yml）。
#
# 设计要点（每条都有代价，写清为什么这么选）：
# 1. 三个阶段：builder 装依赖并编译字节码，runtime 只拿结果。runtime 不装 uv，
#    生产容器里少一个能改环境的工具。
# 2. 依赖层与代码层分开 COPY：改一行业务代码不会让 `uv sync` 那层缓存失效。
# 3. 非 root（uid/gid 10001）。镜像里没有任何需要 root 的步骤；
#    `/data/hf` 是模型缓存卷，chown 之后再切用户，否则首次下载写不进去。
# 4. 健康检查用 python 内建 urllib，不装 curl（少一个包、少一层 apt）。
# 5. 默认带 `--extra local-embed`：容器里没有宿主那套 `.venv`，embedding 只能走
#    sentence-transformers。镜像因此大很多（torch 占大头）；只想跑远端 embedding 时
#    用 `--build-arg INSTALL_LOCAL_EMBED=false`，见 ARG 注释。
#
# 构建：docker build -t pyknora-api:local .
# 运行：见 docker-compose.yml；单独跑见 docs/部署与回滚.md
# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.9

# 网络相关的三个旋钮（踩过的坑，别再猜）：
# - uv 默认 UV_HTTP_TIMEOUT=30s。`--extra local-embed` 会拉 torch 与一串 nvidia-* wheel，
#   单个包几十到几百 MB；国内直连 files.pythonhosted.org 实测 13–45 KiB/s，
#   于是构建几乎每次死在 `Failed to download nvidia-nvjitlink==...` / `network timeout`。
#   放大超时与重试是止血，换索引才是根治。
# - UV_INDEX 留空 = 走 pypi 官方；国内可传镜像，例如
#   `--build-arg UV_INDEX=https://mirrors.aliyun.com/pypi/simple/`（或 compose 里给同名环境变量）。
#   镜像同样提供 torch 的 CPU 版 wheel，比 CUDA 版小一个数量级。
ARG UV_INDEX=
ARG UV_HTTP_TIMEOUT=600
ARG UV_HTTP_RETRIES=8

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---------------------------------------------------------------- builder
FROM python:${PYTHON_VERSION}-slim AS builder

# 全局 ARG 一旦过了 FROM 就不在作用域里了，必须在每个 stage 内重新声明一次。
# 漏了这一步 BuildKit 只报警告（`UndefinedVar: '$UV_HTTP_TIMEOUT'`），
# ENV 展开成空串，uv 的超时悄悄回到默认 30s —— 表现为"改了但没生效"。
ARG UV_INDEX=
ARG UV_HTTP_TIMEOUT=600
ARG UV_HTTP_RETRIES=8

COPY --from=uv /uv /uvx /bin/

# UV_LINK_MODE=copy：容器里没有硬链接跨层这回事，copy 避免 "failed to link" 噪音。
# UV_PYTHON_DOWNLOADS=0：基础镜像已经是目标解释器，别让 uv 再下一份。
# UV_HTTP_TIMEOUT / UV_HTTP_RETRIES：见文件头，默认 30s 撑不住 torch 的 CUDA 依赖。
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_FROZEN=1 \
    UV_HTTP_TIMEOUT=${UV_HTTP_TIMEOUT} \
    UV_HTTP_RETRIES=${UV_HTTP_RETRIES}

WORKDIR /app

# 依赖层：只有 pyproject.toml / uv.lock 变才失效。
COPY pyproject.toml uv.lock ./
ARG INSTALL_LOCAL_EMBED=true
# 索引与 local-embed 两个开关都拼成变量：`--default-index` 只在显式给了 UV_INDEX 时出现，
# 空串直接展开成命令的一部分会被 uv 当成「索引地址为空」而报错。
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eu; \
    index_arg=""; \
    if [ -n "${UV_INDEX:-}" ]; then index_arg="--default-index ${UV_INDEX}"; fi; \
    embed_arg=""; \
    if [ "$INSTALL_LOCAL_EMBED" = "true" ]; then embed_arg="--extra local-embed"; fi; \
    uv sync --frozen --no-install-project --no-dev $index_arg $embed_arg

# 代码层
COPY . .

# 第二次 sync 把项目本身装进去（可编辑安装），此时依赖层已缓存。
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eu; \
    index_arg=""; \
    if [ -n "${UV_INDEX:-}" ]; then index_arg="--default-index ${UV_INDEX}"; fi; \
    embed_arg=""; \
    if [ "$INSTALL_LOCAL_EMBED" = "true" ]; then embed_arg="--extra local-embed"; fi; \
    uv sync --frozen --no-dev $index_arg $embed_arg

# ---------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim AS runtime

# rapidocr-onnxruntime 链到 opencv，opencv 运行时需要 libGL / libglib；
# 不装这两个会得到 `ImportError: libGL.so.1: cannot open shared object file`。
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/data/hf \
    PYKNORA_LOG_LEVEL=INFO

WORKDIR /app

COPY --from=builder --chown=10001:10001 /app /app

# 模型缓存卷的挂载点必须先存在且属于运行用户
RUN mkdir -p /data/hf && chown -R 10001:10001 /data/hf

USER 10001:10001

EXPOSE 8010

# 容器里没有 curl，用解释器自己探活；3 秒超时避免 healthcheck 把 hung 当 healthy。
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD python -c "import sys,urllib.request; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "pyknora.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8010"]
