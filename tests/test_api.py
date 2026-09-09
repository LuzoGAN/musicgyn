"""Testes da API v1 (contrato para iOS/Android)."""

API = "/api/v1/gym/token-teste-123"


def test_health(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_gym_info(client, gym):
    resp = client.get(f"{API}/info")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["name"] == "Box Teste"
    assert data["primary_color"] == "#e50914"


def test_gym_info_404(client):
    assert client.get("/api/v1/gym/nope/info").status_code == 404


def test_active_session(client, gym, session_with_playlist, playlist):
    resp = client.get(f"{API}/active-session")
    assert resp.status_code == 200
    data = resp.get_json()["active_session"]
    assert data["title"] == "WOD Segunda 18h"
    assert len(data["playlists"]) == 1
    assert data["playlists"][0]["name"] == "Rock p/ WOD"


def test_active_session_none(client, gym):
    resp = client.get(f"{API}/active-session")
    assert resp.get_json() == {"active_session": None}


def test_api_vote_ok(client, gym, session_with_playlist, playlist):
    resp = client.post(f"{API}/vote", json={
        "session_id": session_with_playlist.id,
        "playlist_id": playlist.id,
        "user_identifier": "device-uuid-1",
        "user_name": "Aluno App",
    })
    assert resp.status_code == 201
    assert resp.get_json()["success"] is True


def test_api_vote_duplicado(client, gym, session_with_playlist, playlist):
    payload = {"session_id": session_with_playlist.id, "playlist_id": playlist.id,
               "user_identifier": "device-uuid-2"}
    client.post(f"{API}/vote", json=payload)
    resp = client.post(f"{API}/vote", json=payload)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Already voted"


def test_api_vote_payload_incompleto(client, gym):
    resp = client.post(f"{API}/vote", json={"session_id": 1})
    assert resp.status_code == 400


def test_api_suggest(client, gym):
    resp = client.post(f"{API}/suggest", json={
        "name": "Via App", "platform": "spotify",
        "platform_url": "https://open.spotify.com/playlist/XYZ789abc",
        "suggested_by": "Aluno App",
    })
    assert resp.status_code == 201
    assert "playlist_id" in resp.get_json()


def test_api_suggest_url_invalida(client, gym):
    resp = client.post(f"{API}/suggest", json={
        "name": "Ruim", "platform": "youtube",
        "platform_url": "https://errada.com",
    })
    assert resp.status_code == 400


def test_api_results(client, gym, session_with_playlist, playlist):
    client.post(f"{API}/vote", json={
        "session_id": session_with_playlist.id, "playlist_id": playlist.id,
        "user_identifier": "u1"})
    resp = client.get(f"{API}/session/{session_with_playlist.id}/results")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ranking"][0]["votes"] == 1
    assert data["ranking"][0]["playlist"]["id"] == playlist.id


def test_api_playlists(client, gym, playlist):
    resp = client.get(f"{API}/playlists")
    assert resp.status_code == 200
    assert len(resp.get_json()["playlists"]) == 1
