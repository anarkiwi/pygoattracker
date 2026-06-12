"""Fetch GoatTracker 2's example songs for tests.

The songs are GoatTracker distribution artifacts, so they are not
tracked in this repo; they are downloaded on demand from a pinned
GoatTracker 2 source mirror commit, verified by SHA-256, and cached
under ``tests/.fixture_cache``. Tests skip when offline.
"""

import hashlib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_COMMIT = "a78e7e5273bfbeb17c3777b1f5ceb63b8e6b4667"
_BASE_URL = f"https://raw.githubusercontent.com/leafo/goattracker2/{_COMMIT}/examples"
CACHE_DIR = Path(__file__).parent / ".fixture_cache"

EXAMPLE_SONGS = {
    "consultant.sng": (
        "8a4e114ee1fe9b132ae939dd6435a1fb980bec82ba20680721e33158d5f71fbe"
    ),
    "funktest.sng": (
        "6fc1b6eba487a88eff6316a40f3ea87e44abfecc6a44f6b4428f2f78c4b61a89"
    ),
    "dojo.sng": ("ea2d0be5ed1f86e9834ac1c5e297ce8c25940ad5f1d3c7d4793248bac4ec69f7"),
}


def example_song(name: str) -> bytes:
    """Bytes of a pinned GoatTracker example song (cached download)."""
    sha256 = EXAMPLE_SONGS[name]
    cached = CACHE_DIR / name
    if not cached.exists():
        try:
            with urllib.request.urlopen(f"{_BASE_URL}/{name}", timeout=30) as response:
                data = response.read()
        except (urllib.error.URLError, OSError) as exc:
            pytest.skip(f"cannot download example song {name}: {exc}")
        CACHE_DIR.mkdir(exist_ok=True)
        cached.write_bytes(data)
    data = cached.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        cached.unlink()
        raise AssertionError(f"{name}: checksum mismatch ({digest})")
    return data
