"""Testes fase 2: fila justa + Modo Aula + API de pedidos."""
from mix_logic import build_class_queue, fair_order_requests, queue_summary
from models import ClassMix, PlaylistTrack, SongSuggestion, db
from tests.conftest import login


def _song(title, by):
    return {"title": title, "suggested_by": by, "duration_sec": 180}


# ---------------- lógica pura

def test_fair_alterna_pessoas():
    songs = [_song(f"A{i}", "Ana") for i in range(4)] + [_song(f"B{i}", "Beto") for i in range(4)]
    ordered = fair_order_requests(songs, seed=7)
    assert len(ordered) == 8
    users = [(s["suggested_by"]) for s in ordered]
    # enquanto ambos têm músicas, ninguém repete em sequência
    for a, b in zip(users[:8:2], users[1:8:2]):
        assert a != b
    assert users.count("Ana") == 4 and users.count("Beto") == 4


def test_fair_quem_pediu_muito_nao_domina_inicio():
    songs = [_song(f"A{i}", "Ana") for i in range(10)] + [_song("B0", "Beto")]
    ordered = fair_order_requests(songs, seed=3)
    first_two = {ordered[0]["suggested_by"], ordered[1]["suggested_by"]}
    assert first_two == {"Ana", "Beto"}  # Beto aparece já no começo


def test_fair_um_autor_mantem_tudo():
    songs = [_song(f"A{i}", "Ana") for i in range(3)]
    assert len(fair_order_requests(songs, seed=1)) == 3


def test_intercala_proporcao():
    base = [{"title": f"Base{i}", "duration_sec": 200} for i in range(6)]
    reqs = [_song("R0", "Ana"), _song("R1", "Beto")]
    queue, _ = build_class_queue(base, reqs, base_per_request=3,
                                 target_minutes=60, seed=1)
    kinds = [i["kind"] for i in queue[:4]]
    assert kinds == ["base", "base", "base", "request"]


def test_corta_pela_duracao_da_aula():
    base = [{"title": f"B{i}", "duration_sec": 200} for i in range(30)]
    reqs = [_song(f"R{i}", f"Aluno{i}") for i in range(30)]
    queue, total = build_class_queue(base, reqs, base_per_request=3,
                                     target_minutes=30, seed=5)
    assert total <= 30 * 60 + 200  # não estoura além de 1 faixa
    assert total >= 30 * 60  # enche a aula
    # nem todos os 30 pedidos entraram — o sorteio escolheu
    assert sum(1 for i in queue if i["kind"] == "request") < 30


def test_sem_faixas_usa_blocos_da_playlist():
    reqs = [_song("R0", "Ana"), _song("R1", "Beto")]
    queue, total = build_class_queue([], reqs, base_per_request=3,
                                     target_minutes=60, seed=1)
    kinds = [i["kind"] for i in queue]
    assert kinds[0] == "block"
    assert "request" in kinds
    assert total >= 60 * 60


def test_so_pedidos_quando_proporcao_zero():
    base = [{"title": "B0", "duration_sec": 200}]
    reqs = [_song("R0", "Ana")]
    queue, _ = build_class_queue(base, reqs, base_per_request=0,
                                 target_minutes=60, seed=1)
    assert all(i["kind"] == "request" for i in queue)


def test_vazio_retorna_vazio():
    assert build_class_queue([], [], 3, 60, seed=1) == ([], 0)


def test_resumo():
    q, _ = build_class_queue([{"title": "B", "duration_sec": 200}],
                             [_song("R", "Ana")], 1, 60, seed=1)
    s = queue_summary(q)
    assert s["counts"]["request"] >= 1
    assert s["by_user"].get("ana", 0) >= 1


# ---------------- integração web

def _add_track(client, playlist, title="Faixa X"):
    return client.post(f"/dashboard/playlist/{playlist.id}/tracks/add", data={
        "title": title, "artist": "Artista",
        "platform": "youtube",
        "platform_url": f"https://youtu.be/{title[:6].ljust(6, 'x')}AB_-",
    }, follow_redirects=True)


def test_cadastrar_faixas_na_playlist(client, gym, playlist):
    login(client)
    resp = _add_track(client, playlist, "Thunder")
    assert resp.status_code == 200
    assert PlaylistTrack.query.filter_by(playlist_id=playlist.id).count() == 1


