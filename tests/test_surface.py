"""The attack surface: developer routes off unless DEBUG=1, headers everywhere, session expiry and caps, rate limit, delete-my-data."""
import dataclasses

import pytest
from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def prod(monkeypatch):
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, debug=False))


def new_session():
    return client.post("/sessions").json()["session_id"]


def test_developer_routes_are_off_without_debug(prod):
    sid = new_session()
    for method, path, kw in (("get", "/docs", {}), ("get", "/openapi.json", {}), ("get", "/redoc", {}), ("get", "/samples", {}), ("post", "/assess", {"json": {"sample_id": "TC07"}}),
                             ("post", f"/sessions/{sid}/claim", {"json": {"sample_id": "TC07"}})):
        r = getattr(client, method)(path, **kw)
        assert r.status_code == 404, (path, r.status_code)


def test_developer_routes_work_with_debug():
    assert client.get("/openapi.json").status_code == 200 and client.get("/samples").status_code == 200
    assert client.post("/assess", json={"sample_id": "TC07"}).status_code == 200 and client.get("/docs").status_code == 200


def test_health_says_only_ok():
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/", "/health", "/nope", "/static/app.js", "/static/nope.js", "/sessions/none/chat"])
def test_security_headers_on_every_route(path):
    r = client.post(path, json={"message": "x"}) if path.endswith("/chat") else client.get(path)
    h = r.headers
    assert h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "no-referrer" and h["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in h["content-security-policy"]
    assert "cache-control" in h


def test_an_idle_session_is_deleted_after_the_ttl():
    sid = new_session()
    assert client.post(f"/sessions/{sid}/chat", json={"message": "hello"}).status_code == 200
    main.SESSIONS[sid]["last_seen"] -= main.settings.session_ttl_s + 5
    r = client.post(f"/sessions/{sid}/chat", json={"message": "hello"})
    assert r.status_code == 404 and "30 minutes" in r.json()["error"]["message"] and sid not in main.SESSIONS


def test_activity_keeps_a_session_alive():
    sid = new_session()
    main.SESSIONS[sid]["last_seen"] -= main.settings.session_ttl_s - 5
    assert client.post(f"/sessions/{sid}/chat", json={"message": "hi"}).status_code == 200
    assert client.post(f"/sessions/{sid}/chat", json={"message": "hi"}).status_code == 200


def test_delete_my_data_removes_everything_and_is_safe_twice():
    sid = new_session()
    client.post(f"/sessions/{sid}/documents/sample")
    assert client.delete(f"/sessions/{sid}").json() == {"deleted": True} and sid not in main.SESSIONS
    assert client.delete(f"/sessions/{sid}").json() == {"deleted": False}
    assert client.post(f"/sessions/{sid}/intake").status_code == 404


def test_a_session_has_a_message_cap(monkeypatch):
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, max_messages_per_session=3))
    sid = new_session()
    client.post(f"/sessions/{sid}/documents/sample")
    client.post(f"/sessions/{sid}/intake")
    codes = [client.post(f"/sessions/{sid}/chat", json={"message": "hello"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_the_per_ip_rate_limit(monkeypatch):
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, rate_limit_per_min=5, chat_rate_limit_per_min=2))
    main._HITS.clear()
    codes = [client.get("/health").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
    r = client.get("/health")
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited" and r.headers["retry-after"] and r.headers["x-content-type-options"] == "nosniff"
    main._HITS.clear()
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, rate_limit_per_min=100, chat_rate_limit_per_min=2))
    sid = new_session()
    client.post(f"/sessions/{sid}/documents/sample")
    client.post(f"/sessions/{sid}/intake")
    assert [client.post(f"/sessions/{sid}/chat", json={"message": "hi"}).status_code for _ in range(3)] == [200, 200, 429]
    main._HITS.clear()
