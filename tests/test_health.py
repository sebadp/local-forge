from unittest.mock import AsyncMock, MagicMock


def test_health_liveness(client):
    """Liveness probe always returns 200 ok — no dependency checks."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_ready_all_ok(client):
    """Readiness returns 200 when DB and Ollama are healthy."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    client.app.state.ollama_client._http.get = AsyncMock(return_value=mock_response)
    client.app.state.repository.ping = AsyncMock(return_value=True)

    resp = client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["checks"]["db"] == "ok"
    assert data["checks"]["ollama"] == "ok"


def test_ready_ollama_down(client):
    """Readiness returns 503 when Ollama is unreachable."""
    import httpx

    client.app.state.ollama_client._http.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    client.app.state.repository.ping = AsyncMock(return_value=True)

    resp = client.get("/ready")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["checks"]["db"] == "ok"
    assert "error" in data["checks"]["ollama"]


def test_ready_db_down(client):
    """Readiness returns 503 when DB is unreachable."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    client.app.state.ollama_client._http.get = AsyncMock(return_value=mock_response)
    client.app.state.repository.ping = AsyncMock(side_effect=Exception("db locked"))

    resp = client.get("/ready")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "degraded"
    assert "error" in data["checks"]["db"]
    assert data["checks"]["ollama"] == "ok"


def test_ready_both_down(client):
    """Readiness returns 503 when both DB and Ollama are down."""
    import httpx

    client.app.state.ollama_client._http.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    client.app.state.repository.ping = AsyncMock(side_effect=Exception("db error"))

    resp = client.get("/ready")
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "degraded"
    assert "error" in data["checks"]["db"]
    assert "error" in data["checks"]["ollama"]
