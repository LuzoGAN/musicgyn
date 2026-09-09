"""Importação de faixas a partir do link da playlist.

Como funciona por plataforma:
- YouTube: sem chave de API, fazemos leitura best-effort da página pública
  da playlist (extrai os videoIds em ordem). Com YOUTUBE_API_KEY configurada,
  usamos a Data API v3 (títulos + durações reais, com paginação).
- Spotify: exige SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET (fluxo Client
  Credentials, só lê playlists públicas). Sem credenciais, retornamos erro
  explicativo em vez de falhar silenciosamente.
- Apple Music / SoundCloud / Deezer: não há API pública simples sem
  credenciais — orientamos cadastrar as faixas manualmente.

Tudo usa apenas a biblioteca padrão (urllib) para não adicionar dependências.
"""

import base64
import json
import math
import re
import urllib.parse
import urllib.request
from datetime import timedelta

AVG_TRACK_SEC = 210  # estimativa quando não sabemos a duração real
MIN_IMPORT = 10
MAX_IMPORT = 20


def suggest_import_limit(target_minutes=60, base_per_request=3,
                         avg_sec=AVG_TRACK_SEC,
                         min_tracks=MIN_IMPORT, max_tracks=MAX_IMPORT):
    """Quantas faixas da base importar p/ encher a aula (10-20 por padrão).

    Ex: aula de 60 min, proporção 3 base + 1 pedido → a base cobre ~75% do
    tempo → 2700s / 210s ≈ 13 faixas. O mix completa o resto ciclando a base
    em loop e intercalando os pedidos, então não precisa importar tudo.
    """
    try:
        bpr = int(base_per_request)
    except (TypeError, ValueError):
        bpr = 3
    if bpr <= 0:
        return 0
    try:
        target = int(target_minutes)
    except (TypeError, ValueError):
        target = 60
    try:
        avg = int(avg_sec) or AVG_TRACK_SEC
    except (TypeError, ValueError):
        avg = AVG_TRACK_SEC
    share = bpr / (bpr + 1)
    needed = math.ceil(target * 60 * share / avg)
    return max(min_tracks, min(max_tracks, needed))


