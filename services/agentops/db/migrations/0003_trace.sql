-- 0003_trace.sql — run records of every Pack (append only). Shared DB design v1.0 appendix A 0004 and appendix B, week-1 form:
-- trace.run has no foreign keys to llmops tables yet (LLMOps records are in SQLite until week 2); the trace writer checks
-- registration in code before run_started (registration_check). 0005_trace_llmops_fks.sql adds the foreign keys in week 2.
CREATE SCHEMA trace AUTHORIZATION db_owner;

CREATE TABLE trace.run (
  run_id            text PRIMARY KEY,
  trace_id          char(32) NOT NULL,              -- OpenTelemetry form (16-byte hex)
  origin            text NOT NULL CHECK (origin IN ('evaluation', 'deployment')),
  source_kind       text NOT NULL CHECK (source_kind IN ('agent_pack', 'langgraph')),
  evaluation_run_id text,                           -- week 2: REFERENCES llmops.evaluation_run
  deployment_id     text,
  pack_id           text NOT NULL,                  -- langgraph: release name
  pack_revision     text NOT NULL,                  -- langgraph: code hash
  data_release_id   text,                           -- langgraph: empty
  release_bindings  jsonb NOT NULL,                 -- langgraph: {}
  runtime_version   text NOT NULL,
  graph             text NOT NULL,
  manifest_sha256   text NOT NULL,
  redaction_policy_version text NOT NULL,
  retention_class   text NOT NULL,
  started_at        timestamptz NOT NULL,
  recorded_at       timestamptz NOT NULL DEFAULT now(),
  CHECK ((origin = 'evaluation') = (evaluation_run_id IS NOT NULL AND deployment_id IS NULL)),
  CHECK ((origin = 'deployment') = (deployment_id IS NOT NULL AND evaluation_run_id IS NULL)),
  CHECK (source_kind = 'agent_pack' OR (origin = 'evaluation' AND data_release_id IS NULL)),
  CHECK (source_kind = 'langgraph' OR data_release_id IS NOT NULL)
  -- week 2: FOREIGN KEY (deployment_id, pack_revision, data_release_id) REFERENCES llmops.deployment (...)
);

CREATE TABLE trace.event (                           -- the record of truth
  run_id         text NOT NULL REFERENCES trace.run(run_id),
  seq            integer NOT NULL CHECK (seq >= 0),
  span_id        char(16) NOT NULL,
  parent_span_id char(16),
  type           text NOT NULL,
  node_id        text,
  at             timestamptz NOT NULL,               -- run-side time
  recorded_at    timestamptz NOT NULL DEFAULT now(),
  contract       integer NOT NULL,
  body           jsonb NOT NULL,                     -- references only, no raw values
  body_sha256    text NOT NULL,
  PRIMARY KEY (run_id, seq)
);

CREATE TABLE trace.node_event (run_id text, seq integer, node_id text, iteration integer, kind text, status text,
  started_at timestamptz, ended_at timestamptz, input_ref text, output_ref text, error jsonb,
  provider text, model text, request_config jsonb, tokens_in integer, tokens_out integer,
  prompt_ref text, provider_raw_ref text, custom_source_hash text, expect_checks jsonb, route jsonb, checks jsonb,
  PRIMARY KEY (run_id, seq), FOREIGN KEY (run_id, seq) REFERENCES trace.event(run_id, seq));
CREATE TABLE trace.retrieval (run_id text, seq integer, node_id text, store text, release_id text,
  query_ref text, query_sha256 text, filter_applied jsonb, results jsonb, fallback_used boolean, fallback_reason text,
  embedding_mode text, embedding_model text, embed_tokens integer,
  PRIMARY KEY (run_id, seq), FOREIGN KEY (run_id, seq) REFERENCES trace.event(run_id, seq));
CREATE TABLE trace.review (run_id text, seq integer, node_id text, review_id text, event text, action text,
  actor text, actor_kind text CHECK (actor_kind IN ('human', 'fixture', 'model', 'auto')), role text, reason text,
  edited_fields jsonb, edited_value_ref text, revalidation jsonb, at timestamptz,
  PRIMARY KEY (run_id, seq), FOREIGN KEY (run_id, seq) REFERENCES trace.event(run_id, seq));
CREATE TABLE trace.artifact (run_id text, seq integer, node_id text, format text, sha256 text, location text, size bigint,
  PRIMARY KEY (run_id, seq), FOREIGN KEY (run_id, seq) REFERENCES trace.event(run_id, seq));
CREATE TABLE trace.payload_ref (                     -- raw value file references and retention class (append only)
  sha256       text NOT NULL,                        -- of the bytes after redaction
  run_id       text NOT NULL REFERENCES trace.run(run_id),
  role         text NOT NULL,
  size         bigint NOT NULL,
  media_type   text NOT NULL,
  location     text NOT NULL,                        -- path inside the payload store
  retention_class text NOT NULL CHECK (retention_class IN ('standard', 'provider_raw', 'dev')),
  redaction_policy_version text NOT NULL,
  recorded_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (sha256, run_id, role)
);
CREATE TABLE trace.payload_expiration (              -- expiry record (append only)
  sha256 text NOT NULL, expired_at timestamptz NOT NULL DEFAULT now(), reason text NOT NULL,
  PRIMARY KEY (sha256, expired_at)
);
CREATE VIEW trace.run_current AS                     -- screens: the last run_ended
  SELECT r.*, e.body->>'status' AS status, e.body->>'outcome' AS outcome, e.at AS ended_at
  FROM trace.run r LEFT JOIN LATERAL (
    SELECT * FROM trace.event x WHERE x.run_id = r.run_id AND x.type = 'run_ended' ORDER BY seq DESC LIMIT 1) e ON true;

-- the writer adds only; to tell a repeat from a conflict it reads the key and hash columns of trace.event only (appendix B)
GRANT USAGE ON SCHEMA trace TO trace_writer;
GRANT INSERT ON ALL TABLES IN SCHEMA trace TO trace_writer;                -- no UPDATE, DELETE, TRUNCATE
GRANT SELECT (run_id, seq, body_sha256) ON trace.event TO trace_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE db_owner IN SCHEMA trace GRANT INSERT ON TABLES TO trace_writer;
