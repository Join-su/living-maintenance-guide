# packrt · tracewriter 공개 이름 (PUBLIC-API)

- 다른 저장소(LLMOps 서버 · Deployment Server · Pack 저장소의 도구 · 시험)가 부를 수 있는 이름입니다. 여기 없는 이름, 밑줄로 시작하는 이름은 안쪽 이름이라 판 사이에 바뀔 수 있습니다.
- 실행 진입점: `python -m packrt.runner <pack 폴더>`, `python -m packrt.probe <pack 폴더>`.
- 목록을 바꾸면 판(packrt `__version__` 또는 저장소 태그)을 올립니다. 이름을 지우는 것은 부르는 쪽이 모두 옮긴 뒤에만 합니다.
- 출처: EV-023(llmops-lite-v2) CHG-201 — 세 저장소(llmops-lite-v2 · task10-pack · llmopsagent-deploy) import 목록에서 뽑음. llmops 쪽 사본은 `tests/fixtures/packrt-public-api.json`(그 저장소).

## `packrt`

- `IR_VERSION`
- `__version__`
- `ir`
- `release_line`
- `trace`

## `packrt.fake.jev`

- `FakeJevProvider`
- `respond`

## `packrt.index`

- `BUILDER`
- `IndexBuildError`
- `RECIPE_KEYS`
- `RecipeError`
- `build_sha256`
- `find_corpus`
- `index_recipe`
- `index_recipe_of`
- `index_recipe_sha256`
- `release_id`

## `packrt.ir`

- `check_fixed_flow`
- `compute_revision`
- `fixed_flow`
- `flow`
- `load_pack`
- `node_options`
- `pg`
- `releases`
- `revision`
- `semantic`
- `strict_schema_problems`
- `strict_yaml`
- `yaml_write`

## `packrt.ir.flow`

- `check`
- `written_before`

## `packrt.ir.patch`

- `ADDABLE`
- `apply_patch`
- `bridge_ops`
- `delete_impact`
- `editable_params`

## `packrt.ir.pg`

- `connect`

## `packrt.ir.releases`

- `IndexAmbiguous`
- `IndexMissing`
- `RUNTIME_DSN_ENV`
- `ReleaseError`
- `SECRET_VALUE_RE`
- `config_sha256`
- `dir_hash`
- `load_execution_config`
- `uses_postgres`

## `packrt.ir.revision`

- `apply_defaults`
- `compute`
- `file_hash`
- `revision_files`

## `packrt.ir.semantic`

- `Graph`
- `STATE_REF`
- `state_type`

## `packrt.ir.strict_yaml`

- `StrictYAMLError`
- `load`

## `packrt.ir.yaml_write`

- `WriteError`
- `rewrite`

## `packrt.run`

- `backends`
- `templates`

## `packrt.run.backends`

- `BackendError`
- `FlatLocal`
- `PgVector`
- `PostgresTable`
- `SqliteFile`
- `match_filter`

## `packrt.run.embedding`

- `FAKE_DIM`
- `fake_embedding`

## `packrt.run.engine`

- `NodeError`
- `ReviewStop`
- `Runtime`

## `packrt.run.providers`

- `HttpProvider`
- `ProviderError`

## `packrt.run.resources`

- `PgTableResource`
- `ResourceError`
- `TableResource`

## `packrt.run.templates`

- `REF`
- `render`

## `packrt.trace`

- `Emitter`
- `LineSink`
- `NullSink`
- `TraceFailure`
- `canonical`
- `data_release_id`
- `model_release_id`
- `new_span_id`
- `release_line`

## `tracewriter`

- `__all__`
- `EVENT_SCHEMA`
- `FEEDBACK_CONTRACT`
- `LABELS`
- `LABEL_LIST_VERSION`
- `LineRelay`
- `OK_LABELS`
- `REDACTION_POLICY_VERSION`
- `RunSink`
- `TraceWriteError`
- `TraceWriter`
- `check_feedback`
- `new_feedback_id`
- `new_run_id`
- `new_trace_id`
- `redact`
- `redact_text`
- `sqlite_registration_check`

## `tracewriter.redact`

- `KEY_PATTERNS`
- `KEY_RE`
- `REDACTED`