def _http_get(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or {
        "User-Agent": "Mozilla/5.0 (GymBeats importer)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _http_post(url, data, headers=None, timeout=15):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_iso8601_duration(value):
    """PT4M13S -> segundos. Retorna None se inválido."""
    if not value:
        return None
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value.strip())
    if not m:
        return None
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    total = h * 3600 + mi * 60 + s
    return total if total > 0 else None


# ---------------------------------------------------------------- YouTube

def import_youtube_playlist(url, max_tracks=15, api_key=None):
    """Retorna [{'title','artist','platform_url','embed_id','duration_sec'}].

    Levanta ValueError se o link não for de playlist/vídeo válido.
    """
    from utils import extract_youtube_id  # import local p/ evitar ciclo
    pid = extract_youtube_id(url or "")
    if not pid:
        raise ValueError("Link do YouTube inválido.")
    if "list=" not in (url or ""):
        # link de vídeo avulso (não playlist): importa só essa faixa
        return [{"title": "Faixa da playlist", "artist": None,
                 "platform_url": f"https://youtu.be/{pid}",
                 "embed_id": pid, "duration_sec": None}]
    if api_key:
        return _youtube_via_api(pid, max_tracks, api_key)
    return _youtube_via_page(url, pid, max_tracks)


def _youtube_via_page(page_url, playlist_id, max_tracks):
    html = _http_get(page_url if "list=" in (page_url or "")
                     else f"https://www.youtube.com/playlist?list={playlist_id}")
    ids = []
    for vid in re.findall(r'"videoId"\s*:\s*"([a-zA-Z0-9_-]{11})"', html):
        if vid not in ids:
            ids.append(vid)
        if len(ids) >= max_tracks:
            break
    if not ids:
        # fallback: links /watch?v= presentes no HTML
        for vid in re.findall(r"watch\?v=([a-zA-Z0-9_-]{11})", html):
            if vid not in ids:
                ids.append(vid)
            if len(ids) >= max_tracks:
                break
    if not ids:
        raise ValueError("Não consegui ler essa playlist sem API. "
                         "Configure YOUTUBE_API_KEY ou cadastre as faixas manualmente.")
    return [{"title": f"Faixa {i + 1} da playlist", "artist": None,
             "platform_url": f"https://youtu.be/{vid}",
             "embed_id": vid, "duration_sec": None}
            for i, vid in enumerate(ids[:max_tracks])]


def _youtube_via_api(playlist_id, max_tracks, api_key):
    items, page_token = [], None
    while len(items) < max_tracks:
        params = {"part": "contentDetails,snippet", "playlistId": playlist_id,
                  "maxResults": min(50, max_tracks - len(items)), "key": api_key}
        if page_token:
            params["pageToken"] = page_token
        data = json.loads(_http_get(
            "https://www.googleapis.com/youtube/v3/playlistItems?"
            + urllib.parse.urlencode(params)))
        if "error" in data:
            raise ValueError("YouTube API: %s" % data["error"].get("message", "erro"))
        for it in data.get("items", []):
            vid = (it.get("contentDetails") or {}).get("videoId")
            title = (it.get("snippet") or {}).get("title") or f"Faixa {len(items) + 1}"
            if vid:
                items.append({"video_id": vid, "title": title})
            if len(items) >= max_tracks:
                break
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    if not items:
        raise ValueError("Playlist vazia ou privada.")
    # durações reais em lote de 50
    durations = {}
    for i in range(0, len(items), 50):
        chunk = ",".join(x["video_id"] for x in items[i:i + 50])
        data = json.loads(_http_get(
            "https://www.googleapis.com/youtube/v3/videos?"
            + urllib.parse.urlencode({"part": "contentDetails", "id": chunk,
                                      "key": api_key})))
        for it in data.get("items", []):
            durations[it.get("id")] = _parse_iso8601_duration(
                (it.get("contentDetails") or {}).get("duration"))
    return [{"title": x["title"][:200], "artist": None,
             "platform_url": f"https://youtu.be/{x['video_id']}",
             "embed_id": x["video_id"],
             "duration_sec": durations.get(x["video_id"])}
            for x in items]


# ---------------------------------------------------------------- Spotify

def import_spotify_playlist(url, max_tracks=15, client_id=None, client_secret=None):
    """Lê playlist pública via Client Credentials. Exige credenciais."""
    if not client_id or not client_secret:
        raise ValueError("Spotify exige SPOTIFY_CLIENT_ID e SPOTIFY_CLIENT_SECRET "
                         "no servidor. Sem isso, cadastre as faixas manualmente "
                         "ou use playlist do YouTube.")
    m = re.search(r"spotify\.com\/(playlist|album)\/([a-zA-Z0-9]+)", url or "")
    if not m:
        raise ValueError("Link do Spotify inválido (use playlist ou álbum público).")
    kind, sid = m.group(1), m.group(2)
    token = _spotify_token(client_id, client_secret)
    headers = {"Authorization": f"Bearer {token}"}
    tracks, offset = [], 0
    while len(tracks) < max_tracks:
        limit = min(50, max_tracks - len(tracks))
        data = json.loads(_http_get(
            f"https://api.spotify.com/v1/{kind}s/{sid}/tracks"
            f"?limit={limit}&offset={offset}", headers=headers))
        items = data.get("items", [])
        if not items:
            break
        for it in items:
            t = it.get("track") if kind == "playlist" else it
            if not t or not t.get("id"):
                continue
            artists = ", ".join(a.get("name", "") for a in t.get("artists", []))
            tracks.append({
                "title": (t.get("name") or "Faixa")[:200],
                "artist": artists[:200] or None,
                "platform_url": (t.get("external_urls") or {}).get("spotify")
                                or f"https://open.spotify.com/track/{t['id']}",
                "embed_id": f"track/{t['id']}",
                "duration_sec": (t.get("duration_ms") or 0) // 1000 or None,
            })
            if len(tracks) >= max_tracks:
                break
        offset += len(items)
        if len(items) < limit:
            break
    if not tracks:
        raise ValueError("Playlist vazia ou privada.")
    return tracks


def _spotify_token(client_id, client_secret):
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = json.loads(_http_post(
        "https://accounts.spotify.com/api/token",
        {"grant_type": "client_credentials"},
        {"Authorization": f"Basic {basic}"}))
    if "access_token" not in data:
        raise ValueError("Spotify: falha na autenticação (confira ID/secret).")
    return data["access_token"]


# ---------------------------------------------------------------- dispatcher

IMPORTERS = {
    "youtube": import_youtube_playlist,
    "spotify": import_spotify_playlist,
}


def import_playlist_tracks(platform, url, max_tracks=15, settings=None):
    """Importa até max_tracks faixas do link. settings: dict com credenciais."""
    settings = settings or {}
    if platform == "youtube":
        return import_youtube_playlist(url, max_tracks,
                                       api_key=settings.get("YOUTUBE_API_KEY"))
    if platform == "spotify":
        return import_spotify_playlist(url, max_tracks,
                                       client_id=settings.get("SPOTIFY_CLIENT_ID"),
                                       client_secret=settings.get("SPOTIFY_CLIENT_SECRET"))
    raise ValueError(
        f"Importação automática não suportada para {platform}. "
        "Cadastre as faixas manualmente pelos links individuais.")
