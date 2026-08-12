"""walking-skeleton REQ-1.1 (S11, amended): GET /api/health returns 200 ok + build version.

Shape-only assert on `version` — a stray gitignored stamp file on a dev machine
must not redden the suite (version-display spec, review gate R-3); exact values
are covered in test_version.py via an injected path.
"""


def test_health_ok_with_build_version(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str)
    assert body["version"]
