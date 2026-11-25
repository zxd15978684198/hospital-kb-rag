-- 0017_worker_role.sql
--
-- Worker 需要一个能跨租户扫任务的连库角色。
--
-- 为什么不能继续用 pyknora_app：0003 / 0011 的 `ws_isolation` 策略要求
-- `current_setting('app.workspace_id', true)` 有值，而 worker 认领任务是
-- `FOR UPDATE SKIP LOCKED` 跨整张 ingestion_jobs 扫——没有"当前 workspace"可言，
-- 策略于是恒为假，**一个任务都认领不到**。症状极其安静：任务一直 pending、
-- worker 日志无报错、API 的 /jobs 只是永不终态。
--
-- 所以 worker 用独立的 BYPASSRLS 角色：它是受信系统组件，天然要跨租户；
-- 面向用户的查询路径仍走 pyknora_app，RLS 那层保护一点没减。
--
-- 这也是"给角色分层"的本意：app 角色配 RLS 保护读路径，worker 角色专属后台作业，
-- 迁移仍用 postgres（建表要 owner）。

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pyknora_worker') THEN
        CREATE ROLE pyknora_worker LOGIN PASSWORD 'pyknora_worker_dev'
            NOSUPERUSER BYPASSRLS;
    ELSE
        -- 已存在时也把属性纠正过来：手滑改成 NOSUPERUSER 但没 BYPASSRLS 的话，
        -- 作业会再次静默卡死，而这类失败不会在启动时报错。
        ALTER ROLE pyknora_worker NOSUPERUSER BYPASSRLS;
    END IF;
END
$$;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO pyknora_worker', current_database());
END
$$;

GRANT USAGE ON SCHEMA public TO pyknora_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO pyknora_worker;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pyknora_worker;

-- 之后新建的表自动继承同一组权限，否则每加一张表就要手工补 GRANT，
-- 漏一次就是 worker 运行时 permission denied。
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pyknora_worker;
