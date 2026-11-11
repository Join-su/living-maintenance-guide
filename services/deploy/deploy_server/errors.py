"""오류 공통 형식(API 계약 4절): {"error": {code, message, where, retryable, run_id, details}}."""


class ApiError(Exception):
    def __init__(self, status, code, message, where=None, retryable=False, run_id=None, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.where = status, code, message, where
        self.retryable, self.run_id, self.details = retryable, run_id, details or {}

    def body(self):
        return {"error": {"code": self.code, "message": self.message, "where": self.where, "retryable": self.retryable,
                          "run_id": self.run_id, "details": self.details}}


# trace 계약 8절 기록기 오류 → HTTP 상태
TRACE_STATUS = {"trace_unavailable": 503}
TRACE_RETRYABLE = {"trace_unavailable"}
