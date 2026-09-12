# Copiar o CONTEÚDO para a RAIZ do projeto Flanelinha como "wsgi.py",
# ou aplicar as 2 partes no run.py (que o gunicorn usa: run:app).
#
# ATENÇÃO: o gunicorn descarta o header X-Script-Name (tem underscore) antes
# de chegar ao Flask — por isso o prefixo vai FIXO no middleware abaixo
# (o app só é servido em /flanelinha, então é sempre correto).
from werkzeug.middleware.proxy_fix import ProxyFix

from app import create_app

app = create_app()
# Protocolo/IP/host reais vindos do nginx (headers com hífen passam OK)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


class SubPathMiddleware:
    """Fixa o SCRIPT_NAME: gera URLs e redirects com /flanelinha/..."""

    def __init__(self, app, prefix="/flanelinha"):
        self.app = app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        environ["SCRIPT_NAME"] = self.prefix
        return self.app(environ, start_response)


app.wsgi_app = SubPathMiddleware(app.wsgi_app)

if __name__ == "__main__":
    app.run()
