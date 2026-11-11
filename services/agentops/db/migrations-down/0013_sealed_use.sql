-- Back from db/migrations/0013_sealed_use.sql (D-486 덧 9), as db_owner: the table with its rows (the uses outside a wheel are lost —
-- keep a copy first if they matter: COPY llmops.sealed_use TO …) and its schema_version row, so dbadmin migrate can apply 0013 again.
DROP TABLE llmops.sealed_use;
DELETE FROM llmops.schema_version WHERE version = 13 AND name = 'sealed_use';
