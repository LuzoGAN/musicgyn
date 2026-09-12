"""Configurações da aplicação GymBeats."""
import os

basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or "gymbeats-dev-secret-change-me"
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or (
        "sqlite:///" + os.path.join(basedir, "gymbeats.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Cookies próprios (não colidem com outros apps no mesmo domínio)
    SESSION_COOKIE_NAME = os.environ.get("SESSION_COOKIE_NAME") or "gymbeats_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Atrás de proxy HTTPS (nginx), o cookie só trafega com Secure
    SESSION_COOKIE_SECURE = os.environ.get("FLASK_ENV") == "production"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
