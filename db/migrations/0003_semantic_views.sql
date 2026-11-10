-- Governed semantic layer. The text-to-SQL prompt is rendered from these views and their COMMENTs
-- (app/tools/semantic_layer.py), so the comments are the documentation the model sees.

CREATE SCHEMA IF NOT EXISTS semantic;
GRANT USAGE ON SCHEMA semantic TO copilot_readonly, copilot_app;

CREATE VIEW semantic.projects WITH (security_invoker = true) AS
SELECT id AS project_id, name, client, sector, country, contract_value, currency,
       start_date, planned_end_date, status
FROM public.projects;
COMMENT ON VIEW semantic.projects IS 'One row per construction project in the portfolio.';
COMMENT ON COLUMN semantic.projects.project_id IS 'Project code, e.g. P1. Join key for every other view.';
COMMENT ON COLUMN semantic.projects.name IS 'Project name.';
COMMENT ON COLUMN semantic.projects.client IS 'Client organisation.';
COMMENT ON COLUMN semantic.projects.sector IS 'transport | healthcare | industrial.';
COMMENT ON COLUMN semantic.projects.country IS 'ISO-like country name, e.g. United Kingdom.';
COMMENT ON COLUMN semantic.projects.contract_value IS 'Original contract value in `currency`.';
COMMENT ON COLUMN semantic.projects.currency IS 'ISO 4217 code, GBP for all seeded projects.';
COMMENT ON COLUMN semantic.projects.start_date IS 'Contractual start date.';
COMMENT ON COLUMN semantic.projects.planned_end_date IS 'Contractual completion date.';
COMMENT ON COLUMN semantic.projects.status IS 'planning | active | on_hold | closed.';

CREATE VIEW semantic.change_orders WITH (security_invoker = true) AS
SELECT id AS change_order_id, project_id, number, title, reason_category, amount, status,
       submitted_on, decided_on, source_doc_id
FROM public.change_orders;
COMMENT ON VIEW semantic.change_orders IS 'One row per change order (variation). Amount is positive for additions, negative for omissions.';
COMMENT ON COLUMN semantic.change_orders.change_order_id IS 'Change order id, e.g. P1-CO-003.';
COMMENT ON COLUMN semantic.change_orders.project_id IS 'Project code.';
COMMENT ON COLUMN semantic.change_orders.number IS 'Sequential number within the project.';
COMMENT ON COLUMN semantic.change_orders.title IS 'Short title.';
COMMENT ON COLUMN semantic.change_orders.reason_category IS 'client_request | design_error | unforeseen_ground | regulatory | value_engineering.';
COMMENT ON COLUMN semantic.change_orders.amount IS 'Value in project currency; use status = approved for committed cost.';
COMMENT ON COLUMN semantic.change_orders.status IS 'draft | submitted | approved | rejected | withdrawn.';
COMMENT ON COLUMN semantic.change_orders.submitted_on IS 'Submission date.';
COMMENT ON COLUMN semantic.change_orders.decided_on IS 'Approval/rejection date, NULL while open.';
COMMENT ON COLUMN semantic.change_orders.source_doc_id IS 'Document id of the change order document (for citations via open_document_section).';

CREATE VIEW semantic.budget_lines WITH (security_invoker = true) AS
SELECT project_id, cost_code, description, category, budget_amount, committed_amount, actual_amount,
       (actual_amount - budget_amount) AS variance_amount
FROM public.budget_lines;
COMMENT ON VIEW semantic.budget_lines IS 'Cost breakdown per project and cost code.';
COMMENT ON COLUMN semantic.budget_lines.project_id IS 'Project code.';
COMMENT ON COLUMN semantic.budget_lines.cost_code IS 'Cost code, e.g. 03-100.';
COMMENT ON COLUMN semantic.budget_lines.description IS 'What the line covers.';
COMMENT ON COLUMN semantic.budget_lines.category IS 'labour | materials | plant | subcontract | prelims | contingency.';
COMMENT ON COLUMN semantic.budget_lines.budget_amount IS 'Approved budget.';
COMMENT ON COLUMN semantic.budget_lines.committed_amount IS 'Value of placed orders and subcontracts.';
COMMENT ON COLUMN semantic.budget_lines.actual_amount IS 'Cost incurred to date.';
COMMENT ON COLUMN semantic.budget_lines.variance_amount IS 'actual_amount - budget_amount; positive means overspend.';

CREATE VIEW semantic.milestones WITH (security_invoker = true) AS
SELECT project_id, name, planned_date, forecast_date, actual_date, status, critical,
       (forecast_date - planned_date) AS slip_days
FROM public.milestones;
COMMENT ON VIEW semantic.milestones IS 'Schedule milestones per project.';
COMMENT ON COLUMN semantic.milestones.project_id IS 'Project code.';
COMMENT ON COLUMN semantic.milestones.name IS 'Milestone name.';
COMMENT ON COLUMN semantic.milestones.planned_date IS 'Baseline date.';
COMMENT ON COLUMN semantic.milestones.forecast_date IS 'Current forecast date.';
COMMENT ON COLUMN semantic.milestones.actual_date IS 'Achieved date, NULL if not achieved.';
COMMENT ON COLUMN semantic.milestones.status IS 'not_started | in_progress | complete | delayed.';
COMMENT ON COLUMN semantic.milestones.critical IS 'true when on the critical path.';
COMMENT ON COLUMN semantic.milestones.slip_days IS 'forecast_date - planned_date in days; positive means late.';

CREATE VIEW semantic.project_cost_summary WITH (security_invoker = true) AS
SELECT p.id AS project_id,
       p.name AS project_name,
       p.contract_value,
       COALESCE(b.budget_total, 0)            AS budget_total,
       COALESCE(b.committed_total, 0)         AS committed_total,
       COALESCE(b.actual_total, 0)            AS actual_total,
       COALESCE(co.approved_change_total, 0)  AS approved_change_total,
       COALESCE(co.approved_change_count, 0)  AS approved_change_count
FROM public.projects p
LEFT JOIN (
    SELECT project_id, SUM(budget_amount) AS budget_total, SUM(committed_amount) AS committed_total,
           SUM(actual_amount) AS actual_total
    FROM public.budget_lines GROUP BY project_id
) b ON b.project_id = p.id
LEFT JOIN (
    SELECT project_id, SUM(amount) AS approved_change_total, COUNT(*) AS approved_change_count
    FROM public.change_orders WHERE status = 'approved' GROUP BY project_id
) co ON co.project_id = p.id;
COMMENT ON VIEW semantic.project_cost_summary IS 'Pre-aggregated cost position per project. Prefer this view for totals.';
COMMENT ON COLUMN semantic.project_cost_summary.project_id IS 'Project code.';
COMMENT ON COLUMN semantic.project_cost_summary.project_name IS 'Project name.';
COMMENT ON COLUMN semantic.project_cost_summary.contract_value IS 'Original contract value.';
COMMENT ON COLUMN semantic.project_cost_summary.budget_total IS 'Sum of budget_lines.budget_amount.';
COMMENT ON COLUMN semantic.project_cost_summary.committed_total IS 'Sum of budget_lines.committed_amount.';
COMMENT ON COLUMN semantic.project_cost_summary.actual_total IS 'Sum of budget_lines.actual_amount.';
COMMENT ON COLUMN semantic.project_cost_summary.approved_change_total IS 'Sum of approved change order amounts.';
COMMENT ON COLUMN semantic.project_cost_summary.approved_change_count IS 'Number of approved change orders.';

GRANT SELECT ON ALL TABLES IN SCHEMA semantic TO copilot_readonly, copilot_app;
