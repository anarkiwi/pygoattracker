"""Render one tune with pygoattracker and diff it against the sidtrace oracle.

A single-tune oracle view for debugging: the oracle grid
(:func:`pysidtracker.survey.file_oracle`, sidtrace or else jennings), the
pygoattracker grid (``--anchor`` forcing a frequency-table anchor), and the lead
report and first-difference window of the two.
"""

import argparse
import sys
from pathlib import Path

import numpy as np

from pysidtracker import gridcmp, resolve_tune, survey
from pysidtracker.testing import default_cache

import oracle_hvsc
from pygoattracker import gt1, sid


def render(data, song, nframes, anchor=None):
    """The pygoattracker grid (as int16) for 1-based ``song`` of ``data``; a
    GoatTracker 2 tune decompiled at the frequency-table ``anchor`` if given."""
    image = sid._load_image(data)
    if anchor is None or gt1.find_player(image.mem) is not None:
        return oracle_hvsc.render(data, song, nframes)[0]
    det = sid.detect_playroutine(image, sid._recognize, subtune=song - 1)
    match = next((a for a in sid._all_anchors(image) if a[0] == anchor), det.anchor)
    decoded = sid._decompile_any_songs(image, image.header, image.end, match, song - 1)
    return np.asarray(decoded.player().render_grid(nframes), np.int16)


def main(argv=None):
    parser = argparse.ArgumentParser(description="single-tune oracle diff")
    parser.add_argument("tune", help="an HVSC relpath or a .sid file")
    parser.add_argument("--song", type=int, default=1)
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--anchor", type=lambda s: int(s, 16), default=None)
    parser.add_argument("--cache", default=None, help="tune cache (default .tunecache)")
    parser.add_argument("--context", type=int, default=3)
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    path = resolve_tune(args.tune, cache_dir=args.cache)
    if path is None and Path(args.tune).exists():
        path = Path(args.tune)
    if path is None:
        print(f"tune {args.tune!r} unavailable (offline, not cached)", file=sys.stderr)
        raise SystemExit(2)

    data = Path(path).read_bytes()
    _, oracle, source = survey.file_oracle(
        data, args.song, args.frames, default_cache("csv", root), Path(path)
    )
    try:
        lib = render(data, args.song, args.frames + gridcmp.LEAD, args.anchor)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"RENDER FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"tune : {path}")
    print(f"song : {args.song}   {source} {len(oracle)} lib {len(lib)} frames")
    verdict = gridcmp.verdict_lead(oracle, lib)
    print(gridcmp.lead_report(verdict))
    if not verdict.ok and verdict.first is not None:
        print(
            gridcmp.window_report(
                *gridcmp.aligned(oracle, lib, verdict.lead), verdict.first, args.context
            )
        )


if __name__ == "__main__":
    main()
