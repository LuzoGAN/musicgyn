"""GymBeats - Sistema de Playlist Colaborativa para Academias.

Web em Flask + API REST v1 pronta para apps iOS/Android.
"""
import secrets
from datetime import datetime, timedelta

from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   url_for)
from flask_login import (LoginManager, current_user, login_required,
                         login_user, logout_user)

from config import Config
from models import (ClassMix, ClassMixItem, Notification, Playlist,
                    PlaylistTrack, SessionPlaylist, SkipVote, SongSuggestion,
                    Vote, VoteSession, Gym, db)
from mix_logic import build_class_queue, fair_order_requests, queue_summary
from utils import (extract_platform_id, generate_qr_code, is_http_url,
                   is_valid_color)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Faça login para acessar o painel."
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Gym, int(user_id))


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)

    register_routes(app)
    register_api(app)
    register_errors(app)
    return app


# ---------------------------------------------------------------- helpers

def get_gym_or_404(gym_token: str) -> Gym:
    gym = Gym.query.filter_by(qr_code_token=gym_token).first()
    if not gym or not gym.is_active:
        from flask import abort
        abort(404)
    return gym


def winner_of_session(session_id: int):
    """Retorna (playlist, nº de votos) vencedora da sessão, ou (None, 0)."""
    row = (
        db.session.query(Vote.playlist_id, db.func.count(Vote.id).label("total"))
        .filter(Vote.session_id == session_id)
        .group_by(Vote.playlist_id)
        .order_by(db.desc("total"))
        .first()
    )
    if not row:
        return None, 0
    playlist = db.session.get(Playlist, row.playlist_id)
    return playlist, row.total


def ensure_schema():
    """Migração leve p/ bancos criados na fase 1 (adiciona colunas novas)."""
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)
    if "song_suggestion" in insp.get_table_names():
        existing = {c["name"] for c in insp.get_columns("song_suggestion")}
        for col in ("session_id INTEGER", "embed_id VARCHAR(200)",
                    "duration_sec INTEGER", "played BOOLEAN",
                    "user_key VARCHAR(150)"):
            name = col.split()[0]
            if name not in existing:
                default = " DEFAULT 0" if name == "played" else ""
                db.session.execute(
                    text(f"ALTER TABLE song_suggestion ADD COLUMN {name}"
                         f" {col.split(None, 1)[1]}{default}"))
        # backfill da chave p/ pedidos antigos
        db.session.execute(text(
            "UPDATE song_suggestion SET user_key = lower(trim(suggested_by)) "
            "WHERE user_key IS NULL AND suggested_by IS NOT NULL"))
        db.session.commit()
    if "gym" in insp.get_table_names():
        existing = {c["name"] for c in insp.get_columns("gym")}
        for name, coltype, dflt in (
            ("max_requests_per_session", "INTEGER", "3"),
            ("skip_votes_needed", "INTEGER", "5"),
        ):
            if name not in existing:
                db.session.execute(
                    text(f"ALTER TABLE gym ADD COLUMN {name} {coltype}"
                         f" DEFAULT {dflt}"))
        db.session.commit()


def requester_key(name, ip=""):
    """Chave do limite de pedidos: nome normalizado; anônimos usam IP junto
    para não somarem no mesmo balde."""
    norm = (name or "").strip().lower() or "anonimo"
    if norm in ("anonimo", "anônimo", "anon"):
        return f"anonimo|{(ip or '').strip()}"
    return norm


def voter_identifier(email, name, ip):
    """Identificador de voto: email se dado, senão IP+nome (distingue duas
    pessoas 'Anônimo'/'Ana' na mesma rede sem impedir ninguém)."""
    email = (email or "").strip().lower()
    if email:
        return email
    norm = (name or "").strip().lower() or "anonimo"
    return f"{(ip or '').strip()}|{norm}"


