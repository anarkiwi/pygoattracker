"""Fetch GoatTracker 2 and NinjaTracker 2 example songs for tests.

The songs are tracker distribution artifacts, so they are not tracked
in this repo; they are downloaded on demand from pinned source mirror
commits, verified by SHA-256, and cached under
``tests/.fixture_cache``. Tests skip when offline. The NinjaTracker
example tunes live on the editor's .d64 disk image, so a minimal 1541
filesystem reader extracts them.
"""

import hashlib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_COMMIT = "a78e7e5273bfbeb17c3777b1f5ceb63b8e6b4667"
_BASE_URL = f"https://raw.githubusercontent.com/leafo/goattracker2/{_COMMIT}/examples"
_NT2_COMMIT = "8da3d4b9c24fd20cfbd0a669a2030c3f37998085"
_NT2_D64_URL = (
    "https://raw.githubusercontent.com/localhost/NinjaTracker/"
    f"{_NT2_COMMIT}/ninjatr2.d64"
)
_NT2_D64_SHA256 = "440624b88db8361251bce0729b383bf3737161b9b87e406fcfef6191829a6002"
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

NT2_EXAMPLE_SONGS = (
    "CONSULTANT",
    "TRANSYLVANIAN",
    "MW4TITLE",
    "TRUEOLDSKOOL",
    "EFNCOLD",
    "FIGHTMACHINE",
)

# The two example tunes whose pattern slots carry no stale editor
# bytes, so a rewrite reproduces them byte for byte.
NT2_CLEAN_SONGS = ("CONSULTANT", "TRANSYLVANIAN")


def _download(name: str, url: str, sha256: str) -> bytes:
    cached = CACHE_DIR / name
    if not cached.exists():
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read()
        except (urllib.error.URLError, OSError) as exc:
            pytest.skip(f"cannot download {name}: {exc}")
        CACHE_DIR.mkdir(exist_ok=True)
        cached.write_bytes(data)
    data = cached.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        cached.unlink()
        raise AssertionError(f"{name}: checksum mismatch ({digest})")
    return data


def example_song(name: str) -> bytes:
    """Bytes of a pinned GoatTracker example song (cached download)."""
    return _download(name, f"{_BASE_URL}/{name}", EXAMPLE_SONGS[name])


def _sectors_per_track(track: int) -> int:
    if track <= 17:
        return 21
    if track <= 24:
        return 19
    if track <= 30:
        return 18
    return 17


def _d64_sector(image: bytes, track: int, sector: int) -> bytes:
    offset = sum(_sectors_per_track(num) for num in range(1, track)) + sector
    return image[offset * 256 : offset * 256 + 256]


def _d64_files(image: bytes) -> dict:
    files = {}
    track, sector = 18, 1
    while track:
        directory = _d64_sector(image, track, sector)
        for num in range(8):
            entry = directory[2 + num * 32 :][:30]
            if entry[0] & 0x80 and entry[0] & 0x07 in (1, 2):
                name = entry[3:19].rstrip(b"\xa0").decode("latin-1")
                files[name] = (entry[1], entry[2])
        track, sector = directory[0], directory[1]
    return files


def _d64_extract(image: bytes, track: int, sector: int) -> bytes:
    out = bytearray()
    while track:
        data = _d64_sector(image, track, sector)
        track, sector = data[0], data[1]
        out += data[2 : sector + 1] if track == 0 else data[2:]
    return bytes(out)


def nt2_example_song(name: str) -> bytes:
    """Bytes of a NinjaTracker 2 example tune from the pinned .d64."""
    image = _download("ninjatr2.d64", _NT2_D64_URL, _NT2_D64_SHA256)
    track, sector = _d64_files(image)[name]
    return _d64_extract(image, track, sector)
