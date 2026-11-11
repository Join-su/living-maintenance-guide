"""packrt 실행 어댑터. 실행·trace 이벤트(seq·span·원문 참조·artifact_written)는 packrt `Runtime.run(input, context=, sink=,
review=, on_artifact=)`이 한다(trace 계약 7절, llmops 015·015a). 이 파일은 그 결과를 API 응답에 쓸 모양으로 바꾸기만 한다.

- sink는 공유 기록기의 RunSink를 그대로 넘기되, 응답에 필요한 두 가지(지나간 node, 검토 기록)를 보려고 얇게 감싼다(내용은 바꾸지 않음)
- 보고서 파일은 서버가 on_artifact 콜백으로 쓴다. 콜백이 실패하면 packrt가 실행 오류 artifact_failed로 끝낸다"""
import packrt
from packrt import trace

AUTO_NOTE = "자동 승인(사람 검토 아님)"   # packrt가 reason을 주지 않을 때만 씀(015c부터 packrt가 자동 승인·자동 escalate 문구를 reason에 넣음)


def model_nodes(doc):
    """Pack 문서의 predict node가 쓰는 모델 이름 → node id 목록(모든 graph, 겹침 0). 모델 다름 거부 글의 '어느 node'(D-373, 읽기만)."""
    out = {}
    for g in (doc.get("graphs") or {}).values():
        for n in g.get("nodes") or []:
            name = (n.get("params") or {}).get("model")
            if str(n.get("kind", "")).split("@")[0] == "predict" and name:
                out.setdefault(str(name), set()).add(str(n.get("id")))
    return {k: sorted(v) for k, v in out.items()}


class _Watch:
    """TraceSink를 감싸 지나간 node(node_ended done)와 마지막 검토 기록(review_recorded)을 본다. 호출은 그대로 넘긴다."""

    def __init__(self, sink):
        self.sink = sink
        self.visited, self.review = [], None

    def put_payload(self, value, *, role, retention_class):
        return self.sink.put_payload(value, role=role, retention_class=retention_class)

    def emit(self, event):
        self.sink.emit(event)   # 기록이 실패하면 여기서 예외 → packrt가 실행을 오류로 끝냄. 아래는 기록된 이벤트만 본다
        t, body = event.get("type"), event.get("body") or {}
        if t == "node_ended" and body.get("status") == "done":
            self.visited.append(event.get("node_id"))
        elif t == "review_recorded":
            self.review = {"node_id": event.get("node_id"), "review_id": body.get("review_id"), "action": body.get("action"),
                           "actor_kind": body.get("actor_kind"), "reason": body.get("reason")}


class PackRunner:
    def __init__(self, runtime, *, review_policy="auto_approve", outcome_by_node=None, refusal=None):
        self.rt = runtime
        self.refusal = refusal or (lambda: None)   # 등록 대조가 남긴 마지막 거부 까닭(D-373, llmops_pg일 때만 값)
        self.review_policy = review_policy
        self.outcome_by_node = dict(outcome_by_node or {})
        self.pack_id = runtime.doc["pack"]["id"]
        self.runtime_version = packrt.__version__
        stores = {k: v.get("release_id") for k, v in runtime.bindings["stores"].items()}
        self.data_release_id = trace.data_release_id(stores)   # packrt 공용 함수 하나(015a 2) — run_started·deploy-bootstrap과 같은 값

    def check_input(self, value):
        """입력 schema 검사(실행 전). 틀리면 ValueError — 키를 쓰지 않고 422로 돌려주려고 run 전에 한 번 본다."""
        self.rt.start_state(value)

    def run(self, input_value, *, context, sink, on_artifact):
        """on_artifact(node_path, format, text, review=) -> {location, sha256, size, ...}: 서버가 보고서 파일을 쓴다.
        돌려줌: {status, outcome, output, error, run_id, state, artifact, review}. error = {code, message, where, retryable, details, kind}"""
        watch = _Watch(sink)
        made = {}

        def write(node_path, fmt, text):
            made["artifact"] = on_artifact(node_path, fmt, text, review=self._review(watch))
            return made["artifact"]

        res = self.rt.run(input_value, context=context, sink=watch, review={"policy": self.review_policy}, on_artifact=write)
        detail = res.get("detail") or {}
        base = {"run_id": context["run_id"], "state": detail.get("state"), "artifact": None, "review": None, "output": None}
        if res["status"] != "succeeded":
            err = _error(res.get("error") or {}, detail)
            if err["code"] == "deployment_not_registered":   # 대조한 세 칸을 오류에 붙인다(API 계약 4절)
                err["details"] = {"deployment_id": context.get("deployment_id"), "pack_revision": self.rt.revision, "data_release_id": self.data_release_id,
                                  **(self.refusal() or {})}   # + reason(+ 모델 다름이면 모델 칸, D-373)
            return {**base, "status": "failed", "outcome": None, "error": err}
        outcome = res.get("outcome") or "completed"
        if outcome == "completed":   # escalate도 보고서를 만들므로 completed(015c). no_issue 같은 업무 결과는 설정이 node로 정함
            for name in watch.visited:
                outcome = self.outcome_by_node.get(name, outcome)
        artifact = made.get("artifact")
        return {**base, "status": "succeeded", "outcome": outcome, "output": res.get("output"), "error": None,
                "artifact": artifact, "review": self._review(watch)}

    def _review(self, watch):
        r = watch.review
        if not r:
            return None
        out = {"node_path": f"{self.rt.entry}.{r['node_id']}", "review_id": r["review_id"], "action": r["action"], "actor_kind": r["actor_kind"]}
        if r["actor_kind"] == "auto":
            out["note"] = r.get("reason") or AUTO_NOTE
        return out


def _error(err, detail):
    """packrt 결과의 error·detail.stage → API 오류 칸. kind: trace(기록 실패) · node(node 실패) · artifact · internal."""
    stage = detail.get("stage")
    code, msg = err.get("code") or "run_failed", err.get("message") or ""
    if stage == "trace":
        return {"code": code, "message": msg, "where": "trace", "retryable": code == "trace_unavailable", "details": {}, "kind": "trace"}
    if stage == "artifact":
        return {"code": "artifact_failed", "message": msg, "where": None, "retryable": False, "details": {}, "kind": "artifact"}
    if stage == "run":
        ne = detail.get("node_error") or {}
        return {"code": "run_failed", "message": msg or ne.get("message", ""), "where": ne.get("node_path"), "retryable": bool(ne.get("retryable")),
                "details": {"node_error": ne} if ne else {"error": err}, "kind": "node"}
    return {"code": "run_failed", "message": msg, "where": None, "retryable": False, "details": {"stage": stage, "error": err}, "kind": "internal"}
