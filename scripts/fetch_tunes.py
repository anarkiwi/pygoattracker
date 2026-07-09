#!/usr/bin/env python3
"""Download GoatTracker ``.sid`` corpus tunes into a gitignored cache.

The HVSC GoatTracker corpus tunes (``tests/data/hvsc_goattracker_sample.txt``)
are copyright works and are **never** committed to this repo (see
``.gitignore``).  They are fetched on demand from a public HVSC mirror into
``tests/.tunecache/`` (gitignored), so the corpus decompile test runs against a
local HVSC tree (``$HVSC``) when present, and otherwise fetches from the mirror.

Usage::

    python scripts/fetch_tunes.py            # fetch the whole corpus sample
    python scripts/fetch_tunes.py --list     # print the sample paths
"""

from __future__ import annotations

import argparse
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("GT_TUNECACHE", str(REPO / "tests" / ".tunecache")))
SAMPLE_FILE = REPO / "tests" / "data" / "hvsc_goattracker_sample.txt"

# Public HVSC mirror.  Override with ``$HVSC_MIRROR``.
MIRROR = os.environ.get("HVSC_MIRROR", "https://hvsc.brona.dk/HVSC/C64Music").rstrip(
    "/"
)

# Transient-failure retry policy for mirror fetches (attempts, fixed backoff).
_FETCH_ATTEMPTS = 4
_FETCH_BACKOFF = 2.0


def _is_sid(data: bytes) -> bool:
    return data[:4] in (b"PSID", b"RSID")


def fetch(relpath: str, *, force: bool = False) -> Path:
    """Fetch ``relpath`` from the HVSC mirror into the cache; return its path."""
    relpath = relpath.lstrip("/")
    dest = CACHE / relpath
    if dest.exists() and not force:
        return dest
    url = f"{MIRROR}/{urllib.request.quote(relpath)}"
    req = urllib.request.Request(url, headers={"User-Agent": "pygoattracker/fetch"})
    data = None
    last_exc = None
    for attempt in range(_FETCH_ATTEMPTS):
        try:
            with urllib.request.urlopen(  # nosec B310 (https mirror)
                req, timeout=60
            ) as resp:
                data = resp.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # genuinely absent -- do not retry
                raise
            last_exc = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
        if attempt < _FETCH_ATTEMPTS - 1:
            time.sleep(_FETCH_BACKOFF)
    if data is None:
        raise RuntimeError(f"{url}: fetch failed after retries: {last_exc}")
    if not _is_sid(data):
        raise RuntimeError(f"{url}: not a SID file (magic {data[:4]!r})")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def sample_paths() -> list:
    """Relative HVSC paths in the committed corpus sample."""
    return [
        line.strip()
        for line in SAMPLE_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv=None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download")
    parser.add_argument("--list", action="store_true", help="print sample paths")
    args = parser.parse_args(argv)

    paths = sample_paths()
    if args.list:
        for rel in paths:
            print(rel)
        return 0

    ok = 0
    for rel in paths:
        try:
            fetch(rel, force=args.force)
            ok += 1
        except Exception as exc:  # pylint: disable=broad-except
            print(f"skip {rel}: {exc}")
    print(f"fetched {ok}/{len(paths)} into {CACHE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
