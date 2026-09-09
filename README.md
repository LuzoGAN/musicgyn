# 🎵 GymBeats — Playlist Colaborativa para Academias

Web em **Flask (Python) + HTML + CSS** com **API REST pronta para iOS/Android**.
Tema padrão **preto + vermelho**, com cores personalizáveis por academia.

## Funcionalidades
- 👤 Cadastro/login da academia (dona da conta)
- 🎨 Cores + logo personalizáveis (página do aluno herda as cores)
- 📱 QR Code da academia (aluno escaneia e vota, sem instalar nada)
- 🗳️ Sessões de votação por aula (ex: "WOD Quarta 18h") com prazo
- 🎵 Sugestão de playlists: YouTube, Spotify, Apple Music, SoundCloud, Deezer
- ✅ Moderação: sugestão do aluno entra como pendente
- 🎵 Sugestão de música avulsa (coach monta a playlist)
- ⭐ Playlists oficiais da casa
- 🏆 Ranking ao vivo + vencedora + histórico (vezes tocada)
- 📊 Dashboard com estatísticas
- 🔌 API `/api/v1/` para apps mobile

## Rodar
```bash
pip install -r requirements.txt
python app.py
# abre http://127.0.0.1:5000
# demo: demo@gymbeats.com / demo123
```

## Testes
```bash
pytest -v
```

## API mobile (contrato)
- `GET /api/v1/health`
- `GET /api/v1/gym/<token>/info`
- `GET /api/v1/gym/<token>/playlists`
- `GET /api/v1/gym/<token>/active-session`
- `GET /api/v1/gym/<token>/session/<id>/results`
- `POST /api/v1/gym/<token>/vote` → `{session_id, playlist_id, user_identifier, user_name?}`
- `POST /api/v1/gym/<token>/suggest` → `{name, platform, platform_url, suggested_by?, category?}`
- `POST /api/v1/gym/<token>/request-song` → `{title, artist?, platform, platform_url, suggested_by?, session_id?}`
- `GET /api/v1/gym/<token>/session/<id>/songs` → pedidos da aula
- `GET /api/v1/gym/<token>/session/<id>/queue` → fila + tocando agora
- `POST /api/v1/gym/<token>/mix/preview` → simula o mix sem salvar
- `POST /api/v1/gym/<token>/mix/<mix_id>/skip` → `{user_identifier, user_name?}` (pular democrático)
- `GET /api/v1/gym/<token>/notifications?since_id=N` → avisos (polling p/ push futuro)

## Modo Aula (fase 2)
1. Academia cadastra a **playlist base por link** e **importa as faixas** (botão na
   playlist — só o necessário p/ o tempo da aula, 10–20 faixas) ou adiciona manualmente.
2. Alunos **pedem músicas** para a aula (página via QR ou app).
3. Coach gera o **mix**: N faixas da base + 1 pedido, com **sorteio justo** —
   pedidos intercalados por pessoa (round-robin + aleatório), então quem pediu
   10 músicas não domina a fila; o corte respeita a duração da aula (padrão 60 min).
4. **Player** mostra tocando agora, fila, anterior/próxima e reiniciar.

## Engajamento da aula
- **Limite por aluno** (padrão 3 pedidos/aula, ajustável em Configurações) — evita flood.
- **Pular democrático**: alunos votam p/ pular a faixa atual; batendo a meta (padrão
  5 votos, ajustável), pula sozinho. Coach vê o placar no player.
- **Modo TV** (`/tv/<token>`): telão com tocando agora, próximos, placar de pular e
  QR Code p/ pedir — atualiza sozinho a cada 10s.
- **Notificações**: ao abrir votação, cria aviso exibido na página do aluno e servido
  em `GET /api/v1/.../notifications?since_id=N` (polling; base pronta p/ FCM/APNs).

### Importação automática do link
- **YouTube**: funciona sem configuração (lê a página pública). Com `YOUTUBE_API_KEY`
  no `.env`, importa títulos e durações exatas via Data API v3.
- **Spotify**: exige `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET` (playlist pública).
- **Apple Music / SoundCloud / Deezer**: cadastrar faixas manualmente.
- O limite é calculado pelo tempo da aula (ex: 60 min, 3+1 → ~13 faixas) — o mix
  repete a base em loop, então playlist de 200 músicas não precisa vir inteira.

## Estrutura
```
app.py  config.py  models.py  utils.py
templates/  static/css/  static/js/
tests/ (unitários + integração web + API)
```
