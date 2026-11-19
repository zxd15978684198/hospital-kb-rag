# 评测工具

本目录包含 PyKnora 的离线评测工具。语料本身也在这里生成，所以整套评测可以开箱跑通：

- `corpus.py` 用字符串常量生成 5 份**虚构公司**（澜川实业有限公司）的制度语料，每份的
  sha256 固定在 `corpus_manifest.json` 里，换机器重跑也一致；
- 生成出来的语料与评测集落在 `.tmp_ref/eval_corpus/` 下（已被 `.gitignore` 覆盖）；
- `eval/results/` 同样不进版本库，仓库里没有历史基线结果。

要换用自己的文档集合，改 `corpus.py` 的语料常量、以及 `build_eval_set.py` 里各样本的
定位句即可；两份文件都刻意不含任何真实企业文档。

## 目录

| 路径 | 作用 |
| --- | --- |
| `corpus.py` | 生成并校验评测语料（虚构公司，字节可复现） |
| `build_eval_set.py` | 基于语料构建 30 条固定评测集（8 类行为） |
| `make_configs.py` | 生成 baseline 与 candidate 实验配置 |
| `run_eval.py` | 对运行中的 HTTP API 执行评测 |
| `gate.py` | 比较 baseline 与 candidate 两轮结果 |
| `report.py` | 渲染评测报告 |
| `selfcheck.py` | 校验评测工具链 |

## 指标

评测工具会测量：

- `recall_at_5`
- `precision_at_5`
- `citation_accuracy`
- `abstain_accuracy`
- 权限拦截率
- `retrieval_p95_ms`
- `chat_ttft_p95_ms`
- `chat_total_p95_ms`

指标解释和限制见 [`../EVALUATION.md`](../EVALUATION.md)。

## 通用用法

```bash
# 1. 生成并校验语料（--verify-only 只比对清单，不写盘）
uv run python eval/corpus.py
uv run python eval/corpus.py --verify-only

# 2. 构建评测集
uv run python eval/build_eval_set.py --build

# 3. 生成 baseline 和 candidate 配置
uv run python eval/make_configs.py

# 4. 执行评测
uv run python eval/run_eval.py --config eval/baseline_config.yaml --out eval/results/baseline

# 5. 比较两轮结果
uv run python eval/gate.py --baseline eval/results/baseline --candidate eval/results/candidate
```

具体参数会根据你的工作区设置变化。评测工具不含任何真实企业文档或凭据。
