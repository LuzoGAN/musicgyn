"""Testes fase 2b: importação de faixas do link + limite p/ tempo de aula."""
import pytest

from importers import (import_playlist_tracks, import_spotify_playlist,
                       import_youtube_playlist, suggest_import_limit)
from models import PlaylistTrack, db
from tests.conftest import login


# ---------------- limite inteligente

def test_limite_aula_crossfit():
    # 60 min, 3 base + 1 pedido → base cobre 75% → ~13 faixas
    assert suggest_import_limit(60, 3) == 13


def test_limite_minimo_e_maximo():
    assert suggest_import_limit(30, 3) == 10  # pouco tempo → piso de 10
    assert suggest_import_limit(240, 4) == 20  # aula longa → teto de 20


def test_limite_so_pedidos_e_zero():
    assert suggest_import_limit(60, 0) == 0


# ---------------- YouTube (rede simulada)

FAKE_HTML = """
{"videoId":"dQw4w9WgXcQ","title":"Musica 1"}
{"videoId":"hTWKbfoikeg","title":"Musica 2"}
{"videoId":"dQw4w9WgXcQ","title":"repetida"}
"""


def test_youtube_via_pagina(monkeypatch):
    import importers
    monkeypatch.setattr(importers, "_http_get", lambda url, headers=None, timeout=15: FAKE_HTML)
    tracks = import_youtube_playlist("https://www.youtube.com/playlist?list=PL123", 10)
    assert [t["embed_id"] for t in tracks] == ["dQw4w9WgXcQ", "hTWKbfoikeg"]  # sem duplicar
    assert tracks[0]["platform_url"] == "https://youtu.be/dQw4w9WgXcQ"


def test_youtube_pagina_vazia_da_erro_amigavel(monkeypatch):
    import importers
    monkeypatch.setattr(importers, "_http_get", lambda *a, **k: "<html>nada</html>")
    with pytest.raises(ValueError, match="YOUTUBE_API_KEY"):
        import_youtube_playlist("https://www.youtube.com/playlist?list=PL123", 10)


def test_youtube_link_invalido():
    with pytest.raises(ValueError, match="inválido"):
        import_youtube_playlist("https://exemplo.com/nada", 10)


# ---------------- Spotify

def test_spotify_sem_credenciais_da_erro_explicativo():
    with pytest.raises(ValueError, match="SPOTIFY_CLIENT_ID"):
        import_spotify_playlist("https://open.spotify.com/playlist/ABC123", 10)


def test_spotify_com_credenciais(monkeypatch):
    import importers, json
    token = json.dumps({"access_token": "tok"})
    page = json.dumps({"items": [
        {"track": {"id": "T1", "name": "Song Um",
                   "artists": [{"name": "Art A"}],
                   "duration_ms": 200000,
                   "external_urls": {"spotify": "https://open.spotify.com/track/T1"}}},
        {"track": {"id": "T2", "name": "Song Dois", "artists": [],
                   "duration_ms": 0, "external_urls": {}}},
    ]})
    monkeypatch.setattr(importers, "_http_post", lambda *a, **k: token)
    monkeypatch.setattr(importers, "_http_get", lambda *a, **k: page)
    tracks = import_spotify_playlist("https://open.spotify.com/playlist/ABC123",
                                     10, "id", "secret")
    assert len(tracks) == 2
    assert tracks[0]["artist"] == "Art A"
    assert tracks[0]["duration_sec"] == 200
    assert tracks[0]["embed_id"] == "track/T1"
    assert tracks[1]["duration_sec"] is None


def test_dispatcher_plataforma_sem_suporte():
    with pytest.raises(ValueError, match="não suportada"):
        import_playlist_tracks("deezer", "https://www.deezer.com/playlist/123", 10)


# ---------------- rota web

def test_rota_importa_e_respeita_limite(client, gym, playlist, monkeypatch):
    import importers
    fake = [{"title": f"Imp{i}", "artist": None,
             "platform_url": f"https://youtu.be/imp{i:02d}ABC_-",
             "embed_id": f"imp{i:02d}ABC_-", "duration_sec": 200}
            for i in range(30)]
    monkeypatch.setattr(importers, "import_playlist_tracks", lambda *a, **k: fake[:13])
    login(client)
    resp = client.post(f"/dashboard/playlist/{playlist.id}/tracks/import", data={
        "target_minutes": "60", "base_per_request": "3",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert PlaylistTrack.query.filter_by(playlist_id=playlist.id).count() == 13
    assert "importadas" in resp.get_data(as_text=True).lower()


def test_rota_nao_duplica_na_segunda_importacao(client, gym, playlist, monkeypatch):
    import importers
    fake = [{"title": "Mesma", "artist": None,
             "platform_url": "https://youtu.be/mesmaAB_-",
             "embed_id": "mesmaAB_-", "duration_sec": 200}]
    monkeypatch.setattr(importers, "import_playlist_tracks", lambda *a, **k: fake)
    login(client)
    client.post(f"/dashboard/playlist/{playlist.id}/tracks/import",
                data={}, follow_redirects=True)
    resp = client.post(f"/dashboard/playlist/{playlist.id}/tracks/import",
                       data={}, follow_redirects=True)
    assert PlaylistTrack.query.filter_by(playlist_id=playlist.id).count() == 1
    assert "já estavam" in resp.get_data(as_text=True).lower()


def test_rota_erro_vira_flash(client, gym, playlist, monkeypatch):
    import importers

    def boom(*a, **k):
        raise ValueError("Spotify exige SPOTIFY_CLIENT_ID")
    monkeypatch.setattr(importers, "import_playlist_tracks", boom)
    login(client)
    resp = client.post(f"/dashboard/playlist/{playlist.id}/tracks/import",
                       data={}, follow_redirects=True)
    assert "SPOTIFY_CLIENT_ID" in resp.get_data(as_text=True)
    assert PlaylistTrack.query.filter_by(playlist_id=playlist.id).count() == 0
