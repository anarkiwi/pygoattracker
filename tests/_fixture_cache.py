"""Fetch GoatTracker 2 and NinjaTracker 2 example songs for tests.

The songs are tracker distribution artifacts, so they are not tracked
in this repo; they are downloaded on demand from pinned source mirror
commits, verified by SHA-256 (:func:`pysidtracker.testing.fetch_verified`),
and cached in pysidtracker's download cache (``.tunecache/archives``). Tests
skip when offline and fail when a pin no longer verifies. The NinjaTracker
example tunes are read from the editor's .d64 disk image
(:func:`pysidtracker.d64.read_d64`).
"""

from pathlib import Path

import pytest
from pysidtracker.d64 import read_d64
from pysidtracker.testing import (
    ChecksumMismatch,
    TuneFetchError,
    fetch_archive_member,
    fetch_verified,
)
from pygoattracker.gt2sources import ARCHIVES as GT2_ARCHIVES

_COMMIT = "a78e7e5273bfbeb17c3777b1f5ceb63b8e6b4667"
_BASE_URL = f"https://raw.githubusercontent.com/leafo/goattracker2/{_COMMIT}/examples"
_NT2_COMMIT = "8da3d4b9c24fd20cfbd0a669a2030c3f37998085"
_NT2_D64_URL = (
    "https://raw.githubusercontent.com/localhost/NinjaTracker/"
    f"{_NT2_COMMIT}/ninjatr2.d64"
)
_NT2_D64_SHA256 = "440624b88db8361251bce0729b383bf3737161b9b87e406fcfef6191829a6002"

_GT1_URL = "https://cadaver.github.io/tools"
GT1_ARCHIVES = {
    "goattrk.zip": "f12df334df5b1749fa0660575d63520a5e064ed254e4390cede6b03f9808ecef",
    "goatold.zip": "ef4ab3287bbb60be5cf5f323518a630e392650516bc23510b6a821acd3702233",
    "goattrk093.zip": (
        "2034f37e1a14c261bdb08028e8aa7fc8fa81922f9ebe76fc3cf6b87aa1425327"
    ),
    "goattracker_v1.4b.zip": (
        "815c1519da43f3428b6129d3184163c9c9286191210e1e5e4b74c606c1a3356e"
    ),
    # GoatTracker 2 releases: CSDb 152431 (V2.06), 152434 (V2.35), 152433 (V2.61).
    "goattrk2_206.zip": GT2_ARCHIVES["gt2_206.zip"][1],
    "goattrk2_235.zip": GT2_ARCHIVES["gt2_235.zip"][1],
    "goattrk2_261.zip": GT2_ARCHIVES["gt2_261.zip"][1],
}
# goattrk093.zip, the V1.4b release and the GoatTracker 2 releases are not on the
# cadaver tools mirror; they ship on CSDb.
_GT1_ARCHIVE_URLS = {
    "goattrk.zip": f"{_GT1_URL}/goattrk.zip",
    "goatold.zip": f"{_GT1_URL}/goatold.zip",
    "goattrk093.zip": "https://csdb.dk/getinternalfile.php/154230/goattrk093.zip",
    "goattracker_v1.4b.zip": (
        "https://csdb.dk/getinternalfile.php/143294/goattracker_v1.4b.zip"
    ),
    "goattrk2_206.zip": "https://csdb.dk/getinternalfile.php/153899/goattrk2.zip",
    "goattrk2_235.zip": "https://csdb.dk/getinternalfile.php/153872/goattrk2.zip",
    "goattrk2_261.zip": "https://csdb.dk/getinternalfile.php/42238/goattrk2_61.zip",
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

# GoatTracker V0.93 editor example songs (no restart byte, $00-terminated wavetables).
GOATTRK093_SONGS = {
    "examples/drumtest.sng": (
        "f1a161f25d7cd4e4d374a443e8eeadeed16e587a84767af6f1b2b1c06808395b"
    ),
    "examples/example.sng": (
        "5abd769dacb64254e16cbb39481b10d3a226cc694b1a743c536476cea11e19a8"
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


def _fetch(fetch, *args, **kwargs) -> bytes:
    """The bytes ``fetch`` caches, skipping the test when the download is
    unreachable."""
    try:
        return fetch(*args, **kwargs).read_bytes()
    except ChecksumMismatch:
        raise
    except TuneFetchError as exc:
        pytest.skip(f"cannot download: {exc}")


def example_song(name: str) -> bytes:
    """Bytes of a pinned GoatTracker example song (cached download)."""
    return _fetch(fetch_verified, f"{_BASE_URL}/{name}", EXAMPLE_SONGS[name])


def gt1_archive(name: str) -> bytes:
    """Bytes of a pinned GoatTracker V1.x release archive (cached download)."""
    return _fetch(fetch_verified, _GT1_ARCHIVE_URLS[name], GT1_ARCHIVES[name])


def _archive_member(archive: str, member: str, sha256: str) -> bytes:
    return _fetch(
        fetch_archive_member,
        _GT1_ARCHIVE_URLS[archive],
        GT1_ARCHIVES[archive],
        member,
        sha256,
        name=f"{Path(archive).stem}/{member}",
    )


def goatold_song(name: str) -> bytes:
    """Bytes of a pinned GoatTracker V1.25 example song (goatold.zip)."""
    return _archive_member("goatold.zip", f"EXAMPLES/{name}", GOATOLD_SONGS[name])


def goattrk_song(member: str) -> bytes:
    """Bytes of a pinned GoatTracker V1.53 example song (goattrk.zip)."""
    return _archive_member("goattrk.zip", member, GOATTRK_SONGS[member])


def goattrk093_song(member: str) -> bytes:
    """Bytes of a pinned GoatTracker V0.93 example song (goattrk093.zip)."""
    return _archive_member("goattrk093.zip", member, GOATTRK093_SONGS[member])


def nt2_example_song(name: str) -> bytes:
    """Bytes of a NinjaTracker 2 example tune from the pinned .d64."""
    image = _fetch(fetch_verified, _NT2_D64_URL, _NT2_D64_SHA256)
    return next(f.prg for f in read_d64(image) if f.name == name)
