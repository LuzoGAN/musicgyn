# Imagem pequena (slim). Python 3.12 tem wheel pronta p/ tudo do requirements.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependências primeiro (aproveita cache do Docker a cada build)
COPY requirements.txt .
RUN pip install -r requirements.txt

# Código (banco vai em volume, fora da imagem)
COPY app.py config.py models.py utils.py mix_logic.py importers.py wsgi.py ./
COPY templates/ ./templates/
COPY static/ ./static/

# Usuário sem root + pasta do banco
RUN useradd -m appuser && mkdir -p /data && chown appuser:appuser /data
USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/v1/health')"

# 2 workers aguentam bem o tráfego de portfólio/aula; sqlite em volume
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", \
     "--threads", "2", "--timeout", "60", \
     "--access-logfile", "-", "--error-logfile", "-", "wsgi:app"]
