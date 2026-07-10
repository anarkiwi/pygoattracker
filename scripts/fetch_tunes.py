#!/usr/bin/env python3
"""Download GoatTracker ``.sid`` corpus tunes into a gitignored cache.

The HVSC GoatTracker corpus tunes (``tests/data/hvsc_goattracker_sample.txt``)
are copyright works and are **never** committed to this repo (see
``.gitignore``).  They are fetched on demand from a public HVSC mirror into
``tests/.tunecache/`` (gitignored), so the corpus decompile test runs against a
local HVSC tree (``$HVSC``) when present, and otherwise fetches from the mirror.

The download/validate/retry core is :func:`pysidtracker.testing.fetch_tune`;
this script keeps the repo's corpus sample list and CLI.

Usage::

    python scripts/fetch_tunes.py            # fetch the whole corpus sample
    python scripts/fetch_tunes.py --list     # print the sample paths
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from pysidtracker.testing import DEFAULT_MIRROR, fetch_tune

REPO = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("GT_TUNECACHE", str(REPO / "tests" / ".tunecache")))
SAMPLE_FILE = REPO / "tests" / "data" / "hvsc_goattracker_sample.txt"

# Public HVSC mirror.  Override with ``$HVSC_MIRROR``.
MIRROR = os.environ.get("HVSC_MIRROR", DEFAULT_MIRROR).rstrip("/")


def fetch(relpath: str, *, force: bool = False) -> Path:
    """Fetch ``relpath`` from the HVSC mirror into the cache; return its path."""
    return fetch_tune(relpath, cache_dir=CACHE, mirror=MIRROR, force=force)


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
