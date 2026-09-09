"""Testes fase 3: limite por aluno, pular democrático, Modo TV, notificações."""
from models import ClassMix, Notification, SkipVote, SongSuggestion, db
from tests.conftest import login


def _pedir(client, token, nome, titulo, session_id=None):
    data = {"title": titulo, "platform": "youtube",
            "platform_url": f"https://youtu.be/{titulo[:6].ljust(6, 'x')}AB_-",
            "suggested_by": nome}
    if session_id:
        data["session_id"] = str(session_id)
    return client.post(f"/gym/{token}/suggest-song", data=data,
                       follow_redirects=True)


def _mix_com_faixas(client, gym, playlist, session, n_base=2):
    from models import PlaylistTrack
    for i in range(n_base):
        db.session.add(PlaylistTrack(
            playlist_id=playlist.id, gym_id=gym.id, title=f"Base{i}",
            platform="youtube", platform_url=f"https://youtu.be/base{i:02d}AB_-",
            embed_id=f"base{i:02d}AB_-", duration_sec=180))
    db.session.commit()
    client.post("/dashboard/mix/new", data={
        "title": "Mix Eng", "session_id": str(session.id),
        "base_playlist_id": str(playlist.id),
        "target_minutes": "15", "base_per_request": "3",
    }, follow_redirects=True)
    return ClassMix.query.filter_by(title="Mix Eng").first()


# ---------------- limite por aluno

def test_limite_bloqueia_4o_pedido(client, gym, session_with_playlist):
    token = gym.qr_code_token
    assert gym.max_requests_per_session == 3
    for i in range(3):
        _pedir(client, token, "Ana", f"Mus{i:02d}", session_with_playlist.id)
    assert SongSuggestion.query.filter_by(session_id=session_with_playlist.id).count() == 3
    resp = _pedir(client, token, "ANA  ", "Mus03", session_with_playlist.id)  # case-insensitive
    assert "Limite" in resp.get_data(as_text=True)
    assert SongSuggestion.query.filter_by(session_id=session_with_playlist.id).count() == 3


def test_limite_outro_aluno_e_outra_aula_ok(client, gym, session_with_playlist):
    token = gym.qr_code_token
    for i in range(3):
        _pedir(client, token, "Ana", f"A{i:02d}", session_with_playlist.id)
    resp = _pedir(client, token, "Beto", "B00", session_with_playlist.id)
    assert "sorteio justo" in resp.get_data(as_text=True).lower()
    # sem aula (pedido geral) tem balde próprio
    resp = _pedir(client, token, "Ana", "Geral1")
    assert SongSuggestion.query.filter_by(title="Geral1").first() is not None


def test_limite_configuravel_e_api_429(client, gym, session_with_playlist):
    gym.max_requests_per_session = 1
    db.session.commit()
    login(client)
    token = gym.qr_code_token
    _pedir(client, token, "Ana", "M1x", session_with_playlist.id)
    resp = _pedir(client, token, "Ana", "M2x", session_with_playlist.id)
    assert "Limite" in resp.get_data(as_text=True)

    api = f"/api/v1/gym/{token}/request-song"
    r1 = client.post(api, json={"title": "Z1", "platform": "youtube",
                                "platform_url": "https://youtu.be/z1z1z1z1z1_",
                                "suggested_by": "Zezinho",
                                "session_id": session_with_playlist.id})
    assert r1.status_code == 201
    r2 = client.post(api, json={"title": "Z2", "platform": "youtube",
                                "platform_url": "https://youtu.be/z2z2z2z2z2_",
                                "suggested_by": "Zezinho",
                                "session_id": session_with_playlist.id})
    assert r2.status_code == 429


# ---------------- pular democrático

def test_skip_web_pula_na_meta(client, gym, playlist, session_with_playlist):
    gym.skip_votes_needed = 2
    db.session.commit()
    login(client)
    mix = _mix_com_faixas(client, gym, playlist, session_with_playlist)
    assert mix.current_index == 0
    token = gym.qr_code_token
    r1 = client.post(f"/gym/{token}/skip", data={
        "mix_id": mix.id, "user_name": "A", "user_email": "a@a.com"},
        follow_redirects=True)
    assert "(1/2)" in r1.get_data(as_text=True)
    assert db.session.get(ClassMix, mix.id).current_index == 0
    # voto duplicado da mesma pessoa não conta
    client.post(f"/gym/{token}/skip", data={
        "mix_id": mix.id, "user_name": "A", "user_email": "a@a.com"},
        follow_redirects=True)
    assert SkipVote.query.filter_by(mix_id=mix.id).count() == 1
    r2 = client.post(f"/gym/{token}/skip", data={
        "mix_id": mix.id, "user_name": "B", "user_email": "b@b.com"},
        follow_redirects=True)
    assert "Pularam" in r2.get_data(as_text=True)
    assert db.session.get(ClassMix, mix.id).current_index == 1


