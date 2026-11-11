-- 0011_c1a15_index.sql — C1-a15 Pack vector DB in the shared DB (D-298 Q-1 appendix A · D-363 (6)): schema c1a15 with the same
-- data_release columns as task10 0002 and the general chunk table index_chunk that packrt.index load writes (packrt 0.12.1).
-- index_loader (dbadmin ROLES, INSERT only) loads; runtime_reader · llmops_app read. No UPDATE · DELETE · TRUNCATE for anyone but db_owner.
CREATE SCHEMA c1a15 AUTHORIZATION db_owner;
CREATE TABLE c1a15.data_release (
  release_id text PRIMARY KEY, store text NOT NULL, kind text NOT NULL CHECK (kind IN ('table','index')),
  sha256 text NOT NULL, manifest jsonb NOT NULL,
  embedding_provider text, embedding_model text, embedding_dim integer,
  embedding_mode text CHECK (embedding_mode IN ('real','fake')),
  loaded_at timestamptz NOT NULL DEFAULT now(), loaded_by text NOT NULL DEFAULT current_user,
  CHECK ((kind = 'index') = (embedding_mode IS NOT NULL AND embedding_model IS NOT NULL AND embedding_dim IS NOT NULL)));
CREATE TABLE c1a15.index_chunk (
  release_id text NOT NULL REFERENCES c1a15.data_release(release_id),
  chunk_id   text NOT NULL,
  text       text NOT NULL,
  metadata   jsonb NOT NULL,
  locator    jsonb NOT NULL,
  embedding  vector NOT NULL,
  PRIMARY KEY (release_id, chunk_id));
GRANT USAGE ON SCHEMA c1a15 TO index_loader, runtime_reader, llmops_app;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA c1a15 TO index_loader;      -- no UPDATE, DELETE, TRUNCATE
GRANT SELECT ON ALL TABLES IN SCHEMA c1a15 TO runtime_reader, llmops_app;
ALTER DEFAULT PRIVILEGES FOR ROLE db_owner IN SCHEMA c1a15 GRANT SELECT, INSERT ON TABLES TO index_loader;
ALTER DEFAULT PRIVILEGES FOR ROLE db_owner IN SCHEMA c1a15 GRANT SELECT ON TABLES TO runtime_reader, llmops_app;
