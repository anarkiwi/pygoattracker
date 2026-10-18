"""Fetch GoatTracker 2 and NinjaTracker 2 example songs for tests.

The songs are tracker distribution artifacts, so they are not tracked
in this repo; they are downloaded on demand from pinned source mirror
commits, verified by SHA-256, and cached under
``tests/.fixture_cache``. Tests skip when offline. The NinjaTracker
example tunes live on the editor's .d64 disk image, so a minimal 1541
filesystem reader extracts them.
"""

import hashlib
import io
import os
import urllib.error
import urllib.request
import zipfile
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

_GT1_URL = "https://cadaver.github.io/tools"
GT1_ARCHIVES = {
    "goattrk.zip": "f12df334df5b1749fa0660575d63520a5e064ed254e4390cede6b03f9808ecef",
    "goatold.zip": "ef4ab3287bbb60be5cf5f323518a630e392650516bc23510b6a821acd3702233",
}

# GoatTracker V1.25 editor example songs (musicroutine 11.x layout).
GOATOLD_SONGS = {
    "BOFH.SNG": "b5042f224a178013240655badf406547ec55e7aad8fabd0a9d74808a9cc2ba26",
    "DRUMTEST.SNG": "cfb0defe77036d8572d3addb6a6e8b3e2ac87ddb0ac65334e6f24aada13e4b90",
    "EXAMPLE.SNG": "d7860d64b0d82d670b26a2c3ab9e5ab510cccce7523f2399664dda6533a0afa2",
    "GOATCLAS.SNG": "99cb99aab743dd2c210edb90d5c7fbbbaebf128bb05ca58dcde9cfeb7372bec9",
    "NINTENDO.SNG": "2f6a09895429771472051359bf4521244fefbe1ab9b7c587a26b7d6bdb59c298",
    "TIMETEST.SNG": "31d4cd23c0c8732995dda81c6bd7e0eaea8defc2f71b6b67aa7e64982e58de0f",
}

# GoatTracker V1.53 editor example songs (256-byte filtertable layout).
GOATTRK_SONGS = {
    "examples/2x_speed.sng": (
        "6f0b53890d34af40feb582c1965077e0aa62626105a28b19bae7c33b4a42f501"
    ),
    "examples/dojo.sng": (
        "cee3451227d8cda831033af98d8c2dd4b73900a7c937c59bf3ee17571fc586bd"
    ),
    "examples/funktest.sng": (
        "76424e6ac976530e50327a87fda685c0c7573fe1089794a8e179b8e4280624bc"
    ),
    "examples/loader.sng": (
        "30d62f3c3752ddbaa658c33c68b4f424029b5b5656ae029bb7ce36eba8cc0757"
    ),
    "examples/mwremix.sng": (
        "1f84d1673cbc7b4cb7e4b1e05f071120904bb282a057cc43f64a958e9790c56a"
    ),
    "examples/nintendo.sng": (
        "e652f7b206a74087500e4097f327b9d7d254389c3a0d15845e1dab7f21bb1302"
    ),
}

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
        tmp = cached.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, cached)
    data = cached.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        cached.unlink()
        raise AssertionError(f"{name}: checksum mismatch ({digest})")
    return data


def example_song(name: str) -> bytes:
    """Bytes of a pinned GoatTracker example song (cached download)."""
    return _download(name, f"{_BASE_URL}/{name}", EXAMPLE_SONGS[name])


def gt1_archive(name: str) -> bytes:
    """Bytes of a pinned GoatTracker V1.x release archive (cached download)."""
    return _download(name, f"{_GT1_URL}/{name}", GT1_ARCHIVES[name])


def _archive_member(archive: str, member: str, sha256: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(gt1_archive(archive))) as zin:
        payload = zin.read(member)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != sha256:
        raise AssertionError(f"{archive}:{member}: checksum mismatch ({digest})")
    return payload


def goatold_song(name: str) -> bytes:
    """Bytes of a pinned GoatTracker V1.25 example song (goatold.zip)."""
    return _archive_member("goatold.zip", f"EXAMPLES/{name}", GOATOLD_SONGS[name])


def goattrk_song(member: str) -> bytes:
    """Bytes of a pinned GoatTracker V1.53 example song (goattrk.zip)."""
    return _archive_member("goattrk.zip", member, GOATTRK_SONGS[member])


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
