-- EV-029 CHG-132 (deploy report 023 E-2, Q-N2): the deploy side's registration check reads LLMOps' deployment ledger with
-- runtime_reader — llmops USAGE and SELECT on llmops.deployment only; no other llmops table, no write. Shared databases only
-- after the user's A5.
GRANT USAGE ON SCHEMA llmops TO runtime_reader;
GRANT SELECT ON llmops.deployment TO runtime_reader;
