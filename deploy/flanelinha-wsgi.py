# Copiar para a RAIZ do projeto Flanelinha como "wsgi.py".
# O ProxyFix com x_prefix=1 lê o header X-Script-Name que o nginx envia,
# então url_for e redirects passam a gerar /flanelinha/... automaticamente.
from werkzeug.middleware.proxy_fix import ProxyFix

from app import create_app

app = create_app()
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

if __name__ == "__main__":
    app.run()
