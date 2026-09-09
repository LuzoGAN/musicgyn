"""Testes unitários: utils (integração com plataformas de música)."""
from utils import (extract_platform_id, extract_spotify_id,
                   extract_youtube_id, is_valid_color)


def test_youtube_watch():
    assert extract_youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_youtube_short():
    assert extract_youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_youtube_playlist():
    assert extract_youtube_id("https://www.youtube.com/playlist?list=PL123abc") == "PL123abc"


def test_youtube_invalid():
    assert extract_youtube_id("https://example.com/nada") is None
    assert extract_youtube_id("") is None
    assert extract_youtube_id(None) is None


def test_spotify_playlist():
    assert extract_spotify_id("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M") == \
        "playlist/37i9dQZF1DXcBWIGoYBM5M"


def test_spotify_track():
    assert extract_spotify_id("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQ?si=x") == \
        "track/4uLU6hMCjMI75M1A2tKUQ"


def test_platform_dispatch():
    assert extract_platform_id("youtube", "https://youtu.be/abc123XYZ_-") == "abc123XYZ_-"
    assert extract_platform_id("spotify", "https://open.spotify.com/playlist/ABC123") == "playlist/ABC123"
    assert extract_platform_id("deezer", "https://www.deezer.com/playlist/123") == \
        "https://www.deezer.com/playlist/123"
    assert extract_platform_id("soundcloud", "not-a-url") is None
    assert extract_platform_id("desconhecida", "https://x.com") is None


def test_colors():
    assert is_valid_color("#e50914")
    assert is_valid_color("#fff")
    assert not is_valid_color("e50914")
    assert not is_valid_color("#zzzzzz")
    assert not is_valid_color(None)
