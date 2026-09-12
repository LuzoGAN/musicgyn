"""Entrada WSGI p/ produção (gunicorn) atrás de proxy reverso (nginx).

O ProxyFix faz o Flask enxergar o IP, esquema https e prefixo reais
vindos dos headers que o nginx envia — sem isso, redirects e URLs
externas (ex: QR Code) sairiam como http:// errados.
"""
import os

from dotenv import load_dotenv

load_dotenv()

from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

from app import create_app, init_db  # noqa: E402

app = create_app()
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Garante tabelas + migrações leves + academia demo UMA vez no boot
# (via ExecStartPre com BOOT_INIT=1). Workers do gunicorn NÃO repetem isso:
# SQLite com 2 processos criando tabelas juntos dá "database is locked".
if os.environ.get("BOOT_INIT", "") == "1":
    with app.app_context():
        from models import db  # noqa: E402
        from app import ensure_schema  # noqa: E402
        db.create_all()
        ensure_schema()
        if os.environ.get("CREATE_DEMO", "1") == "1":
            init_db(app)
