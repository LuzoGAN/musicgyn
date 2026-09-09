"""Utilitários: extração de IDs de plataformas de música e QR Code."""
import base64
import re
from io import BytesIO

import qrcode
import qrcode.image.svg


def extract_youtube_id(url: str | None) -> str | None:
    """Extrai ID de vídeo ou playlist do YouTube."""
    if not url:
        return None
    patterns = [
        r"(?:youtube\.com\/watch\?v=|youtu\.be\/)([^&\n?\"'#]+)",
        r"youtube\.com\/playlist\?list=([^&\n?\"'#]+)",
        r"youtube\.com\/embed\/([^&\n?\"'#]+)",
        r"music\.youtube\.com\/watch\?v=([^&\n?\"'#]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def extract_spotify_id(url: str | None) -> str | None:
    """Extrai 'tipo/id' de URL do Spotify (playlist/track/album)."""
    if not url:
        return None
    match = re.search(r"spotify\.com\/(playlist|track|album)\/([a-zA-Z0-9]+)", url)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    return None


def extract_platform_id(platform: str, url: str) -> str | None:
    """Extrai o embed_id conforme a plataforma.

    Para apple_music/soundcloud/deezer aceitamos a URL como embed
    (player via link) desde que seja http(s).
    """
    if platform == "youtube":
        return extract_youtube_id(url)
    if platform == "spotify":
        return extract_spotify_id(url)
    if platform in ("apple_music", "soundcloud", "deezer"):
        if url and re.match(r"^https?://", url.strip()):
            return url.strip()
        return None
    return None


def is_valid_color(value: str | None) -> bool:
    """Valida cor hexadecimal #rgb ou #rrggbb."""
    if not value:
        return False
    return re.match(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$", value.strip()) is not None


def is_http_url(value: str | None) -> bool:
    """Só permite links http(s) — bloqueia javascript:/data: (XSS em href)."""
    if not value:
        return False
    return re.match(r"^https?://[^\s]+$", value.strip(), re.IGNORECASE) is not None


def generate_qr_code(data: str) -> str:
    """Gera QR Code SVG em base64."""
    factory = qrcode.image.svg.SvgImage
    img = qrcode.make(data, image_factory=factory)
    buffer = BytesIO()
    img.save(buffer)
    return base64.b64encode(buffer.getvalue()).decode()
