"""Testes de auditoria: regressões dos erros encontrados na revisão."""
from datetime import datetime, timedelta

import pytest

from mix_logic import build_class_queue
from models import (ClassMix, Playlist, SessionPlaylist, SongSuggestion, Vote,
                    VoteSession, db)
from tests.conftest import login


def _mix(client, gym, playlist, session):
    from models import PlaylistTrack
    db.session.add(PlaylistTrack(
        playlist_id=playlist.id, gym_id=gym.id, title='B', platform='youtube',
        platform_url='https://youtu.be/baseAAB_-', embed_id='baseAAB_-',
        duration_sec=180))
    db.session.commit()
    client.post('/dashboard/mix/new', data={
        'title': 'Mix Audit', 'session_id': str(session.id),
        'base_playlist_id': str(playlist.id),
        'target_minutes': '15', 'base_per_request': '3',
    }, follow_redirects=True)
    return ClassMix.query.filter_by(title='Mix Audit').first()


# --- F1: playlist em uso não pode ser excluída (evita fantasmas na votação)

def test_delete_bloqueado_quando_em_sessao(client, gym, playlist,
                                           session_with_playlist):
    login(client)
    resp = client.get(f'/dashboard/playlist/{playlist.id}/delete',
                      follow_redirects=True)
    assert 'em uso' in resp.get_data(as_text=True)
    assert db.session.get(Playlist, playlist.id) is not None


def test_delete_ok_quando_sem_uso(client, gym, playlist):
    login(client)
    client.get(f'/dashboard/playlist/{playlist.id}/delete',
               follow_redirects=True)
    assert db.session.get(Playlist, playlist.id) is None


# --- F2: API respeita fim do prazo de votação

def test_api_vote_expirada_fecha_sessao(client, gym, session_with_playlist,
                                        playlist):
    session_with_playlist.voting_ends = datetime.utcnow() - timedelta(hours=1)
    db.session.commit()
    resp = client.post(f'/api/v1/gym/{gym.qr_code_token}/vote', json={
        'session_id': session_with_playlist.id, 'playlist_id': playlist.id,
        'user_identifier': 'x1'})
    assert resp.status_code == 400
    assert db.session.get(VoteSession, session_with_playlist.id).status == 'closed'


# --- F3: pular com mix terminado não cria voto fantasma

def test_skip_mix_terminado(client, gym, playlist, session_with_playlist):
    from models import SkipVote
    login(client)
    mix = _mix(client, gym, playlist, session_with_playlist)
    mix.current_index = len(mix.items)  # terminou
    db.session.commit()
    resp = client.post(f'/gym/{gym.qr_code_token}/skip',
                       data={'mix_id': mix.id}, follow_redirects=True)
    assert 'terminou' in resp.get_data(as_text=True)
    assert SkipVote.query.filter_by(mix_id=mix.id).count() == 0
    api = client.post(
        f'/api/v1/gym/{gym.qr_code_token}/mix/{mix.id}/skip',
        json={'user_identifier': 'd1'})
    assert api.status_code == 400


# --- F7: dois anônimos com nomes distintos votam na mesma rede

def test_anonimos_distintos_mesmo_ip(client, gym, session_with_playlist,
                                     playlist):
    for nome in ('Ana', 'Beto'):
        resp = client.post(f'/gym/{gym.qr_code_token}/vote', data={
            'session_id': session_with_playlist.id,
            'playlist_id': playlist.id, 'user_name': nome,
        }, follow_redirects=True)
        assert 'registrado' in resp.get_data(as_text=True).lower()
    assert Vote.query.filter_by(
        session_id=session_with_playlist.id).count() == 2


# --- F8: ids inválidos na API não quebram

def test_api_ids_invalidos(client, gym):
    r = client.post(f'/api/v1/gym/{gym.qr_code_token}/vote', json={
        'session_id': 'abc', 'playlist_id': 'xyz', 'user_identifier': 'u'})
    assert r.status_code in (400, 404)
    r = client.post(f'/api/v1/gym/{gym.qr_code_token}/mix/preview',
                    json={'session_id': 'ops', 'target_minutes': 'xx'})
    assert r.status_code == 200  # usa padrões, sem 500


