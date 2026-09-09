"""Fixtures compartilhadas dos testes."""
import pytest

from app import create_app
from config import TestConfig
from models import Playlist, SessionPlaylist, VoteSession, Gym, db
from datetime import datetime, timedelta


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def gym(app):
    g = Gym(name="Box Teste", email="box@teste.com", qr_code_token="token-teste-123")
    g.set_password("senha123")
    db.session.add(g)
    db.session.commit()
    return g


@pytest.fixture
def playlist(gym):
    p = Playlist(
        gym_id=gym.id, name="Rock p/ WOD", platform="youtube",
        platform_url="https://www.youtube.com/watch?v=abc123XYZ_-",
        embed_id="abc123XYZ_-", created_by="Coach",
        category="wod", approved=True,
    )
    db.session.add(p)
    db.session.commit()
    return p


@pytest.fixture
def session_with_playlist(gym, playlist):
    vs = VoteSession(
        gym_id=gym.id, title="WOD Segunda 18h",
        class_date=datetime.utcnow() + timedelta(days=1),
        voting_ends=datetime.utcnow() + timedelta(hours=24),
        status="open",
    )
    db.session.add(vs)
    db.session.flush()
    db.session.add(SessionPlaylist(session_id=vs.id, playlist_id=playlist.id))
    db.session.commit()
    return vs


def login(client, email="box@teste.com", password="senha123"):
    return client.post("/login", data={"email": email, "password": password},
                       follow_redirects=True)