def test_skip_api(client, gym, playlist, session_with_playlist):
    gym.skip_votes_needed = 2
    db.session.commit()
    login(client)
    mix = _mix_com_faixas(client, gym, playlist, session_with_playlist)
    url = f"/api/v1/gym/{gym.qr_code_token}/mix/{mix.id}/skip"
    r1 = client.post(url, json={"user_identifier": "dev-1"})
    assert r1.get_json() == {"success": True, "skipped": False,
                             "votes": 1, "needed": 2}
    r2 = client.post(url, json={"user_identifier": "dev-1"})
    assert r2.status_code == 400
    r3 = client.post(url, json={"user_identifier": "dev-2"})
    assert r3.get_json()["skipped"] is True
    assert db.session.get(ClassMix, mix.id).current_index == 1


def test_skip_api_fila_inclui_placar(client, gym, playlist, session_with_playlist):
    login(client)
    mix = _mix_com_faixas(client, gym, playlist, session_with_playlist)
    client.post(f"/api/v1/gym/{gym.qr_code_token}/mix/{mix.id}/skip",
                json={"user_identifier": "dev-9"})
    q = client.get(f"/api/v1/gym/{gym.qr_code_token}"
                   f"/session/{session_with_playlist.id}/queue").get_json()["queue"]
    assert q["skip_votes"] == 1
    assert q["skip_needed"] == (gym.skip_votes_needed or 5)


# ---------------- Modo TV

def test_tv_ok_e_idle(client, gym):
    resp = client.get(f"/tv/{gym.qr_code_token}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert gym.name in html and "QR" in html or "qr" in html.lower()


def test_tv_mostra_tocando(client, gym, playlist, session_with_playlist):
    login(client)
    mix = _mix_com_faixas(client, gym, playlist, session_with_playlist)
    resp = client.get(f"/tv/{gym.qr_code_token}")
    html = resp.get_data(as_text=True)
    assert "Tocando agora" in html
    first = mix.items[0]
    assert first.title in html


def test_tv_token_invalido(client):
    assert client.get("/tv/nope").status_code == 404


# ---------------- notificações

def test_nova_sessao_gera_notificacao(client, gym, playlist):
    login(client)
    assert Notification.query.count() == 0
    client.post("/dashboard/session/new", data={
        "title": "WOD Sexta", "class_date": "2030-02-02T18:00",
        "voting_hours": "24", "playlists": [str(playlist.id)],
    }, follow_redirects=True)
    note = Notification.query.first()
    assert note is not None
    assert "WOD Sexta" in note.title
    assert note.session_id is not None


def test_api_notifications_since(client, gym, playlist):
    login(client)
    client.post("/dashboard/session/new", data={
        "title": "WOD A", "class_date": "2030-02-02T18:00",
        "voting_hours": "24", "playlists": [str(playlist.id)],
    }, follow_redirects=True)
    client.post("/dashboard/session/new", data={
        "title": "WOD B", "class_date": "2030-02-03T18:00",
        "voting_hours": "24", "playlists": [str(playlist.id)],
    }, follow_redirects=True)
    all_notes = client.get(
        f"/api/v1/gym/{gym.qr_code_token}/notifications").get_json()["notifications"]
    assert len(all_notes) == 2
    since = client.get(
        f"/api/v1/gym/{gym.qr_code_token}/notifications?since_id={all_notes[0]['id']}"
    ).get_json()["notifications"]
    assert len(since) == 1
    assert since[0]["title"].endswith("WOD B")


def test_banner_na_pagina_do_aluno(client, gym, playlist):
    login(client)
    client.post("/dashboard/session/new", data={
        "title": "WOD Banner", "class_date": "2030-02-02T18:00",
        "voting_hours": "24", "playlists": [str(playlist.id)],
    }, follow_redirects=True)
    html = client.get(f"/gym/{gym.qr_code_token}").get_data(as_text=True)
    assert "WOD Banner" in html