def to_int(value):
    """Coage id vindo da API (pode vir string). None se inválido."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def current_playing_mix(gym_id, session_id=None):
    """Mix a exibir como 'tocando agora': o da aula se ainda tem faixas,
    senão o último mix ativo da academia com faixas restantes."""
    def unfinished(mix):
        if not mix:
            return None
        n = ClassMixItem.query.filter_by(mix_id=mix.id).count()
        return mix if 0 <= mix.current_index < n else None
    if session_id:
        mix = unfinished(current_mix_for_gym(gym_id, session_id))
        if mix:
            return mix
    return unfinished(current_mix_for_gym(gym_id))


def count_user_requests(gym_id, session_id, key):
    """Quantos pedidos essa pessoa já fez (na aula ou gerais)."""
    songs = session_songs(gym_id, session_id, approved_only=False)
    n = 0
    for s in songs:
        stored = s.user_key or (s.suggested_by or "").strip().lower()
        if stored == key:
            n += 1
    return n


def skip_votes_count(mix_id, position):
    return SkipVote.query.filter_by(mix_id=mix_id, position=position).count()


def advance_mix(mix):
    """Avança o mix uma faixa (marca atual como tocada)."""
    n = ClassMixItem.query.filter_by(mix_id=mix.id).count()
    if mix.current_index < n:
        items = ClassMixItem.query.filter_by(mix_id=mix.id).order_by(
            ClassMixItem.position.asc()).all()
        if 0 <= mix.current_index < len(items):
            items[mix.current_index].played = True
        mix.current_index = min(mix.current_index + 1, n)
        db.session.commit()
        return True
    return False


def current_mix_for_gym(gym_id, session_id=None):
    """Último mix ativo (da aula, se informada, senão da academia)."""
    q = ClassMix.query.filter_by(gym_id=gym_id, is_active=True)
    if session_id:
        q = q.filter_by(session_id=session_id)
    return q.order_by(ClassMix.created_at.desc()).first()


def session_songs(gym_id: int, session_id: int | None, approved_only=True,
                  include_general=True):
    """Pedidos de música da aula (+ gerais, que valem p/ qualquer aula).

    Sem session_id, retorna só os gerais.
    """
    q = SongSuggestion.query.filter_by(gym_id=gym_id)
    if session_id:
        if include_general:
            q = q.filter(db.or_(SongSuggestion.session_id == session_id,
                                SongSuggestion.session_id.is_(None)))
        else:
            q = q.filter_by(session_id=session_id)
    else:
        q = q.filter(SongSuggestion.session_id.is_(None))
    if approved_only:
        q = q.filter_by(approved=True)
    return q.order_by(SongSuggestion.created_at.asc()).all()


def song_to_dict(s: SongSuggestion):
    return {
        "id": s.id,
        "title": s.title,
        "artist": s.artist,
        "platform": s.platform,
        "platform_url": s.platform_url,
        "embed_id": s.embed_id,
        "duration_sec": s.duration_sec,
        "suggested_by": s.suggested_by,
        "session_id": s.session_id,
        "approved": s.approved,
        "played": s.played,
    }


# ---------------------------------------------------------------- rotas web

def register_routes(app: Flask):

    @app.route("/")
    def index():
        gym_token = request.args.get("gym")
        if gym_token:
            gym = Gym.query.filter_by(qr_code_token=gym_token).first()
            if gym:
                return redirect(url_for("gym_public", gym_token=gym_token))
            flash("QR Code inválido.", "error")
        gyms_count = Gym.query.filter_by(is_active=True).count()
        return render_template("index.html", gyms_count=gyms_count)

    # ------------------------------- área pública do aluno
    @app.route("/gym/<gym_token>")
    def gym_public(gym_token):
        gym = get_gym_or_404(gym_token)
        active_session = (
            VoteSession.query.filter_by(gym_id=gym.id, status="open")
            .order_by(VoteSession.created_at.desc())
            .first()
        )
        top_playlists = (
            Playlist.query.filter_by(gym_id=gym.id, approved=True)
            .order_by(Playlist.vote_count.desc())
            .limit(10)
            .all()
        )
        official = (
            Playlist.query.filter_by(gym_id=gym.id, is_official=True, approved=True)
            .order_by(Playlist.created_at.desc())
            .limit(5)
            .all()
        )
        category = request.args.get("category", "")
        if category:
            top_playlists = [p for p in top_playlists if p.category == category]
        session_song_list = session_songs(
            gym.id, active_session.id if active_session else None) if active_session else []
        latest_notice = (Notification.query.filter_by(gym_id=gym.id)
                         .order_by(Notification.created_at.desc()).first())
        now_mix = current_playing_mix(
            gym.id, active_session.id if active_session else None)
        now_item, skip_votes, skip_needed = None, 0, gym.skip_votes_needed or 5
        if now_mix:
            items = ClassMixItem.query.filter_by(mix_id=now_mix.id).order_by(
                ClassMixItem.position.asc()).all()
            if 0 <= now_mix.current_index < len(items):
                now_item = items[now_mix.current_index]
                skip_votes = skip_votes_count(now_mix.id, now_mix.current_index)
        return render_template(
            "gym_public.html",
            gym=gym,
            active_session=active_session,
            top_playlists=top_playlists,
            official_playlists=official,
            selected_category=category,
            session_song_list=session_song_list,
            latest_notice=latest_notice,
            now_mix=now_mix,
            now_item=now_item,
            skip_votes=skip_votes,
            skip_needed=skip_needed,
        )

    @app.route("/gym/<gym_token>/vote", methods=["POST"])
    def cast_vote(gym_token):
        gym = get_gym_or_404(gym_token)
        session_id = request.form.get("session_id", type=int)
        playlist_id = request.form.get("playlist_id", type=int)
        user_name = (request.form.get("user_name") or "Anônimo").strip()[:100]
        user_email = (request.form.get("user_email") or "").strip().lower()

        vote_session = db.session.get(VoteSession, session_id)
        if not vote_session or vote_session.gym_id != gym.id:
            flash("Sessão de votação não encontrada.", "error")
            return redirect(url_for("gym_public", gym_token=gym_token))
        if vote_session.status != "open":
            flash("Esta votação já foi encerrada.", "error")
            return redirect(url_for("gym_public", gym_token=gym_token))
        if vote_session.voting_ends and datetime.utcnow() > vote_session.voting_ends:
            vote_session.status = "closed"
            db.session.commit()
            flash("O prazo de votação terminou.", "warning")
            return redirect(url_for("gym_public", gym_token=gym_token))
        if not playlist_id or not db.session.get(Playlist, playlist_id):
            flash("Escolha uma playlist válida.", "error")
            return redirect(url_for("gym_public", gym_token=gym_token))

        # A playlist precisa estar na sessão
        in_session = SessionPlaylist.query.filter_by(
            session_id=session_id, playlist_id=playlist_id
        ).first()
        if not in_session:
            flash("Essa playlist não faz parte desta votação.", "error")
            return redirect(url_for("gym_public", gym_token=gym_token))

        user_identifier = voter_identifier(user_email, user_name,
                                            request.remote_addr)

        existing = Vote.query.filter_by(
            session_id=session_id, user_identifier=user_identifier
        ).first()
        if existing:
            flash("Você já votou nesta sessão!", "warning")
            return redirect(url_for("gym_public", gym_token=gym_token))

        from sqlalchemy.exc import IntegrityError
        try:
            db.session.add(Vote(
                session_id=session_id,
                playlist_id=playlist_id,
                user_identifier=user_identifier,
                user_name=user_name,
            ))
            playlist = db.session.get(Playlist, playlist_id)
            playlist.vote_count = (playlist.vote_count or 0) + 1
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Você já votou nesta sessão!", "warning")
            return redirect(url_for("gym_public", gym_token=gym_token))
        flash("Voto registrado com sucesso! 🎵", "success")
        return redirect(url_for("gym_public", gym_token=gym_token))

    @app.route("/gym/<gym_token>/suggest", methods=["POST"])
    def suggest_playlist(gym_token):
        gym = get_gym_or_404(gym_token)
        name = (request.form.get("name") or "").strip()
        platform = (request.form.get("platform") or "").strip()
        platform_url = (request.form.get("platform_url") or "").strip()
        suggested_by = (request.form.get("suggested_by") or "Anônimo").strip()[:100]
        category = (request.form.get("category") or "wod").strip()[:50]
        description = (request.form.get("description") or "").strip()

        if not name or not platform or not platform_url:
            flash("Preencha nome, plataforma e URL.", "error")
            return redirect(url_for("gym_public", gym_token=gym_token))
        if platform not in Playlist.VALID_PLATFORMS:
            flash("Plataforma inválida.", "error")
            return redirect(url_for("gym_public", gym_token=gym_token))
        if not is_http_url(platform_url):
            flash("URL deve começar com http(s).", "error")
            return redirect(url_for("gym_public", gym_token=gym_token))

        embed_id = extract_platform_id(platform, platform_url)
        if not embed_id:
            flash("URL inválida para a plataforma selecionada.", "error")
            return redirect(url_for("gym_public", gym_token=gym_token))

        db.session.add(Playlist(
            gym_id=gym.id,
            name=name[:200],
            description=description or None,
            platform=platform,
            platform_url=platform_url,
            embed_id=embed_id,
            created_by=suggested_by,
            category=category,
            approved=False,  # entra em moderação
        ))
        db.session.commit()
        flash("Playlist sugerida! Ela entra na votação após aprovação. 🎶", "success")
        return redirect(url_for("gym_public", gym_token=gym_token))

    @app.route("/gym/<gym_token>/suggest-song", methods=["POST"])
    def suggest_song(gym_token):
        """Pedido de música avulsa — vale para a aula (sessão) se informada."""
        gym = get_gym_or_404(gym_token)
        title = (request.form.get("title") or "").strip()
        artist = (request.form.get("artist") or "").strip()
        platform = (request.form.get("platform") or "youtube").strip()
        platform_url = (request.form.get("platform_url") or "").strip()
        suggested_by = (request.form.get("suggested_by") or "Anônimo").strip()[:100]
        session_id = request.form.get("session_id", type=int)

        if not title or not platform_url:
            flash("Informe ao menos o título e o link da música.", "error")
            return redirect(url_for("gym_public", gym_token=gym_token))
        if platform not in Playlist.VALID_PLATFORMS:
            flash("Plataforma inválida.", "error")
            return redirect(url_for("gym_public", gym_token=gym_token))
        if not is_http_url(platform_url):
            flash("Link deve começar com http(s).", "error")
            return redirect(url_for("gym_public", gym_token=gym_token))
        embed_id = extract_platform_id(platform, platform_url)
        if not embed_id:
            flash("Link inválido para a plataforma.", "error")
            return redirect(url_for("gym_public", gym_token=gym_token))
        vote_session = None
        if session_id:
            vote_session = db.session.get(VoteSession, session_id)
            if not vote_session or vote_session.gym_id != gym.id:
                flash("Aula (sessão) inválida.", "error")
                return redirect(url_for("gym_public", gym_token=gym_token))

        key = requester_key(suggested_by, request.remote_addr)
        max_req = gym.max_requests_per_session or 3
        already = count_user_requests(
            gym.id, vote_session.id if vote_session else None, key)
        if already >= max_req:
            flash(f"Limite de {max_req} pedido(s) por pessoa nesta aula. "
                  f"Deixa os colegas pedirem também! ✌️", "warning")
            return redirect(url_for("gym_public", gym_token=gym_token))

        db.session.add(SongSuggestion(
            gym_id=gym.id,
            title=title[:200],
            artist=artist[:200] or None,
            platform=platform,
            platform_url=platform_url,
            embed_id=embed_id,
            suggested_by=suggested_by,
            user_key=key,
            session_id=vote_session.id if vote_session else None,
            approved=True,  # pedido de música entra direto na fila da aula
        ))
        db.session.commit()
        flash("Música pedida! Ela entra no sorteio justo da aula. 🤘", "success")
        return redirect(url_for("gym_public", gym_token=gym_token))

    @app.route("/gym/<gym_token>/skip", methods=["POST"])
    def vote_skip(gym_token):
        """Voto para pular a faixa atual (pular democrático)."""
        gym = get_gym_or_404(gym_token)
        mix_id = request.form.get("mix_id", type=int)
        user_name = (request.form.get("user_name") or "Anônimo").strip()[:100]
        user_email = (request.form.get("user_email") or "").strip().lower()
        mix = db.session.get(ClassMix, mix_id)
        if not mix or mix.gym_id != gym.id or not mix.is_active:
            flash("Fila não encontrada.", "error")
            return redirect(url_for("gym_public", gym_token=gym_token))
        n_items = ClassMixItem.query.filter_by(mix_id=mix.id).count()
        if not 0 <= mix.current_index < n_items:
            flash("Esse mix já terminou. 🏁", "warning")
            return redirect(url_for("gym_public", gym_token=gym_token))
        identifier = voter_identifier(user_email, user_name, request.remote_addr)
        pos = mix.current_index
        if SkipVote.query.filter_by(mix_id=mix.id, position=pos,
                                    user_identifier=identifier).first():
            flash("Você já votou p/ pular essa faixa!", "warning")
            return redirect(url_for("gym_public", gym_token=gym_token))
        from sqlalchemy.exc import IntegrityError
        try:
            db.session.add(SkipVote(mix_id=mix.id, position=pos,
                                    user_identifier=identifier,
                                    user_name=user_name))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Você já votou p/ pular essa faixa!", "warning")
            return redirect(url_for("gym_public", gym_token=gym_token))
        needed = gym.skip_votes_needed or 5
        total = skip_votes_count(mix.id, pos)
        if total >= needed:
            advance_mix(mix)
            flash("Pularam a faixa! ⏭️", "success")
        else:
            flash(f"Voto p/ pular registrado ({total}/{needed}) ⏭️", "success")
        return redirect(url_for("gym_public", gym_token=gym_token))

    @app.route("/tv/<gym_token>")
    def tv_mode(gym_token):
        """Modo TV: telão do box com tocando agora + próximos + QR."""
        gym = get_gym_or_404(gym_token)
        session_id = request.args.get("session_id", type=int)
        active_session = None
        if session_id:
            active_session = db.session.get(VoteSession, session_id)
            if not active_session or active_session.gym_id != gym.id:
                active_session = None
        if not active_session:
            active_session = (
                VoteSession.query.filter_by(gym_id=gym.id, status="open")
                .order_by(VoteSession.created_at.desc()).first()
            )
        mix = current_playing_mix(
            gym.id, active_session.id if active_session else None)
        items, now_item, upcoming = [], None, []
        skip_votes, skip_needed = 0, gym.skip_votes_needed or 5
        if mix:
            items = ClassMixItem.query.filter_by(mix_id=mix.id).order_by(
                ClassMixItem.position.asc()).all()
            if 0 <= mix.current_index < len(items):
                now_item = items[mix.current_index]
                upcoming = items[mix.current_index + 1:mix.current_index + 6]
                skip_votes = skip_votes_count(mix.id, mix.current_index)
        qr_data = url_for("gym_public", gym_token=gym.qr_code_token, _external=True)
        qr_code = generate_qr_code(qr_data)
        queue_url = None
        if mix and active_session and mix.session_id == active_session.id:
            queue_url = url_for("api_session_queue", gym_token=gym.qr_code_token,
                                session_id=active_session.id)
        return render_template("tv.html", gym=gym, active_session=active_session,
                               mix=mix, items=items, now_item=now_item,
                               upcoming=upcoming, skip_votes=skip_votes,
                               skip_needed=skip_needed, qr_code=qr_code,
                               queue_url=queue_url)

    @app.route("/session/<int:session_id>/results")
    def session_results(session_id):
        vote_session = db.session.get(VoteSession, session_id)
        if not vote_session:
            flash("Sessão não encontrada.", "error")
            return redirect(url_for("index"))
        gym = db.session.get(Gym, vote_session.gym_id)
        ranking = (
            db.session.query(Playlist, db.func.count(Vote.id).label("total"))
            .join(Vote, Vote.playlist_id == Playlist.id)
            .filter(Vote.session_id == session_id)
            .group_by(Playlist.id)
            .order_by(db.desc("total"))
            .all()
        )
        return render_template("vote_session.html", gym=gym,
                               vote_session=vote_session, ranking=ranking)

    # ------------------------------- auth admin
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            gym = Gym.query.filter_by(email=email).first()
            if gym and gym.check_password(password):
                if not gym.is_active:
                    flash("Conta desativada. Fale com o suporte.", "error")
                    return render_template("login.html")
                login_user(gym)
                flash(f"Bem-vindo(a), {gym.name}! 💪", "success")
                return redirect(url_for("dashboard"))
            flash("Email ou senha inválidos.", "error")
        return render_template("login.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            if not name or not email or len(password) < 6 or "@" not in email:
                flash("Preencha nome, email válido e senha com 6+ caracteres.", "error")
                return render_template("register.html")
            if Gym.query.filter_by(email=email).first():
                flash("Email já cadastrado.", "error")
                return render_template("register.html")
            gym = Gym(name=name[:100], email=email,
                      qr_code_token=secrets.token_urlsafe(16))
            gym.set_password(password)
            db.session.add(gym)
            db.session.commit()
            flash("Cadastro realizado! Faça login. 🎉", "success")
            return redirect(url_for("login"))
        return render_template("register.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("index"))

    # ------------------------------- dashboard admin
    @app.route("/dashboard")
    @login_required
    def dashboard():
        total_playlists = Playlist.query.filter_by(gym_id=current_user.id).count()
        pending = Playlist.query.filter_by(gym_id=current_user.id, approved=False).count()
        active_sessions = VoteSession.query.filter_by(
            gym_id=current_user.id, status="open").count()
        total_votes = (
            Vote.query.join(VoteSession)
            .filter(VoteSession.gym_id == current_user.id)
            .count()
        )
        pending_songs = SongSuggestion.query.filter_by(
            gym_id=current_user.id, approved=False).count()
        recent_sessions = (
            VoteSession.query.filter_by(gym_id=current_user.id)
            .order_by(VoteSession.created_at.desc())
            .limit(5)
            .all()
        )
        top_playlists = (
            Playlist.query.filter_by(gym_id=current_user.id)
            .order_by(Playlist.vote_count.desc())
            .limit(5)
            .all()
        )
        qr_data = url_for("gym_public", gym_token=current_user.qr_code_token,
                          _external=True)
        qr_code = generate_qr_code(qr_data)
        recent_mixes = (ClassMix.query.filter_by(gym_id=current_user.id)
                        .order_by(ClassMix.created_at.desc()).limit(3).all())
        return render_template(
            "gym_dashboard.html",
            total_playlists=total_playlists,
            pending_count=pending,
            active_sessions=active_sessions,
            total_votes=total_votes,
            pending_songs=pending_songs,
            recent_sessions=recent_sessions,
            top_playlists=top_playlists,
            qr_code=qr_code,
            recent_mixes=recent_mixes,
        )

    @app.route("/dashboard/session/new", methods=["GET", "POST"])
    @login_required
    def new_session():
        playlists = (
            Playlist.query.filter_by(gym_id=current_user.id, approved=True)
            .order_by(Playlist.vote_count.desc())
            .all()
        )
        if request.method == "POST":
            title = (request.form.get("title") or "").strip()
            description = (request.form.get("description") or "").strip()
            class_date_raw = request.form.get("class_date") or ""
            voting_hours = request.form.get("voting_hours", type=int) or 24
            playlist_ids = request.form.getlist("playlists")
            if not title or not class_date_raw or not playlist_ids:
                flash("Informe título, data da aula e ao menos 1 playlist.", "error")
                return render_template("new_session.html", playlists=playlists)
            try:
                class_datetime = datetime.strptime(class_date_raw, "%Y-%m-%dT%H:%M")
            except ValueError:
                flash("Data da aula inválida.", "error")
                return render_template("new_session.html", playlists=playlists)
            vote_session = VoteSession(
                gym_id=current_user.id,
                title=title[:200],
                description=description or None,
                class_date=class_datetime,
                voting_ends=datetime.utcnow() + timedelta(hours=voting_hours),
            )
            db.session.add(vote_session)
            db.session.flush()
            for pid in playlist_ids:
                try:
                    pid_int = int(pid)
                except (TypeError, ValueError):
                    continue
                pl = db.session.get(Playlist, pid_int)
                if pl and pl.gym_id == current_user.id:
                    db.session.add(SessionPlaylist(session_id=vote_session.id,
                                                   playlist_id=pid_int))
            db.session.add(Notification(
                gym_id=current_user.id, session_id=vote_session.id,
                title=f"Nova votação: {vote_session.title}",
                body=("Vote na playlist da aula e peça suas músicas! "
                      "Abra pelo QR Code da academia."),
            ))
            db.session.commit()
            flash("Sessão de votação criada! 🗳️", "success")
            return redirect(url_for("dashboard"))
        return render_template("new_session.html", playlists=playlists)

    @app.route("/dashboard/session/<int:session_id>/close")
    @login_required
    def close_session(session_id):
        vote_session = db.session.get(VoteSession, session_id)
        if not vote_session or vote_session.gym_id != current_user.id:
            flash("Acesso negado.", "error")
            return redirect(url_for("dashboard"))
        if vote_session.status != "open":
            flash("Sessão já encerrada.", "warning")
            return redirect(url_for("dashboard"))
        winner, total = winner_of_session(session_id)
        vote_session.status = "finished"
        if winner:
            vote_session.winning_playlist_id = winner.id
            winner.times_played = (winner.times_played or 0) + 1
            db.session.commit()
            flash(f"Vencedora: {winner.name} com {total} voto(s)! 🏆", "success")
        else:
            db.session.commit()
            flash("Sessão encerrada sem votos.", "warning")
        return redirect(url_for("session_results", session_id=session_id))

    @app.route("/dashboard/playlists")
    @login_required
    def manage_playlists():
        status = request.args.get("status", "all")
        query = Playlist.query.filter_by(gym_id=current_user.id)
        if status == "pending":
            query = query.filter_by(approved=False)
        elif status == "approved":
            query = query.filter_by(approved=True)
        playlists = query.order_by(Playlist.created_at.desc()).all()
        pending_songs = (
            SongSuggestion.query.filter_by(gym_id=current_user.id)
            .order_by(SongSuggestion.created_at.desc())
            .all()
        )
        return render_template("manage_playlists.html", playlists=playlists,
                               pending_songs=pending_songs, status=status)

    @app.route("/dashboard/playlist/new", methods=["POST"])
    @login_required
    def admin_add_playlist():
        name = (request.form.get("name") or "").strip()
        platform = (request.form.get("platform") or "").strip()
        platform_url = (request.form.get("platform_url") or "").strip()
        category = (request.form.get("category") or "wod").strip()
        description = (request.form.get("description") or "").strip()
        if not name or platform not in Playlist.VALID_PLATFORMS or not platform_url:
            flash("Dados da playlist inválidos.", "error")
            return redirect(url_for("manage_playlists"))
        if not is_http_url(platform_url):
            flash("URL deve começar com http(s).", "error")
            return redirect(url_for("manage_playlists"))
        embed_id = extract_platform_id(platform, platform_url)
        if not embed_id:
            flash("URL inválida para a plataforma.", "error")
            return redirect(url_for("manage_playlists"))
        db.session.add(Playlist(
            gym_id=current_user.id, name=name[:200],
            description=description or None, platform=platform,
            platform_url=platform_url, embed_id=embed_id,
            created_by="Admin", category=category,
            approved=True, is_official=False,
        ))
        db.session.commit()
        flash("Playlist cadastrada! 🎵", "success")
        return redirect(url_for("manage_playlists"))

    @app.route("/dashboard/playlist/<int:playlist_id>/approve")
    @login_required
    def approve_playlist(playlist_id):
        pl = db.session.get(Playlist, playlist_id)
        if not pl or pl.gym_id != current_user.id:
            flash("Acesso negado.", "error")
            return redirect(url_for("manage_playlists"))
        pl.approved = True
        db.session.commit()
        flash(f"Playlist '{pl.name}' aprovada! ✅", "success")
        return redirect(url_for("manage_playlists"))

    @app.route("/dashboard/playlist/<int:playlist_id>/delete")
    @login_required
    def delete_playlist(playlist_id):
        pl = db.session.get(Playlist, playlist_id)
        if not pl or pl.gym_id != current_user.id:
            flash("Acesso negado.", "error")
            return redirect(url_for("manage_playlists"))
        used_in = SessionPlaylist.query.filter_by(playlist_id=pl.id).count()
        won_in = VoteSession.query.filter_by(winning_playlist_id=pl.id).count()
        if used_in or won_in:
            flash("Essa playlist está em uso em votações/aulas e não pode ser "
                  "excluída (exclua a sessão primeiro).", "error")
            return redirect(url_for("manage_playlists"))
        db.session.delete(pl)
        db.session.commit()
        flash("Playlist excluída.", "success")
        return redirect(url_for("manage_playlists"))

    @app.route("/dashboard/playlist/<int:playlist_id>/toggle-official")
    @login_required
    def toggle_official(playlist_id):
        pl = db.session.get(Playlist, playlist_id)
        if not pl or pl.gym_id != current_user.id:
            flash("Acesso negado.", "error")
            return redirect(url_for("manage_playlists"))
        pl.is_official = not pl.is_official
        db.session.commit()
        flash("Status oficial atualizado! ⭐", "success")
        return redirect(url_for("manage_playlists"))

    # ------------------------------- faixas da playlist base
    @app.route("/dashboard/playlist/<int:playlist_id>")
    @login_required
    def playlist_detail(playlist_id):
        pl = db.session.get(Playlist, playlist_id)
        if not pl or pl.gym_id != current_user.id:
            flash("Acesso negado.", "error")
            return redirect(url_for("manage_playlists"))
        tracks = PlaylistTrack.query.filter_by(
            playlist_id=pl.id).order_by(PlaylistTrack.created_at.asc()).all()
        return render_template("playlist_detail.html", playlist=pl, tracks=tracks)

    @app.route("/dashboard/playlist/<int:playlist_id>/tracks/add", methods=["POST"])
    @login_required
    def add_track(playlist_id):
        pl = db.session.get(Playlist, playlist_id)
        if not pl or pl.gym_id != current_user.id:
            flash("Acesso negado.", "error")
            return redirect(url_for("manage_playlists"))
        title = (request.form.get("title") or "").strip()
        artist = (request.form.get("artist") or "").strip()
        platform = (request.form.get("platform") or pl.platform).strip()
        platform_url = (request.form.get("platform_url") or "").strip()
        duration = request.form.get("duration_sec", type=int)
        if not title or not platform_url or platform not in Playlist.VALID_PLATFORMS:
            flash("Informe título, plataforma e link válidos.", "error")
            return redirect(url_for("playlist_detail", playlist_id=pl.id))
        if not is_http_url(platform_url):
            flash("Link deve começar com http(s).", "error")
            return redirect(url_for("playlist_detail", playlist_id=pl.id))
        embed_id = extract_platform_id(platform, platform_url)
        if not embed_id:
            flash("Link inválido para a plataforma.", "error")
            return redirect(url_for("playlist_detail", playlist_id=pl.id))
        db.session.add(PlaylistTrack(
            playlist_id=pl.id, gym_id=current_user.id,
            title=title[:200], artist=artist[:200] or None,
            platform=platform, platform_url=platform_url, embed_id=embed_id,
            duration_sec=duration if duration and duration > 0 else None,
            added_by="Admin",
        ))
        db.session.commit()
        flash("Faixa adicionada à playlist! 🎵", "success")
        return redirect(url_for("playlist_detail", playlist_id=pl.id))

    @app.route("/dashboard/track/<int:track_id>/delete")
    @login_required
    def delete_track(track_id):
        track = db.session.get(PlaylistTrack, track_id)
        if not track or track.gym_id != current_user.id:
            flash("Acesso negado.", "error")
            return redirect(url_for("manage_playlists"))
        pid = track.playlist_id
        db.session.delete(track)
        db.session.commit()
        flash("Faixa removida.", "success")
        return redirect(url_for("playlist_detail", playlist_id=pid))

    @app.route("/dashboard/playlist/<int:playlist_id>/tracks/import", methods=["POST"])
    @login_required
    def import_tracks(playlist_id):
        """Puxa as faixas do link da playlist (limitado ao tempo da aula)."""
        import os
        from importers import import_playlist_tracks, suggest_import_limit
        pl = db.session.get(Playlist, playlist_id)
        if not pl or pl.gym_id != current_user.id:
            flash("Acesso negado.", "error")
            return redirect(url_for("manage_playlists"))
        target_minutes = request.form.get("target_minutes", type=int) or 60
        base_per_request = request.form.get("base_per_request", type=int)
        if base_per_request is None:
            base_per_request = 3
        manual_max = request.form.get("max_tracks", type=int)
        limit = manual_max or suggest_import_limit(target_minutes, base_per_request)
        limit = min(max(limit, 1), 50)
        try:
            tracks = import_playlist_tracks(pl.platform, pl.platform_url, limit, {
                "YOUTUBE_API_KEY": os.environ.get("YOUTUBE_API_KEY"),
                "SPOTIFY_CLIENT_ID": os.environ.get("SPOTIFY_CLIENT_ID"),
                "SPOTIFY_CLIENT_SECRET": os.environ.get("SPOTIFY_CLIENT_SECRET"),
            })
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("playlist_detail", playlist_id=pl.id))
        except Exception:  # rede/API fora do ar
            flash("Falha ao ler a playlist (rede ou link). Tente de novo ou "
                  "cadastre manualmente.", "error")
            return redirect(url_for("playlist_detail", playlist_id=pl.id))
        existing_urls = {t.platform_url for t in
                         PlaylistTrack.query.filter_by(playlist_id=pl.id).all()}
        added = 0
        for t in tracks:
            if t["platform_url"] in existing_urls:
                continue
            db.session.add(PlaylistTrack(
                playlist_id=pl.id, gym_id=current_user.id,
                title=t["title"][:200], artist=(t.get("artist") or None),
                platform=pl.platform, platform_url=t["platform_url"],
                embed_id=t.get("embed_id"),
                duration_sec=t.get("duration_sec"),
                added_by="Importação",
            ))
            added += 1
        db.session.commit()
        if added:
            flash(f"{added} faixa(s) importadas do link! "
                  f"(limite p/ ~{target_minutes} min de aula) 🎉", "success")
        else:
            flash("Nada novo: essas faixas já estavam cadastradas.", "warning")
        return redirect(url_for("playlist_detail", playlist_id=pl.id))

    # ------------------------------- MODO AULA (mix base + pedidos)
    @app.route("/dashboard/mix")
    @login_required
    def mixes():
        all_mixes = (ClassMix.query.filter_by(gym_id=current_user.id)
                     .order_by(ClassMix.created_at.desc()).all())
        sessions = (VoteSession.query.filter_by(gym_id=current_user.id)
                    .order_by(VoteSession.created_at.desc()).limit(10).all())
        playlists = (Playlist.query.filter_by(gym_id=current_user.id, approved=True)
                     .order_by(Playlist.vote_count.desc()).all())
        return render_template("mixes.html", mixes=all_mixes,
                               sessions=sessions, playlists=playlists)

    @app.route("/dashboard/mix/new", methods=["POST"])
    @login_required
    def new_mix():
        title = (request.form.get("title") or "").strip() or "Mix da aula"
        session_id = request.form.get("session_id", type=int)
        base_playlist_id = request.form.get("base_playlist_id", type=int)
        target_minutes = request.form.get("target_minutes", type=int) or 60
        base_per_request = request.form.get("base_per_request", type=int)
        if base_per_request is None:
            base_per_request = 3
        target_minutes = min(max(target_minutes, 15), 240)

        vote_session = None
        if session_id:
            vote_session = db.session.get(VoteSession, session_id)
            if not vote_session or vote_session.gym_id != current_user.id:
                flash("Aula (sessão) inválida.", "error")
                return redirect(url_for("mixes"))
        base_pl = None
        if base_playlist_id:
            base_pl = db.session.get(Playlist, base_playlist_id)
            if not base_pl or base_pl.gym_id != current_user.id:
                flash("Playlist base inválida.", "error")
                return redirect(url_for("mixes"))
        elif vote_session and vote_session.winning_playlist_id:
            base_pl = db.session.get(Playlist, vote_session.winning_playlist_id)

        base_tracks = (PlaylistTrack.query.filter_by(playlist_id=base_pl.id).all()
                       if base_pl else [])
        requests = session_songs(current_user.id,
                                 vote_session.id if vote_session else None)

        import random as _random
        seed = _random.randint(1, 10 ** 9)
        fair = fair_order_requests(requests, seed=seed)
        queue, total_sec = build_class_queue(
            base_tracks, fair, base_per_request=base_per_request,
            target_minutes=target_minutes, seed=seed)
        if not queue:
            flash("Sem conteúdo: cadastre faixas na playlist base e/ou peça "
                  "músicas para a aula.", "warning")
            return redirect(url_for("mixes"))

        # só um mix ativo por vez: o novo assume o "tocando agora"
        for old in ClassMix.query.filter_by(gym_id=current_user.id,
                                            is_active=True).all():
            old.is_active = False
        mix = ClassMix(
            gym_id=current_user.id,
            session_id=vote_session.id if vote_session else None,
            base_playlist_id=base_pl.id if base_pl else None,
            title=title[:200], target_minutes=target_minutes,
            base_per_request=base_per_request,
        )
        db.session.add(mix)
        db.session.flush()
        for i, item in enumerate(queue):
            db.session.add(ClassMixItem(
                mix_id=mix.id, position=i, kind=item["kind"],
                title=item["title"][:200], artist=item.get("artist"),
                platform=item.get("platform"),
                platform_url=item.get("platform_url"),
                embed_id=item.get("embed_id"),
                requested_by=item.get("requested_by"),
                duration_sec=item.get("duration_sec") or 210,
            ))
        db.session.commit()
        summary = queue_summary(queue)
        flash(f"Mix criado: {len(queue)} faixas (~{summary['total_min']} min) "
              f"com sorteio justo! 🔀", "success")
        return redirect(url_for("mix_player", mix_id=mix.id))

    @app.route("/dashboard/mix/<int:mix_id>")
    @login_required
    def mix_player(mix_id):
        mix = db.session.get(ClassMix, mix_id)
        if not mix or mix.gym_id != current_user.id:
            flash("Acesso negado.", "error")
            return redirect(url_for("mixes"))
        items = ClassMixItem.query.filter_by(mix_id=mix.id).order_by(
            ClassMixItem.position.asc()).all()
        total_sec = sum(i.duration_sec or 210 for i in items)
        current = items[mix.current_index] if items and 0 <= mix.current_index < len(items) else None
        skip_votes = skip_votes_count(mix.id, mix.current_index) if current else 0
        return render_template("mix_player.html", mix=mix, items=items,
                               total_sec=total_sec, current=current,
                               skip_votes=skip_votes,
                               skip_needed=current_user.skip_votes_needed or 5)

    @app.route("/dashboard/mix/<int:mix_id>/next")
    @login_required
    def mix_next(mix_id):
        mix = db.session.get(ClassMix, mix_id)
        if not mix or mix.gym_id != current_user.id:
            flash("Acesso negado.", "error")
            return redirect(url_for("mixes"))
        n = ClassMixItem.query.filter_by(mix_id=mix.id).count()
        if mix.current_index < n:
            items = ClassMixItem.query.filter_by(mix_id=mix.id).order_by(
                ClassMixItem.position.asc()).all()
            if 0 <= mix.current_index < len(items):
                items[mix.current_index].played = True
            mix.current_index = min(mix.current_index + 1, n)
            db.session.commit()
        return redirect(url_for("mix_player", mix_id=mix.id))

    @app.route("/dashboard/mix/<int:mix_id>/prev")
    @login_required
    def mix_prev(mix_id):
        mix = db.session.get(ClassMix, mix_id)
        if not mix or mix.gym_id != current_user.id:
            flash("Acesso negado.", "error")
            return redirect(url_for("mixes"))
        mix.current_index = max(mix.current_index - 1, 0)
        items = ClassMixItem.query.filter_by(mix_id=mix.id).order_by(
            ClassMixItem.position.asc()).all()
        if 0 <= mix.current_index < len(items):
            items[mix.current_index].played = False
        db.session.commit()
        return redirect(url_for("mix_player", mix_id=mix.id))

    @app.route("/dashboard/mix/<int:mix_id>/restart")
    @login_required
    def mix_restart(mix_id):
        mix = db.session.get(ClassMix, mix_id)
        if not mix or mix.gym_id != current_user.id:
            flash("Acesso negado.", "error")
            return redirect(url_for("mixes"))
        mix.current_index = 0
        ClassMixItem.query.filter_by(mix_id=mix.id).update({"played": False})
        db.session.commit()
        flash("Mix reiniciado! 🔁", "success")
        return redirect(url_for("mix_player", mix_id=mix.id))

    @app.route("/dashboard/mix/<int:mix_id>/delete")
    @login_required
    def mix_delete(mix_id):
        mix = db.session.get(ClassMix, mix_id)
        if not mix or mix.gym_id != current_user.id:
            flash("Acesso negado.", "error")
            return redirect(url_for("mixes"))
        db.session.delete(mix)
        db.session.commit()
        flash("Mix excluído.", "success")
        return redirect(url_for("mixes"))

    @app.route("/dashboard/song/<int:song_id>/approve")
    @login_required
    def approve_song(song_id):
        s = db.session.get(SongSuggestion, song_id)
        if not s or s.gym_id != current_user.id:
            flash("Acesso negado.", "error")
            return redirect(url_for("manage_playlists"))
        s.approved = True
        db.session.commit()
        flash("Música aprovada p/ a fila! ✅", "success")
        return redirect(url_for("manage_playlists"))

    @app.route("/dashboard/song/<int:song_id>/delete")
    @login_required
    def delete_song(song_id):
        s = db.session.get(SongSuggestion, song_id)
        if not s or s.gym_id != current_user.id:
            flash("Acesso negado.", "error")
            return redirect(url_for("manage_playlists"))
        db.session.delete(s)
        db.session.commit()
        flash("Pedido removido da fila.", "success")
        return redirect(url_for("manage_playlists"))

    @app.route("/dashboard/settings", methods=["GET", "POST"])
    @login_required
    def settings():
        if request.method == "POST":
            primary = (request.form.get("primary_color") or "").strip()
            secondary = (request.form.get("secondary_color") or "").strip()
            accent = (request.form.get("accent_color") or "").strip()
            logo = (request.form.get("logo_url") or "").strip()
            if primary and is_valid_color(primary):
                current_user.primary_color = primary
            if secondary and is_valid_color(secondary):
                current_user.secondary_color = secondary
            if accent and is_valid_color(accent):
                current_user.accent_color = accent
            current_user.logo_url = logo if not logo or is_http_url(logo) else None
            if logo and not is_http_url(logo):
                flash("Logo ignorado: URL deve começar com http(s).", "warning")
            max_req = request.form.get("max_requests_per_session", type=int)
            if max_req is not None:
                current_user.max_requests_per_session = min(max(max_req, 1), 10)
            skip_needed = request.form.get("skip_votes_needed", type=int)
            if skip_needed is not None:
                current_user.skip_votes_needed = min(max(skip_needed, 1), 50)
            db.session.commit()
            flash("Configurações salvas! 🎨", "success")
            return redirect(url_for("settings"))
        return render_template("admin_settings.html")


# ---------------------------------------------------------------- API v1 (mobile-ready)

def playlist_to_dict(p: Playlist):
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "platform": p.platform,
        "platform_url": p.platform_url,
        "embed_id": p.embed_id,
        "category": p.category,
        "is_official": p.is_official,
        "vote_count": p.vote_count,
        "times_played": p.times_played,
        "created_by": p.created_by,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def register_api(app: Flask):

    @app.route("/api/v1/health")
    def api_health():
        return jsonify({"status": "ok", "service": "gymbeats", "version": "1.1.0"})

    @app.route("/api/v1/gym/<gym_token>/info")
    def api_gym_info(gym_token):
        gym = Gym.query.filter_by(qr_code_token=gym_token).first()
        if not gym:
            return jsonify({"error": "Gym not found"}), 404
        return jsonify({
            "id": gym.id,
            "name": gym.name,
            "primary_color": gym.primary_color,
            "secondary_color": gym.secondary_color,
            "accent_color": gym.accent_color,
            "logo_url": gym.logo_url,
            "max_requests_per_session": gym.max_requests_per_session or 3,
            "skip_votes_needed": gym.skip_votes_needed or 5,
        })

    @app.route("/api/v1/gym/<gym_token>/playlists")
    def api_playlists(gym_token):
        gym = Gym.query.filter_by(qr_code_token=gym_token).first()
        if not gym:
            return jsonify({"error": "Gym not found"}), 404
        playlists = (
            Playlist.query.filter_by(gym_id=gym.id, approved=True)
            .order_by(Playlist.vote_count.desc()).all()
        )
        return jsonify({"playlists": [playlist_to_dict(p) for p in playlists]})

    @app.route("/api/v1/gym/<gym_token>/active-session")
    def api_active_session(gym_token):
        gym = Gym.query.filter_by(qr_code_token=gym_token).first()
        if not gym:
            return jsonify({"error": "Gym not found"}), 404
        vote_session = (
            VoteSession.query.filter_by(gym_id=gym.id, status="open")
            .order_by(VoteSession.created_at.desc()).first()
        )
        if not vote_session:
            return jsonify({"active_session": None})
        playlists = [sp.playlist for sp in vote_session.available_playlists]
        return jsonify({"active_session": {
            "id": vote_session.id,
            "title": vote_session.title,
            "description": vote_session.description,
            "class_date": vote_session.class_date.isoformat(),
            "voting_ends": vote_session.voting_ends.isoformat()
            if vote_session.voting_ends else None,
            "status": vote_session.status,
            "playlists": [playlist_to_dict(p) for p in playlists if p],
        }})

    @app.route("/api/v1/gym/<gym_token>/session/<int:session_id>/results")
    def api_session_results(gym_token, session_id):
        gym = Gym.query.filter_by(qr_code_token=gym_token).first()
        if not gym:
            return jsonify({"error": "Gym not found"}), 404
        vote_session = db.session.get(VoteSession, session_id)
        if not vote_session or vote_session.gym_id != gym.id:
            return jsonify({"error": "Session not found"}), 404
        ranking = (
            db.session.query(Playlist, db.func.count(Vote.id).label("total"))
            .join(Vote, Vote.playlist_id == Playlist.id)
            .filter(Vote.session_id == session_id)
            .group_by(Playlist.id)
            .order_by(db.desc("total")).all()
        )
        return jsonify({
            "session_id": session_id,
            "status": vote_session.status,
            "winning_playlist_id": vote_session.winning_playlist_id,
            "ranking": [{"playlist": playlist_to_dict(p), "votes": t}
                        for p, t in ranking],
        })

    @app.route("/api/v1/gym/<gym_token>/vote", methods=["POST"])
    def api_vote(gym_token):
        gym = Gym.query.filter_by(qr_code_token=gym_token).first()
        if not gym:
            return jsonify({"error": "Gym not found"}), 404
        data = request.get_json(silent=True) or {}
        session_id = to_int(data.get("session_id"))
        playlist_id = to_int(data.get("playlist_id"))
        user_identifier = (data.get("user_identifier") or "").strip()
        user_name = (data.get("user_name") or "Anônimo").strip()[:100]
        if not session_id or not playlist_id or not user_identifier:
            return jsonify({"error": "session_id, playlist_id e user_identifier são obrigatórios"}), 400
        vote_session = db.session.get(VoteSession, session_id)
        if not vote_session or vote_session.gym_id != gym.id:
            return jsonify({"error": "Session not found"}), 404
        if vote_session.status != "open":
            return jsonify({"error": "Voting closed"}), 400
        if vote_session.voting_ends and datetime.utcnow() > vote_session.voting_ends:
            vote_session.status = "closed"
            db.session.commit()
            return jsonify({"error": "Voting closed"}), 400
        in_session = SessionPlaylist.query.filter_by(
            session_id=session_id, playlist_id=playlist_id).first()
        if not in_session:
            return jsonify({"error": "Playlist not in this session"}), 400
        if Vote.query.filter_by(session_id=session_id,
                                user_identifier=user_identifier).first():
            return jsonify({"error": "Already voted"}), 400
        from sqlalchemy.exc import IntegrityError
        try:
            db.session.add(Vote(session_id=session_id, playlist_id=playlist_id,
                                user_identifier=user_identifier,
                                user_name=user_name))
            playlist = db.session.get(Playlist, playlist_id)
            playlist.vote_count = (playlist.vote_count or 0) + 1
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify({"error": "Already voted"}), 400
        return jsonify({"success": True, "message": "Vote registered"}), 201

    @app.route("/api/v1/gym/<gym_token>/suggest", methods=["POST"])
    def api_suggest(gym_token):
        gym = Gym.query.filter_by(qr_code_token=gym_token).first()
        if not gym:
            return jsonify({"error": "Gym not found"}), 404
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        platform = (data.get("platform") or "").strip()
        platform_url = (data.get("platform_url") or "").strip()
        if not name or platform not in Playlist.VALID_PLATFORMS or not platform_url:
            return jsonify({"error": "Invalid payload"}), 400
        if not is_http_url(platform_url):
            return jsonify({"error": "URL must start with http(s)"}), 400
        embed_id = extract_platform_id(platform, platform_url)
        if not embed_id:
            return jsonify({"error": "Invalid URL for platform"}), 400
        playlist = Playlist(
            gym_id=gym.id, name=name[:200],
            description=(data.get("description") or "").strip() or None,
            platform=platform, platform_url=platform_url, embed_id=embed_id,
            created_by=(data.get("suggested_by") or "Anônimo").strip()[:100],
            category=(data.get("category") or "wod").strip()[:50],
            approved=False,
        )
        db.session.add(playlist)
        db.session.commit()
        return jsonify({"success": True, "playlist_id": playlist.id}), 201

    @app.route("/api/v1/gym/<gym_token>/request-song", methods=["POST"])
    def api_request_song(gym_token):
        """API: pedir música avulsa p/ a aula (entra no sorteio justo)."""
        gym = Gym.query.filter_by(qr_code_token=gym_token).first()
        if not gym:
            return jsonify({"error": "Gym not found"}), 404
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        platform = (data.get("platform") or "youtube").strip()
        platform_url = (data.get("platform_url") or "").strip()
        session_id = to_int(data.get("session_id"))
        if not title or platform not in Playlist.VALID_PLATFORMS or not platform_url:
            return jsonify({"error": "Invalid payload"}), 400
        if not is_http_url(platform_url):
            return jsonify({"error": "URL must start with http(s)"}), 400
        embed_id = extract_platform_id(platform, platform_url)
        if not embed_id:
            return jsonify({"error": "Invalid URL for platform"}), 400
        if session_id:
            vs = db.session.get(VoteSession, session_id)
            if not vs or vs.gym_id != gym.id:
                return jsonify({"error": "Session not found"}), 404
        suggested_by = (data.get("suggested_by") or "Anônimo").strip()[:100]
        key = requester_key(suggested_by, request.remote_addr)
        max_req = gym.max_requests_per_session or 3
        if count_user_requests(gym.id, session_id, key) >= max_req:
            return jsonify({"error": f"Request limit reached ({max_req} per user)"}), 429
        song = SongSuggestion(
            gym_id=gym.id, title=title[:200],
            artist=(data.get("artist") or "").strip()[:200] or None,
            platform=platform, platform_url=platform_url, embed_id=embed_id,
            suggested_by=suggested_by, user_key=key,
            session_id=session_id, approved=True,
        )
        db.session.add(song)
        db.session.commit()
        return jsonify({"success": True, "song_id": song.id}), 201

    @app.route("/api/v1/gym/<gym_token>/session/<int:session_id>/songs")
    def api_session_songs(gym_token, session_id):
        """API: pedidos de música da aula."""
        gym = Gym.query.filter_by(qr_code_token=gym_token).first()
        if not gym:
            return jsonify({"error": "Gym not found"}), 404
        vs = db.session.get(VoteSession, session_id)
        if not vs or vs.gym_id != gym.id:
            return jsonify({"error": "Session not found"}), 404
        songs = session_songs(gym.id, session_id)
        return jsonify({"songs": [song_to_dict(s) for s in songs]})

    @app.route("/api/v1/gym/<gym_token>/session/<int:session_id>/queue")
    def api_session_queue(gym_token, session_id):
        """API: fila atual (último mix) da aula — p/ o app mostrar 'tocando agora'."""
        gym = Gym.query.filter_by(qr_code_token=gym_token).first()
        if not gym:
            return jsonify({"error": "Gym not found"}), 404
        mix = (ClassMix.query.filter_by(gym_id=gym.id, session_id=session_id)
               .order_by(ClassMix.created_at.desc()).first())
        if not mix:
            return jsonify({"queue": None})
        items = ClassMixItem.query.filter_by(mix_id=mix.id).order_by(
            ClassMixItem.position.asc()).all()
        current = items[mix.current_index] if 0 <= mix.current_index < len(items) else None
        skip_total = skip_votes_count(mix.id, mix.current_index) if current else 0
        return jsonify({"queue": {
            "mix_id": mix.id, "title": mix.title,
            "current_index": mix.current_index,
            "skip_votes": skip_total,
            "skip_needed": gym.skip_votes_needed or 5,
            "items": [{
                "position": i.position, "kind": i.kind, "title": i.title,
                "artist": i.artist, "platform": i.platform,
                "platform_url": i.platform_url,
                "requested_by": i.requested_by,
                "duration_sec": i.duration_sec, "played": i.played,
            } for i in items],
            "now_playing": {
                "title": current.title, "artist": current.artist,
                "requested_by": current.requested_by, "kind": current.kind,
            } if current else None,
        }})

    @app.route("/api/v1/gym/<gym_token>/mix/preview", methods=["POST"])
    def api_mix_preview(gym_token):
        """API: simula o mix sem salvar (app mostra antes do coach confirmar)."""
        gym = Gym.query.filter_by(qr_code_token=gym_token).first()
        if not gym:
            return jsonify({"error": "Gym not found"}), 404
        data = request.get_json(silent=True) or {}
        session_id = to_int(data.get("session_id"))
        base_playlist_id = to_int(data.get("base_playlist_id"))
        target_minutes = data.get("target_minutes", 60)
        base_per_request = data.get("base_per_request", 3)
        seed = data.get("seed", 42)
        base_tracks = []
        if base_playlist_id:
            pl = db.session.get(Playlist, base_playlist_id)
            if not pl or pl.gym_id != gym.id:
                return jsonify({"error": "Playlist not found"}), 404
            base_tracks = PlaylistTrack.query.filter_by(playlist_id=pl.id).all()
        reqs = session_songs(gym.id, session_id) if session_id else []
        fair = fair_order_requests(reqs, seed=seed)
        queue, total = build_class_queue(base_tracks, fair, base_per_request,
                                         target_minutes, seed)
        return jsonify({"queue": queue, "total_sec": total,
                        "summary": queue_summary(queue)})

    @app.route("/api/v1/gym/<gym_token>/mix/<int:mix_id>/skip", methods=["POST"])
    def api_skip_vote(gym_token, mix_id):
        """API: votar para pular a faixa atual. Atingindo a meta, pula sozinho."""
        gym = Gym.query.filter_by(qr_code_token=gym_token).first()
        if not gym:
            return jsonify({"error": "Gym not found"}), 404
        mix = db.session.get(ClassMix, mix_id)
        if not mix or mix.gym_id != gym.id or not mix.is_active:
            return jsonify({"error": "Queue not found"}), 404
        n_items = ClassMixItem.query.filter_by(mix_id=mix.id).count()
        if not 0 <= mix.current_index < n_items:
            return jsonify({"error": "Queue finished"}), 400
        data = request.get_json(silent=True) or {}
        identifier = (data.get("user_identifier") or "").strip()
        if not identifier:
            return jsonify({"error": "user_identifier is required"}), 400
        pos = mix.current_index
        if SkipVote.query.filter_by(mix_id=mix.id, position=pos,
                                    user_identifier=identifier).first():
            return jsonify({"error": "Already voted to skip"}), 400
        from sqlalchemy.exc import IntegrityError
        try:
            db.session.add(SkipVote(
                mix_id=mix.id, position=pos, user_identifier=identifier,
                user_name=(data.get("user_name") or "Anônimo").strip()[:100]))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify({"error": "Already voted to skip"}), 400
        needed = gym.skip_votes_needed or 5
        total = skip_votes_count(mix.id, pos)
        skipped = False
        if total >= needed:
            skipped = advance_mix(mix)
        return jsonify({"success": True, "skipped": skipped,
                        "votes": total if not skipped else 0,
                        "needed": needed})

    @app.route("/api/v1/gym/<gym_token>/notifications")
    def api_notifications(gym_token):
        """API: avisos da academia (o app faz polling; base p/ push futuro).

        Query: ?since_id=N → só notificações com id maior que N.
        """
        gym = Gym.query.filter_by(qr_code_token=gym_token).first()
        if not gym:
            return jsonify({"error": "Gym not found"}), 404
        since_id = request.args.get("since_id", type=int) or 0
        notes = (Notification.query.filter_by(gym_id=gym.id)
                 .filter(Notification.id > since_id)
                 .order_by(Notification.id.asc()).limit(50).all())
        return jsonify({"notifications": [{
            "id": n.id, "title": n.title, "body": n.body,
            "session_id": n.session_id,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        } for n in notes]})


def register_errors(app: Flask):
    @app.errorhandler(404)
    def not_found(_e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found"}), 404
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def internal_error(_e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Internal server error"}), 500
        return render_template("500.html"), 500


# ---------------------------------------------------------------- run

def init_db(app=None):
    """Cria tabelas + academia demo (demo@gymbeats.com / demo123)."""
    app = app or create_app()
    with app.app_context():
        db.create_all()
        ensure_schema()
        if not Gym.query.first():
            demo = Gym(name="CrossFit Demo Box", email="demo@gymbeats.com",
                       qr_code_token=secrets.token_urlsafe(16))
            demo.set_password("demo123")
            db.session.add(demo)
            db.session.flush()
            demo_pl = Playlist(
                gym_id=demo.id, name="WOD Insano - Rock & Eletrônica",
                description="Playlist oficial demo",
                platform="youtube",
                platform_url="https://www.youtube.com/playlist?list=PLDemo123",
                embed_id="PLDemo123", created_by="Admin",
                category="wod", approved=True, is_official=True,
            )
            db.session.add(demo_pl)
            db.session.flush()
            for i, (t, a) in enumerate([
                ("Enter Sandman", "Metallica"),
                ("Lose Yourself", "Eminem"),
                ("Thunderstruck", "AC/DC"),
                ("Stronger", "Kanye West"),
            ]):
                db.session.add(PlaylistTrack(
                    playlist_id=demo_pl.id, gym_id=demo.id,
                    title=t, artist=a, platform="youtube",
                    platform_url=f"https://youtu.be/demo{i:02d}ABC_-",
                    embed_id=f"demo{i:02d}ABC_-", duration_sec=210,
                    added_by="Admin",
                ))
            db.session.commit()
            print("\n[OK] Academia demo criada! demo@gymbeats.com / demo123\n")


app = create_app()

if __name__ == "__main__":
    init_db(app)
    app.run(debug=True, host="0.0.0.0", port=5000)
