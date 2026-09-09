"""Modelos do banco de dados - GymBeats."""
from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


class Gym(UserMixin, db.Model):
    """Academia / Box de CrossFit (usuário administrador)."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    # Personalização visual (padrão: preto + vermelho)
    primary_color = db.Column(db.String(7), default="#e50914")
    secondary_color = db.Column(db.String(7), default="#141414")
    accent_color = db.Column(db.String(7), default="#ffffff")
    logo_url = db.Column(db.String(500))

    # Acesso público via QR Code
    qr_code_token = db.Column(db.String(100), unique=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Engajamento da aula
    max_requests_per_session = db.Column(db.Integer, default=3)  # pedidos por aluno/aula
    skip_votes_needed = db.Column(db.Integer, default=5)  # votos p/ pular faixa

    playlists = db.relationship("Playlist", backref="gym", lazy=True,
                                cascade="all, delete-orphan")
    sessions = db.relationship("VoteSession", backref="gym", lazy=True,
                               cascade="all, delete-orphan")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Playlist(db.Model):
    """Playlist sugerida ou oficial da academia."""

    VALID_PLATFORMS = ("youtube", "spotify", "apple_music", "soundcloud", "deezer")

    id = db.Column(db.Integer, primary_key=True)
    gym_id = db.Column(db.Integer, db.ForeignKey("gym.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    created_by = db.Column(db.String(100), default="Admin")

    # youtube | spotify | apple_music | soundcloud | deezer
    platform = db.Column(db.String(50), nullable=False)
    platform_url = db.Column(db.String(500), nullable=False)
    embed_id = db.Column(db.String(200))

    # Categoria do treino: wod, weightlifting, cardio, mobility, open_gym...
    category = db.Column(db.String(50), default="wod")

    is_official = db.Column(db.Boolean, default=False)
    approved = db.Column(db.Boolean, default=True)  # sugestões de alunos entram como False
    vote_count = db.Column(db.Integer, default=0)
    times_played = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    votes = db.relationship("Vote", backref="playlist", lazy=True,
                            cascade="all, delete-orphan")
    tracks = db.relationship("PlaylistTrack", backref="playlist", lazy=True,
                             cascade="all, delete-orphan")


class VoteSession(db.Model):
    """Sessão de votação para uma aula específica."""

    id = db.Column(db.Integer, primary_key=True)
    gym_id = db.Column(db.Integer, db.ForeignKey("gym.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)

    # open | closed | finished
    status = db.Column(db.String(20), default="open")

    class_date = db.Column(db.DateTime, nullable=False)
    voting_starts = db.Column(db.DateTime, default=datetime.utcnow)
    voting_ends = db.Column(db.DateTime)

    winning_playlist_id = db.Column(db.Integer, db.ForeignKey("playlist.id"))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    votes = db.relationship("Vote", backref="session", lazy=True,
                            cascade="all, delete-orphan")
    available_playlists = db.relationship("SessionPlaylist", backref="session",
                                          lazy=True, cascade="all, delete-orphan")
    winning_playlist = db.relationship("Playlist", foreign_keys=[winning_playlist_id])


class SessionPlaylist(db.Model):
    """Playlist disponível dentro de uma sessão de votação."""

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("vote_session.id"), nullable=False)
    playlist_id = db.Column(db.Integer, db.ForeignKey("playlist.id"), nullable=False)

    playlist = db.relationship("Playlist")


class Vote(db.Model):
    """Um voto de um aluno em uma sessão."""

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("vote_session.id"), nullable=False)
    playlist_id = db.Column(db.Integer, db.ForeignKey("playlist.id"), nullable=False)
    user_identifier = db.Column(db.String(100), nullable=False)
    user_name = db.Column(db.String(100))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("session_id", "user_identifier",
                            name="unique_vote_per_session"),
    )


class PlaylistTrack(db.Model):
    """Faixa individual dentro de uma playlist base (link de música).

    Permite que a academia cadastre o conteúdo de uma playlist criada por
    link (YouTube/Spotify/...) para o Modo Aula conseguir intercalar as
    faixas da base com os pedidos avulsos dos alunos.
    """

    id = db.Column(db.Integer, primary_key=True)
    playlist_id = db.Column(db.Integer, db.ForeignKey("playlist.id"), nullable=False)
    gym_id = db.Column(db.Integer, db.ForeignKey("gym.id"), nullable=False)

    title = db.Column(db.String(200), nullable=False)
    artist = db.Column(db.String(200))
    platform = db.Column(db.String(50), nullable=False)
    platform_url = db.Column(db.String(500), nullable=False)
    embed_id = db.Column(db.String(200))
    duration_sec = db.Column(db.Integer)  # opcional; estimamos 210s se vazio

    added_by = db.Column(db.String(100), default="Admin")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class SongSuggestion(db.Model):
    """Pedido individual de música (pode ser da academia ou de uma aula)."""

    id = db.Column(db.Integer, primary_key=True)
    gym_id = db.Column(db.Integer, db.ForeignKey("gym.id"), nullable=False)
    playlist_id = db.Column(db.Integer, db.ForeignKey("playlist.id"))
    session_id = db.Column(db.Integer, db.ForeignKey("vote_session.id"))

    title = db.Column(db.String(200), nullable=False)
    artist = db.Column(db.String(200))
    platform = db.Column(db.String(50), nullable=False)
    platform_url = db.Column(db.String(500), nullable=False)
    embed_id = db.Column(db.String(200))
    duration_sec = db.Column(db.Integer)

    suggested_by = db.Column(db.String(100))
    approved = db.Column(db.Boolean, default=False)
    played = db.Column(db.Boolean, default=False)
    # chave do limite por aluno (nome normalizado; anônimos incluem IP)
    user_key = db.Column(db.String(150))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    session = db.relationship("VoteSession", foreign_keys=[session_id])


class ClassMix(db.Model):
    """Mix gerado para uma aula: base + pedidos intercalados de forma justa."""

    id = db.Column(db.Integer, primary_key=True)
    gym_id = db.Column(db.Integer, db.ForeignKey("gym.id"), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey("vote_session.id"))
    base_playlist_id = db.Column(db.Integer, db.ForeignKey("playlist.id"))

    title = db.Column(db.String(200), nullable=False)
    target_minutes = db.Column(db.Integer, default=60)
    base_per_request = db.Column(db.Integer, default=3)

    current_index = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship("ClassMixItem", backref="mix", lazy=True,
                            cascade="all, delete-orphan",
                            order_by="ClassMixItem.position")
    session = db.relationship("VoteSession", foreign_keys=[session_id])
    base_playlist = db.relationship("Playlist", foreign_keys=[base_playlist_id])


class ClassMixItem(db.Model):
    """Um item da fila do mix (faixa da base ou pedido de aluno)."""

    id = db.Column(db.Integer, primary_key=True)
    mix_id = db.Column(db.Integer, db.ForeignKey("class_mix.id"), nullable=False)
    position = db.Column(db.Integer, nullable=False)

    # base | request | block (bloco da playlist quando sem faixas cadastradas)
    kind = db.Column(db.String(20), default="base")

    title = db.Column(db.String(200), nullable=False)
    artist = db.Column(db.String(200))
    platform = db.Column(db.String(50))
    platform_url = db.Column(db.String(500))
    embed_id = db.Column(db.String(200))
    requested_by = db.Column(db.String(100))
    duration_sec = db.Column(db.Integer, default=210)
    played = db.Column(db.Boolean, default=False)


class SkipVote(db.Model):
    """Voto para pular a faixa atual do mix (pular democrático)."""

    id = db.Column(db.Integer, primary_key=True)
    mix_id = db.Column(db.Integer, db.ForeignKey("class_mix.id"), nullable=False)
    position = db.Column(db.Integer, nullable=False)  # faixa alvo no momento do voto
    user_identifier = db.Column(db.String(100), nullable=False)
    user_name = db.Column(db.String(100))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("mix_id", "position", "user_identifier",
                            name="unique_skip_per_track"),
    )


class Notification(db.Model):
    """Aviso da academia (ex: nova votação aberta). O app mobile lê via API
    com polling (base pronta p/ push/FCM no futuro)."""

    id = db.Column(db.Integer, primary_key=True)
    gym_id = db.Column(db.Integer, db.ForeignKey("gym.id"), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey("vote_session.id"))

    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class GymUser(db.Model):
    """Aluno da academia (base do futuro sistema de karma)."""

    id = db.Column(db.Integer, primary_key=True)
    gym_id = db.Column(db.Integer, db.ForeignKey("gym.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    karma_points = db.Column(db.Integer, default=0)
    suggestions_made = db.Column(db.Integer, default=0)
    votes_cast = db.Column(db.Integer, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)
