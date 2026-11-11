-- 0009_trace_feedback.sql — D-182: 사람 피드백(프론트 → 배포단 POST /runs/{id}/feedback → tracewriter record_feedback). 더하기만.
-- EV-040 CHG-200: deploy A-7 E3 3절 DDL 초안(답장 052-01 승인) 그대로, 번호만 llmops의 다음 빈 번호 0009(052-01 Q7). 칸은 tracewriter 0.1.3
-- record_feedback 행 18칸 + body_sha256 + recorded_at(DEFAULT). db_owner가 dbadmin migrate로, 공유 DB는 사용자 승인 뒤에만.
-- 이벤트 계약 2(trace.event)는 그대로. 이 표는 실행이 끝난 뒤의 기록이라 trace.event에 넣지 않는다(Q-L1).
CREATE TABLE trace.feedback (
  feedback_id        text PRIMARY KEY CHECK (feedback_id ~ '^fb_[0-9A-HJKMNP-TV-Z]{26}$'),
  run_id             text NOT NULL REFERENCES trace.run(run_id),
  contract           integer NOT NULL,                 -- 피드백 기록 계약 판(1)
  source             text NOT NULL CHECK (source IN ('deploy_api')),
  review_node        text,                             -- 그 실행이 지나간 검토 node(no_issue 등은 null)
  node               text,                             -- 라벨이 가리키는 node(null = 실행 전체)
  action             text NOT NULL CHECK (action IN ('approve', 'edit', 'escalate', 'reject')),
  label              text,                             -- labels-v1 값(규칙은 코드 — 목록 판이 바뀌어도 DDL 그대로)
  label_list_version text NOT NULL,
  actor              text NOT NULL CHECK (actor ~ '^[a-z][a-z0-9_-]{1,31}$' AND actor !~ '^[0-9]+$'),
  actor_kind         text NOT NULL CHECK (actor_kind = 'human'),
  role               text,                             -- Q2: v1은 null만
  reason             text NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 500),
  edited_fields      jsonb,                            -- ["answer.cause_candidates.0.type", …]
  edited_value_ref   text,                             -- sha256:<hex> — 원문은 payload 파일(redact-1), trace.payload_ref 줄 같이
  redaction_policy_version text NOT NULL,
  retention_class    text NOT NULL CHECK (retention_class IN ('standard', 'dev')),
  at                 timestamptz NOT NULL,             -- 배포단이 받은 시각(엇갈리면 가장 늦은 것, Q-L3)
  recorded_at        timestamptz NOT NULL DEFAULT now(),
  body_sha256        text NOT NULL,                    -- 같은 feedback_id 재요청 대조
  CHECK ((action = 'edit') = (edited_value_ref IS NOT NULL)),
  CHECK ((edited_fields IS NULL) = (edited_value_ref IS NULL)),
  CHECK (action = 'approve' OR label IS NOT NULL)
);
CREATE INDEX feedback_run_at ON trace.feedback (run_id, at);
-- 쓰기 계정: 같은 키 대조용 두 칸만 읽음(trace.event와 같은 방식). INSERT는 0003의 기본 권한이 주지만 명시해 둠.
GRANT INSERT ON trace.feedback TO trace_writer;
GRANT SELECT (feedback_id, body_sha256) ON trace.feedback TO trace_writer;
-- 읽기 계정: 0004 기본 권한과 같음(명시).
GRANT SELECT ON trace.feedback TO llmops_app;
