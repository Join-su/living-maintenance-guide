-- 0002_task10.sql — Pack business data (per release_id, never changed). Shared DB design v1.0 appendix A 0002 and appendix B.
-- The vector extension is created by dbadmin init (superuser) before this file runs. Loading is the task10-pack session's job.
CREATE SCHEMA task10 AUTHORIZATION db_owner;

CREATE TABLE task10.data_release (
  release_id         text PRIMARY KEY,                       -- e.g. hc-ai4i2020-s7-v2, ir-manuals-v3, ir-manuals-v3-fake
  store              text NOT NULL,                          -- Pack stores name (history, manuals)
  kind               text NOT NULL CHECK (kind IN ('table', 'index')),
  sha256             text NOT NULL,                          -- release bundle content hash (manifest files)
  manifest           jsonb NOT NULL,                         -- release manifest (no secret values)
  embedding_provider text,
  embedding_model    text,
  embedding_dim      integer,
  embedding_mode     text CHECK (embedding_mode IN ('real', 'fake')),
  loaded_at          timestamptz NOT NULL DEFAULT now(),
  loaded_by          text NOT NULL DEFAULT current_user,
  CHECK ((kind = 'index') = (embedding_mode IS NOT NULL AND embedding_model IS NOT NULL AND embedding_dim IS NOT NULL))
);

CREATE TABLE task10.maintenance_record (              -- test Pack: case_id. The real Pack makes a new release with record_id (D1)
  release_id              text NOT NULL REFERENCES task10.data_release(release_id),
  case_id                 text NOT NULL,
  air_temperature_k       double precision,
  process_temperature_k   double precision,
  rotational_speed_rpm    double precision,
  torque_nm               double precision,
  tool_wear_min           integer,
  observed_alarm_code     text,
  confirmed_failure_type  text,
  maintenance_action      text,
  outcome                 text,
  source_row_id           text,
  source_cycle            integer,
  provenance              text,
  PRIMARY KEY (release_id, case_id)
);

CREATE TABLE task10.manual_chunk (
  release_id   text NOT NULL REFERENCES task10.data_release(release_id),
  chunk_id     text NOT NULL,                      -- e.g. M-COMMON-ESC-01#0
  manual_id    text NOT NULL,
  text         text NOT NULL,
  metadata     jsonb NOT NULL,
  embedding    vector NOT NULL,                    -- dimension differs per release (real 1536 / fake 64): no fixed dimension, no index
  PRIMARY KEY (release_id, chunk_id)
);
-- The loader checks vector_dims(embedding) = data_release.embedding_dim in the same transaction (the column has no fixed dimension).

-- loading adds only, runs read only (appendix B)
GRANT USAGE ON SCHEMA task10 TO task10_loader, runtime_reader;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA task10 TO task10_loader;      -- no UPDATE, DELETE, TRUNCATE
GRANT SELECT ON ALL TABLES IN SCHEMA task10 TO runtime_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE db_owner IN SCHEMA task10 GRANT SELECT, INSERT ON TABLES TO task10_loader;
ALTER DEFAULT PRIVILEGES FOR ROLE db_owner IN SCHEMA task10 GRANT SELECT ON TABLES TO runtime_reader;
