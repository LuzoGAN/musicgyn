"""Testes de modelos (senha, relacionamentos, unicidade de voto)."""
import pytest
from sqlalchemy.exc import IntegrityError

from models import Playlist, Vote, db


def test_senha_hash(gym):
    assert gym.password_hash != "senha123"
    assert gym.check_password("senha123")
    assert not gym.check_password("outra")


def test_cores_padrao(gym):
    assert gym.primary_color == "#e50914"
    assert gym.secondary_color == "#141414"


def test_voto_unico_por_sessao(gym, session_with_playlist, playlist):
    db.session.add(Vote(session_id=session_with_playlist.id, playlist_id=playlist.id,
                        user_identifier="aluno-x"))
    db.session.commit()
    db.session.add(Vote(session_id=session_with_playlist.id, playlist_id=playlist.id,
                        user_identifier="aluno-x"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_playlist_valida_plataformas(gym):
    for plat in Playlist.VALID_PLATFORMS:
        assert isinstance(plat, str)
    assert "spotify" in Playlist.VALID_PLATFORMS
    assert "youtube" in Playlist.VALID_PLATFORMS
