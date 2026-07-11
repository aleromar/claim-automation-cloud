"""REQ-1.1: GET /api/health returns 200 {"status": "ok"}."""


def test_health_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