# --- F9: link de vídeo avulso importa 1 faixa

def test_import_video_avulso_uma_faixa():
    from importers import import_youtube_playlist
    tracks = import_youtube_playlist('https://youtu.be/dQw4w9WgXcQ', 15)
    assert len(tracks) == 1
    assert tracks[0]['embed_id'] == 'dQw4w9WgXcQ'


# --- F14: javascript: bloqueado em todas as entradas

@pytest.mark.parametrize('payload', [
    "javascript:alert('https://youtu.be/abc123DEF_-')",
    'JaVaScRiPt:alert(1)',
    'data:text/html,<h1>x</h1>',
])
def test_urls_maliciosas_rejeitadas(client, gym, payload):
    login(client)
    client.post('/dashboard/playlist/new', data={
        'name': ' evil', 'platform': 'youtube', 'platform_url': payload,
    }, follow_redirects=True)
    assert Playlist.query.filter_by(name=' evil').first() is None


def test_sugestao_aluno_javascript_rejeitada(client, gym):
    client.post(f'/gym/{gym.qr_code_token}/suggest', data={
        'name': 'evil', 'platform': 'youtube',
        'platform_url': "javascript:x='https://youtu.be/abc123DEF_-'",
    }, follow_redirects=True)
    assert Playlist.query.filter_by(name='evil').first() is None


def test_pedido_musica_javascript_rejeitado(client, gym):
    client.post(f'/gym/{gym.qr_code_token}/suggest-song', data={
        'title': 'evil', 'platform': 'youtube',
        'platform_url': "javascript:x='https://youtu.be/abc123DEF_-'",
    }, follow_redirects=True)
    assert SongSuggestion.query.filter_by(title='evil').first() is None


def test_logo_invalido_ignorado(client, gym):
    login(client)
    client.post('/dashboard/settings', data={'logo_url': 'javascript:alert(1)'},
                follow_redirects=True)
    assert db.session.get(type(gym), gym.id).logo_url is None


# --- F5/F6: tocando agora sobrevive ao fim da votação; mix novo desativa antigo

def test_tocando_agora_sem_votacao_aberta(client, gym, playlist,
                                          session_with_playlist):
    login(client)
    mix = _mix(client, gym, playlist, session_with_playlist)
    session_with_playlist.status = 'finished'  # aula rolando, votação fechada
    db.session.commit()
    html = client.get(f'/gym/{gym.qr_code_token}').get_data(as_text=True)
    assert 'TOCANDO AGORA' in html
    assert mix.items[0].title in html


def test_mix_novo_desativa_antigo(client, gym, playlist, session_with_playlist):
    login(client)
    m1 = _mix(client, gym, playlist, session_with_playlist)
    client.post('/dashboard/mix/new', data={
        'title': 'Mix Novo', 'session_id': str(session_with_playlist.id),
        'base_playlist_id': str(playlist.id),
        'target_minutes': '15', 'base_per_request': '3',
    }, follow_redirects=True)
    assert db.session.get(ClassMix, m1.id).is_active is False
    m2 = ClassMix.query.filter_by(title='Mix Novo').first()
    assert m2.is_active is True


# --- F10/F11/F12: contrato API

def test_api_info_expõe_regras(client, gym):
    info = client.get(
        f'/api/v1/gym/{gym.qr_code_token}/info').get_json()
    assert info['max_requests_per_session'] == 3
    assert info['skip_votes_needed'] == 5


def test_register_email_invalido(client):
    client.post('/register', data={
        'name': 'X', 'email': 'sem-arroba', 'password': 'abcdef'},
        follow_redirects=True)
    from models import Gym
    assert Gym.query.filter_by(email='sem-arroba').first() is None


# --- mix_logic: alvo absurdo termina

def test_alvo_absurdo_termina():
    import time
    base = [{'title': 'B', 'duration_sec': 200}]
    t0 = time.time()
    q, total = build_class_queue(base, [], 3, 10 ** 9, seed=1)
    assert time.time() - t0 < 5
    assert total <= 480 * 60 + 210
