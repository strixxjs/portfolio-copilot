-- Roles: copilot_app runs the application (seeding, retrieval, traces, cache);
-- copilot_readonly runs model-generated SQL only, under RLS, read-only, with a statement timeout.
-- Dev passwords are placeholders; scripts/migrate.py overrides them from APP_DB_PASSWORD / RO_DB_PASSWORD.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'copilot_app') THEN
        CREATE ROLE copilot_app LOGIN PASSWORD 'copilot_app';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'copilot_readonly') THEN
        CREATE ROLE copilot_readonly LOGIN PASSWORD 'copilot_readonly' NOINHERIT;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO copilot_app, copilot_readonly;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO copilot_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO copilot_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO copilot_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO copilot_app;

-- The read-only role needs SELECT on the base tables because the semantic views are security_invoker;
-- RLS below reduces what it can actually see.
GRANT SELECT ON projects, change_orders, budget_lines, milestones TO copilot_readonly;

ALTER ROLE copilot_readonly SET statement_timeout = '3000ms';
ALTER ROLE copilot_readonly SET default_transaction_read_only = on;
ALTER ROLE copilot_readonly SET search_path = semantic, public;

ALTER TABLE projects      ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects      FORCE  ROW LEVEL SECURITY;
ALTER TABLE change_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE change_orders FORCE  ROW LEVEL SECURITY;
ALTER TABLE budget_lines  ENABLE ROW LEVEL SECURITY;
ALTER TABLE budget_lines  FORCE  ROW LEVEL SECURITY;
ALTER TABLE milestones    ENABLE ROW LEVEL SECURITY;
ALTER TABLE milestones    FORCE  ROW LEVEL SECURITY;

-- The application role is trusted: it seeds and reads everything.
CREATE POLICY app_all ON projects      FOR ALL TO copilot_app USING (true) WITH CHECK (true);
CREATE POLICY app_all ON change_orders FOR ALL TO copilot_app USING (true) WITH CHECK (true);
CREATE POLICY app_all ON budget_lines  FOR ALL TO copilot_app USING (true) WITH CHECK (true);
CREATE POLICY app_all ON milestones    FOR ALL TO copilot_app USING (true) WITH CHECK (true);

-- The read-only role sees only projects granted to the current app user (set per transaction).
CREATE POLICY ro_by_project_acl ON projects FOR SELECT TO copilot_readonly
    USING (id IN (SELECT visible_project_ids(current_app_user())));
CREATE POLICY ro_by_project_acl ON change_orders FOR SELECT TO copilot_readonly
    USING (project_id IN (SELECT visible_project_ids(current_app_user())));
CREATE POLICY ro_by_project_acl ON budget_lines FOR SELECT TO copilot_readonly
    USING (project_id IN (SELECT visible_project_ids(current_app_user())));
CREATE POLICY ro_by_project_acl ON milestones FOR SELECT TO copilot_readonly
    USING (project_id IN (SELECT visible_project_ids(current_app_user())));
