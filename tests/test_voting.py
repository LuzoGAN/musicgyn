"""Testes de integração: votação web + moderação + encerramento."""
from datetime import datetime, timedelta

from models import Gym, Playlist, SessionPlaylist, Vote, VoteSession, db
from tests.conftest import login


def test_pagina_publica(client, gym, playlist):
    resp = client.get(f"/gym/{gym.qr_code_token}")
    assert resp.status_code == 200
    assert gym.name in resp.get_data(as_text=True)


def test_pagina_publica_token_invalido(client):
    assert client.get("/gym/token-inexistente").status_code == 404


def test_voto_web_ok(client, gym, session_with_playlist, playlist):
    resp = client.post(f"/gym/{gym.qr_code_token}/vote", data={
        "session_id": session_with_playlist.id,
        "playlist_id": playlist.id,
        "user_name": "Aluno 1",
        "user_email": "aluno1@email.com",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert Vote.query.count() == 1
    assert db.session.get(Playlist, playlist.id).vote_count == 1


def test_voto_duplo_bloqueado(client, gym, session_with_playlist, playlist):
    data = {"session_id": session_with_playlist.id, "playlist_id": playlist.id,
            "user_email": "repetido@email.com"}
    client.post(f"/gym/{gym.qr_code_token}/vote", data=data, follow_redirects=True)
    resp = client.post(f"/gym/{gym.qr_code_token}/vote", data=data, follow_redirects=True)
    assert "já votou" in resp.get_data(as_text=True).lower()
    assert Vote.query.count() == 1


def test_voto_playlist_fora_da_sessao(client, gym, session_with_playlist):
    outra = Playlist(gym_id=gym.id, name="Fora", platform="youtube",
                     platform_url="https://youtu.be/xyz123ABC_-",
                     embed_id="xyz123ABC_-", approved=True)
    db.session.add(outra)
    db.session.commit()
    resp = client.post(f"/gym/{gym.qr_code_token}/vote", data={
        "session_id": session_with_playlist.id, "playlist_id": outra.id,
        "user_email": "a@a.com"}, follow_redirects=True)
    assert "não faz parte" in resp.get_data(as_text=True).lower()
    assert Vote.query.count() == 0


def test_sugestao_entra_em_moderacao(client, gym):
    resp = client.post(f"/gym/{gym.qr_code_token}/suggest", data={
        "name": "Sugestão Aluno", "platform": "spotify",
        "platform_url": "https://open.spotify.com/playlist/ABC123xyz",
        "suggested_by": "Aluno"}, follow_redirects=True)
    assert resp.status_code == 200
    p = Playlist.query.filter_by(name="Sugestão Aluno").first()
    assert p is not None
    assert p.approved is False  # precisa de aprovação do admin


def test_sugestao_url_invalida(client, gym):
    resp = client.post(f"/gym/{gym.qr_code_token}/suggest", data={
        "name": "Ruim", "platform": "youtube",
        "platform_url": "https://exemplo.com/nada"}, follow_redirects=True)
    assert "inválida" in resp.get_data(as_text=True).lower()
    assert Playlist.query.filter_by(name="Ruim").first() is None


def test_fluxo_admin_aprova_e_encerra(client, gym, session_with_playlist, playlist):
    login(client)
    # cria sugestão pendente via aluno
    client.post(f"/gym/{gym.qr_code_token}/suggest", data={
        "name": "Pendente", "platform": "youtube",
        "platform_url": "https://youtu.be/pendente12_",
        "suggested_by": "Aluno"}, follow_redirects=True)
    pend = Playlist.query.filter_by(name="Pendente").first()
    assert pend.approved is False
    # admin aprova
    resp = client.get(f"/dashboard/playlist/{pend.id}/approve", follow_redirects=True)
    assert resp.status_code == 200
    assert db.session.get(Playlist, pend.id).approved is True
    # 2 votos na playlist principal
    for email in ("v1@a.com", "v2@a.com"):
        client.post(f"/gym/{gym.qr_code_token}/vote", data={
            "session_id": session_with_playlist.id, "playlist_id": playlist.id,
            "user_email": email}, follow_redirects=True)
    # encerra
    resp = client.get(f"/dashboard/session/{session_with_playlist.id}/close",
                      follow_redirects=True)
    assert "Vencedora" in resp.get_data(as_text=True)
    vs = db.session.get(VoteSession, session_with_playlist.id)
    assert vs.status == "finished"
    assert vs.winning_playlist_id == playlist.id
    assert db.session.get(Playlist, playlist.id).times_played == 1


def test_criar_sessao(client, gym, playlist):
    login(client)
    resp = client.post("/dashboard/session/new", data={
        "title": "WOD Quarta", "class_date": "2030-01-01T18:00",
        "voting_hours": "24", "playlists": [str(playlist.id)],
    }, follow_redirects=True)
    assert resp.status_code == 200
    vs = VoteSession.query.filter_by(title="WOD Quarta").first()
    assert vs is not None
    assert len(vs.available_playlists) == 1


def test_personalizacao_cores(client, gym):
    login(client)
    resp = client.post("/dashboard/settings", data={
        "primary_color": "#00ff00", "secondary_color": "#000000",
        "accent_color": "#ffffff", "logo_url": "",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert db.session.get(Gym, gym.id).primary_color == "#00ff00"
    # cor inválida é ignorada, não quebra
    client.post("/dashboard/settings", data={"primary_color": "azul"},
                follow_redirects=True)
    assert db.session.get(Gym, gym.id).primary_color == "#00ff00"
