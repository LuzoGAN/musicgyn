# Deploy na VPS (nativo, sem Docker) — GymBeats em `/`, Flanelinha em `/flanelinha`
# Cenário real: Cloudflare Tunnel -> nginx:80 -> gunicorn. Sem Docker na VPS.

## 0. Backup (segurança, 1 min)

```bash
sudo cp /etc/nginx/sites-enabled/flanelinha ~/nginx-flanelinha.bak
sudo cp /etc/nginx/nginx.conf ~/nginx.conf.bak
```

## 1. Subir o código do GymBeats

```bash
sudo mkdir -p /var/www/gymbeats && sudo chown ubuntu:www-data /var/www/gymbeats
# do seu PC: copie TUDO do projeto musicgyn (menos venv, __pycache__, *.db)
scp -r C:\Users\Desk\PycharmProjects\musicgyn\* ubuntu@136.248.105.200:/var/www/gymbeats/
# (ou git clone, se o repo estiver no GitHub)

cd /var/www/gymbeats
sudo apt install -y python3-venv
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
python3 -c "import secrets; print(secrets.token_hex(32))"  # gere o segredo
nano .env
```
`.env`:
```
SECRET_KEY=<cole o segredo gerado>
SESSION_COOKIE_NAME=gymbeats_session
CREATE_DEMO=1
```
```bash
chmod 600 .env
sudo chown -R ubuntu:www-data /var/www/gymbeats
sudo chmod -R g+rX /var/www/gymbeats   # nginx precisa ler static/
```

## 2. Serviço systemd do GymBeats

```bash
sudo cp /var/www/gymbeats/deploy/gymbeats.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gymbeats
sudo systemctl status gymbeats --no-pager | head -8
curl -s http://127.0.0.1:5000/api/v1/health   # {"status":"ok",...}
```

## 3. Mover o Flanelinha para /flanelinha (2 edições pequenas)

**3a.** Em `/var/www/flanelinha/Flanelinha/run.py`, logo após `app = create_app()`:
```python
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


class SubPathMiddleware:
    """Prefixo fixo: o gunicorn descarta o header X-Script-Name."""
    def __init__(self, app, prefix="/flanelinha"):
        self.app = app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        environ["SCRIPT_NAME"] = self.prefix
        return self.app(environ, start_response)


app.wsgi_app = SubPathMiddleware(app.wsgi_app)
```

**3b.** Em `/var/www/flanelinha/Flanelinha/app/config.py`, na classe `Config`:
```python
SESSION_COOKIE_NAME = "flanelinha_session"
SESSION_COOKIE_PATH = "/flanelinha"
```
(Sem isso o login dos dois apps no mesmo domínio se atropela.)

```bash
sudo systemctl restart flanelinha
sudo systemctl status flanelinha --no-pager | head -5
```

## 4. Nginx: novo roteamento + esquema https real

**4a.** Adicione o `map` no `/etc/nginx/nginx.conf`, dentro do bloco `http {}`:
```nginx
map $http_x_forwarded_proto $edge_proto {
    default $http_x_forwarded_proto;
    ""      "https";
}
```
**4b.** Substitua o site pelo `deploy/ozul-nginx.conf`:
```bash
sudo cp /var/www/gymbeats/deploy/ozul-nginx.conf /etc/nginx/sites-enabled/flanelinha
sudo nginx -t && sudo systemctl reload nginx
```

## 5. Testar

```bash
curl -sI https://ozul.com.br | head -3                    # 200
curl -s https://ozul.com.br/api/v1/health                 # {"status":"ok",...}
curl -sI https://ozul.com.br/flanelinha | head -3         # 301 -> /flanelinha/
curl -sI https://ozul.com.br/flanelinha/ | head -3        # 200 (tela de login)
```
No navegador: `/` abre o GymBeats, `/flanelinha/` o Flanelinha (login admin continua igual).

## 6. Memória: folga + swap (a VPS está SEM swap!)

Conta: sistema ~390MB + GymBeats ~120MB + resto igual = ~550MB de ~956MB. OK,
mas crie swap p/ picos:
```bash
sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -m
```
Opcional (libera ~60MB): reduza o Flanelinha de 3 para 2 workers no service dele.

## 7. Rollback (se algo der errado)

```bash
sudo cp ~/nginx-flanelinha.bak /etc/nginx/sites-enabled/flanelinha
# reverta as 2 edições do passo 3 (ou restaure backup do código)
sudo systemctl restart flanelinha && sudo systemctl reload nginx
sudo systemctl stop gymbeats   # opcional
```

## 8. Operação do dia a dia

```bash
sudo systemctl status gymbeats         # saúde
sudo journalctl -u gymbeats -f         # logs ao vivo
sudo systemctl restart gymbeats        # após atualizar código (.env não mexe)
```
Deploy de versão nova do GymBeats = copiar arquivos + `restart`. Banco em
`/var/www/gymbeats/gymbeats.db` (SQLite). Backup: `cp gymbeats.db gymbeats-$(date +%F).db`.
