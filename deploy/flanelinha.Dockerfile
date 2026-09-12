# Copiar para a RAIZ do projeto Flanelinha como "Dockerfile".
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requeriments.txt .
RUN pip install --no-cache-dir -r requeriments.txt \
    && pip install --no-cache-dir gunicorn

COPY . .

RUN mkdir -p /app/instance /app/logs

EXPOSE 8000

# 2 workers: suficiente p/ portfólio e leve p/ 1GB de RAM
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", \
     "--threads", "2", "--timeout", "120", \
     "--access-logfile", "-", "--error-logfile", "-", "wsgi:app"]
