-- 0012 trace.feedback 판 2 — 사람 체크 기록 문서 한 장(D-408 · D-412, deploy 보고 202 · 206). 초안: deploy · 정본 자리: llmops db/migrations.
-- 판 1 줄(사람 피드백 approve · edit · escalate · reject)은 kind = 'feedback'으로 그대로이고 규칙도 그대로(kind별 CHECK로 옮김 — packrt O-3 · O-4).
-- 칸 넷(사용자 승인 D-412 결정 2): kind · record_key · session_id · signals. 문서 원문 참조는 새 칸 대신 edited_value_ref를 kind별로 씀(O-1 — 기록기 반환 이름은 record_ref).
-- 판 2 줄(kind = 'check_record'): 앱 문서 한 장 = 한 줄. 문서 원문은 판 1 edited_value와 같은 방법(가림 → 정규화 JSON → sha256 파일)으로 두고
-- edited_value_ref에 그 참조, trace.payload_ref 한 줄(role check_record)을 같은 트랜잭션에 씀.
-- 206-l 맞춤 표(llmops-2 보고 028 · 기록기 tracewriter 941149f): 판 2 줄 label_list_version = NULL · record_key · session_id 꼴 = 기록기 KEY_RE ·
-- trace_writer가 같은 키 찾기에서 record_key · at을 읽음.
ALTER TABLE trace.feedback
  ADD COLUMN kind text NOT NULL DEFAULT 'feedback' CONSTRAINT feedback_kind_check CHECK (kind IN ('feedback', 'check_record')),
  ADD COLUMN record_key text,
  ADD COLUMN session_id text,
  ADD COLUMN signals jsonb;
ALTER TABLE trace.feedback ALTER COLUMN action DROP NOT NULL, ALTER COLUMN reason DROP NOT NULL, ALTER COLUMN label_list_version DROP NOT NULL;
ALTER TABLE trace.feedback DROP CONSTRAINT feedback_check, DROP CONSTRAINT feedback_check1, DROP CONSTRAINT feedback_check2;
ALTER TABLE trace.feedback
  ADD CONSTRAINT feedback_review_shape CHECK (kind <> 'feedback' OR (
        action IS NOT NULL AND reason IS NOT NULL AND label_list_version IS NOT NULL
        AND (action = 'edit') = (edited_value_ref IS NOT NULL)
        AND (edited_fields IS NULL) = (edited_value_ref IS NULL)
        AND (action = 'approve' OR label IS NOT NULL)
        AND record_key IS NULL AND session_id IS NULL AND signals IS NULL)),
  ADD CONSTRAINT feedback_check_record_shape CHECK (kind <> 'check_record' OR (
        action IS NULL AND label IS NULL AND label_list_version IS NULL AND reason IS NULL AND edited_fields IS NULL AND review_node IS NULL AND node IS NULL
        AND edited_value_ref IS NOT NULL
        AND record_key IS NOT NULL AND record_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'          -- IS NOT NULL을 늘 함께: NULL이면 CHECK가 통과로 보임(예행에서 잡음)
        AND session_id IS NOT NULL AND session_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
        AND signals IS NOT NULL AND jsonb_typeof(signals) = 'array' AND jsonb_array_length(signals) <= 20));   -- 신호 id 문자열 목록(O-6 — 대상 Run은 문서 안)
CREATE UNIQUE INDEX feedback_check_record_key ON trace.feedback (record_key) WHERE kind = 'check_record';   -- record_key 하나에 줄 하나(O-2)
GRANT SELECT (record_key, at) ON trace.feedback TO trace_writer;                                             -- 같은 키 대조 · 저장된 at 돌려줌(O-5 · deploy O-2 청: feedback_id · body_sha256은 0009)
COMMENT ON COLUMN trace.feedback.edited_value_ref IS 'feedback: 고친 값 원문 참조 · check_record: 체크 기록 문서 원문 참조(sha256:<hex>, payload_ref role check_record)';
