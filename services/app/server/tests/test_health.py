from fastapi.testclient import TestClient

from c1a15.api.main import app


def test_health_echoes_app_header():
    client = TestClient(app)
    assert client.get("/api/health", headers={"X-App": "tech"}).json() == {"status": "ok", "app": "tech"}
    assert client.get("/api/health").json() == {"status": "ok", "app": None}