def test_pedido_web_vinculado_a_aula(client, gym, session_with_playlist):
    resp = client.post(f"/gym/{gym.qr_code_token}/suggest-song", data={
        "title": "Enter Sandman", "artist": "Metallica",
        "platform": "youtube",
        "platform_url": "https://youtu.be/hTWKbfoikeg",
        "suggested_by": "Aluno",
        "session_id": str(session_with_playlist.id),
    }, follow_redirects=True)
    assert resp.status_code == 200
    s = SongSuggestion.query.filter_by(title="Enter Sandman").first()
    assert s is not None
    assert s.session_id == session_with_playlist.id
    assert s.approved is True  # pedido entra direto na fila
    # aparece na página da academia
    assert "Enter Sandman" in client.get(f"/gym/{gym.qr_code_token}").get_data(as_text=True)


def test_gerar_mix_e_player(client, gym, playlist, session_with_playlist):
    login(client)
    _add_track(client, playlist, "BaseUm")
    _add_track(client, playlist, "BaseDois")
    for i in range(3):
        db.session.add(SongSuggestion(
            gym_id=gym.id, session_id=session_with_playlist.id,
            title=f"Pedido{i}", platform="youtube",
            platform_url=f"https://youtu.be/ped{i:02d}doAB_-",
            suggested_by="Ana" if i < 2 else "Beto", approved=True))
    db.session.commit()

    resp = client.post("/dashboard/mix/new", data={
        "title": "Mix WOD", "session_id": str(session_with_playlist.id),
        "base_playlist_id": str(playlist.id),
        "target_minutes": "15", "base_per_request": "2",
    }, follow_redirects=True)
    assert resp.status_code == 200
    mix = ClassMix.query.filter_by(title="Mix WOD").first()
    assert mix is not None
    assert len(mix.items) > 3
    kinds = [i.kind for i in mix.items[:3]]
    assert kinds == ["base", "base", "request"]

    # player avança e marca como tocada
    first_id = mix.items[0].id
    client.get(f"/dashboard/mix/{mix.id}/next", follow_redirects=True)
    assert db.session.get(ClassMix, mix.id).current_index == 1
    assert db.session.get(type(mix.items[0]), first_id).played is True
    client.get(f"/dashboard/mix/{mix.id}/prev", follow_redirects=True)
    assert db.session.get(ClassMix, mix.id).current_index == 0


def test_mix_sem_conteudo_avisa(client, gym):
    login(client)
    resp = client.post("/dashboard/mix/new", data={
        "title": "Vazio", "target_minutes": "60", "base_per_request": "3",
    }, follow_redirects=True)
    assert "Sem conte" in resp.get_data(as_text=True)
    assert ClassMix.query.filter_by(title="Vazio").first() is None


# ---------------- API fase 2

def test_api_pedir_musica(client, gym, session_with_playlist):
    resp = client.post("/api/v1/gym/token-teste-123/request-song", json={
        "title": "Song App", "platform": "spotify",
        "platform_url": "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQ",
        "suggested_by": "Aluno App",
        "session_id": session_with_playlist.id,
    })
    assert resp.status_code == 201
    s = SongSuggestion.query.filter_by(title="Song App").first()
    assert s.session_id == session_with_playlist.id


def test_api_musicas_e_fila_da_aula(client, gym, playlist, session_with_playlist):
    login(client)
    _add_track(client, playlist, "BaseApp")
    db.session.add(SongSuggestion(
        gym_id=gym.id, session_id=session_with_playlist.id, title="PedApp",
        platform="youtube", platform_url="https://youtu.be/pedAppAB_-",
        suggested_by="Ana", approved=True))
    db.session.commit()
    client.post("/dashboard/mix/new", data={
        "title": "Mix API", "session_id": str(session_with_playlist.id),
        "base_playlist_id": str(playlist.id),
        "target_minutes": "15", "base_per_request": "2",
    }, follow_redirects=True)

    songs = client.get(
        f"/api/v1/gym/token-teste-123/session/{session_with_playlist.id}/songs")
    assert songs.status_code == 200
    assert any(s["title"] == "PedApp" for s in songs.get_json()["songs"])

    queue = client.get(
        f"/api/v1/gym/token-teste-123/session/{session_with_playlist.id}/queue")
    assert queue.status_code == 200
    data = queue.get_json()["queue"]
    assert data is not None
    assert data["now_playing"] is not None
    assert len(data["items"]) > 0


def test_api_preview_nao_salva(client, gym, playlist):
    n_before = ClassMix.query.count()
    resp = client.post("/api/v1/gym/token-teste-123/mix/preview", json={
        "base_playlist_id": playlist.id, "target_minutes": 30,
        "base_per_request": 3, "seed": 42,
    })
    assert resp.status_code == 200
    assert "queue" in resp.get_json()
    assert ClassMix.query.count() == n_before
