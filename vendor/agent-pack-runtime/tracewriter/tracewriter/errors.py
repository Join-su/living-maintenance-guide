"""TraceWriteError (trace contract 8): every refusal or failure of the writer. `code` is one of deployment_not_registered,
unknown_evaluation_run, trace_schema, trace_conflict, trace_unavailable, payload_failed, payload_too_large."""


class TraceWriteError(RuntimeError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")
