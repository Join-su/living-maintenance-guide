-- 0001_llmops_schema_version.sql — llmops schema and the schema version record (db_owner).
-- Shared DB design v1.0 appendix A 0001. Week 1 (staged application): the llmops schema holds only schema_version;
-- LLMOps records stay in SQLite llmops.db until week 2 (0004_llmops_tables.sql).
CREATE SCHEMA llmops AUTHORIZATION db_owner;
CREATE TABLE llmops.schema_version (
  version     integer PRIMARY KEY,           -- change file number
  name        text NOT NULL,
  applied_at  timestamptz NOT NULL DEFAULT now(),
  sha256      text NOT NULL                  -- change file content hash
);
