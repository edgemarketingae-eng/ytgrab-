from fastapi.testclient import TestClient
from app import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_homepage():
    r = client.get("/")
    assert r.status_code == 200
    assert "YTGrab" in r.text


def test_rejects_non_youtube():
    r = client.post("/api/info", json={"url": "https://example.com/video"})
    assert r.status_code == 400


def test_rejects_bad_format():
    r = client.post("/api/download",
                    json={"url": "https://youtu.be/abc", "format": "avi", "quality": "720"})
    assert r.status_code == 400


def test_unknown_job():
    assert client.get("/api/progress/nope").status_code == 404
