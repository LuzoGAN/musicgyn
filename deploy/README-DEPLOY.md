# Deploy ozul.com.br — GymBeats na raiz + Flanelinha em /flanelinha
# VPS Oracle 1GB: tudo leve, sem container de proxy (usa o nginx do host).

## 0. Descobrir o cenário atual (rode na VPS e me mande a saída)

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}'
docker compose ls
ss -tlnp | grep -E ':80|:443|:5000|:8000' || netstat -tlnp | grep -E ':80|:443|:5000|:8000'
systemctl is-active nginx; nginx -v 2>&1 | head -1
ls /etc/nginx/sites-enabled/ 2>/dev/null
```

## 1. Preparar pastas na VPS

```bash
sudo mkdir -p /opt/sites && sudo chown $USER:$USER /opt/sites
cd /opt/sites
git clone <seu-repo-gymbeats> gymbeats      # ou scp/rsync daqui
cp -r /caminho/do/Flanelinha_backup flanelinha   # o código que hoje roda no docker
```

## 2. Flanelinha: 3 ajustes (uma vez só)

1. Copie `deploy/flanelinha.Dockerfile` → `flanelinha/Dockerfile`
2. Copie `deploy/flanelinha-wsgi.py` → `flanelinha/wsgi.py`
3. Em `flanelinha` (config ou `.env` do app), garanta cookies próprios:
   ```python
   SESSION_COOKIE_NAME = "flanelinha_session"
   SESSION_COOKIE_PATH = "/flanelinha"
   ```
   Sem isso, o login dos dois apps no mesmo domínio se atropela.

## 3. Segredo do GymBeats

```bash
cd /opt/sites/gymbeats/deploy
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"  # cole em SECRET_KEY no .env
```

## 4. Subir os containers

```bash
# GymBeats primeiro (Flanelinha continua como está até o passo 5)
docker compose -f /opt/sites/gymbeats/deploy/docker-compose.yml up -d --build
docker ps --format 'table {{.Names}}\t{{.Status}}'
curl -s http://127.0.0.1:5000/api/v1/health
# quando o Flanelinha estiver com Dockerfile: descomente o bloco dele no yml e repita o up
```

## 5. Nginx do host: adicionar os 2 blocos

```bash
sudo nano /etc/nginx/sites-enabled/ozul   # arquivo do ozul.com.br (porta 443)
# cole o conteúdo de deploy/nginx-ozul-snippet.conf DENTRO do server { listen 443 ... }
sudo nginx -t && sudo systemctl reload nginx
```

Teste: `https://ozul.com.br` (GymBeats) e `https://ozul.com.br/flanelinha/` (Flanelinha).
Novo projeto futuro = copiar o bloco do flanelinha mudando o final + 1 serviço no compose.

## 6. Conta de memória (1GB dá tranquilo)

| Item | RAM aprox. |
|---|---|
| Sistema + Docker | ~300 MB |
| nginx (host) | ~10 MB |
| GymBeats (2 workers) | ~150 MB |
| Flanelinha (2 workers) | ~180 MB |
| **Total** | **~640 MB** |

Recomendações: os `mem_limit: 256m` do compose já evitam que um app coma a VPS;
crie 1–2GB de swap (imagem Oracle geralmente vem sem):
```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && \
sudo mkswap /swapfile && sudo swapon /swapfile && \
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```
Acompanhe com `docker stats --no-stream`.

## 7. Operação

```bash
docker compose -f /opt/sites/gymbeats/deploy/docker-compose.yml logs -f gymbeats
docker compose -f /opt/sites/gymbeats/deploy/docker-compose.yml up -d --build gymbeats  # redeploy
docker compose -f /opt/sites/gymbeats/deploy/docker-compose.yml down   # parar tudo
```

Backup: os bancos ficam nos volumes `gymbeats-data` e `flanelinha-data`
(`docker volume ls`). Para backup rápido:
```bash
docker run --rm -v gymbeats-data:/d -v $PWD:/b alpine tar czf /b/gymbeats-data.tgz -C /d .
```
