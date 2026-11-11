-- 0010_ledger_model_release.sql — EV-044 CHG-219 · 222 (D-263 E · D-265 (3) · D-283 (3), answer 096 = D-326): the deployment ledger
-- records the model release (packrt `mrs:` id, null = no model or a row written before this file — model unknown) and its bindings;
-- one row is (deployment_id, pack_revision, data_release_id, model_release_id), so a model-only change is a new row. decision gets the
-- automatic kind data_release_auto (R12 '자료 바꿈', no person — D-253 (2)). Adds columns only: rows already in the table keep their
-- bytes (the append-only triggers stop UPDATE · DELETE, not ADD COLUMN). The week-2 three-column foreign key from trace.run is
-- withdrawn (Q-G); the registration check stays the three-column function check. db_owner through dbadmin migrate; the shared
-- databases only after the user's approval (Q-E).
ALTER TABLE llmops.deployment ADD COLUMN model_release_id text, ADD COLUMN model_bindings text;
ALTER TABLE llmops.deployment ADD CONSTRAINT deployment_mrs_form CHECK (model_release_id IS NULL OR model_release_id ~ '^mrs:[0-9a-f]{16}$');
ALTER TABLE llmops.deployment DROP CONSTRAINT deployment_pkey;
CREATE UNIQUE INDEX deployment_key ON llmops.deployment (deployment_id, pack_revision, data_release_id, COALESCE(model_release_id, ''));
ALTER TABLE llmops.decision DROP CONSTRAINT decision_decision_check;
ALTER TABLE llmops.decision ADD CONSTRAINT decision_decision_check
  CHECK (decision IN ('change_approved', 'hold', 'rejected', 'deploy_approved', 'deploy_bootstrap', 'deploy_rolled_back', 'deploy_retired',
                      'data_release_auto'));
