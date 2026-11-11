-- 0004_llmops_app_read.sql — EV-025 CHG-127 (deploy design 3 ⑨, answer 020-01 Q-4, user SA-1 10-18 13:56): the LLMOps server
-- reads the deploy side's run records with its own account llmops_app — SELECT on the trace schema only. No INSERT, UPDATE,
-- DELETE or TRUNCATE; no other schema. The account itself (LOGIN, password from db/.env LLMOPS_APP_PASSWORD, CONNECT,
-- search_path trace) is made by `python db/dbadmin.py init` like the other accounts (ROLES).
-- Number: dbadmin needs 1, 2, 3 … without a gap; 0004 · 0005 were only reserved in the shared DB design (llmops tables, FKs) and
-- move to 0005 · 0006 at the 10-21 PG move (question 021-01 Q-A).
GRANT USAGE ON SCHEMA trace TO llmops_app;
GRANT SELECT ON ALL TABLES IN SCHEMA trace TO llmops_app;                       -- the tables and the view run_current
ALTER DEFAULT PRIVILEGES FOR ROLE db_owner IN SCHEMA trace GRANT SELECT ON TABLES TO llmops_app;
