"""Render one HVSC tune with pygoattracker and diff it against the sidtrace oracle.

A single-tune oracle view for debugging: the pygoattracker grid, the
sidplayfp oracle, and a frame/register diff of the two.
"""

import argparse
import sys
from pathlib import Path

import numpy as np

from pysidtracker import gridcmp, is_ntsc, resolve_tune, survey

from pygoattracker import gt1, sid
from pygoattracker.gt1player import Gt1Player


def render(data, song, nframes, anchor):
    """The pygoattracker grid (as int16) for 1-based ``song`` of ``data``."""
    image = sid._load_image(data)
    if gt1.find_player(image.mem) is not None:
        gt1_song = gt1.decompile_gt1(data, subtune=song - 1)
        return np.asarray(
            Gt1Player(gt1_song, subtune=song - 1).render_grid(nframes), np.int16
        )
    if anchor is not None:
        det = sid.detect_playroutine(image, sid._recognize, subtune=song - 1)
        anchors = list(sid._all_anchors(image))
        match = next((a for a in anchors if a[0] == anchor), det.anchor)
        decoded = sid._decompile_any_songs(
            image, image.header, image.end, match, song - 1
        )
    else:
        decoded = sid.decompile_sid(data, subtune=song - 1)
    return np.asarray(decoded.player().render_grid(nframes), np.int16)


def main(argv=None):
    parser = argparse.ArgumentParser(description="single-tune oracle diff")
    parser.add_argument("tune")
    parser.add_argument("--song", type=int, default=1)
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--seconds", type=float, default=None)
    parser.add_argument("--anchor", type=lambda s: int(s, 16), default=None)
    parser.add_argument("--cache", default=None)
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    cache = args.cache or str(root / "tests" / ".tunecache")
    path = resolve_tune(args.tune, cache_dir=cache)
    if path is None and Path(args.tune).exists():
        path = Path(args.tune)
    if path is None:
        print(f"tune {args.tune!r} unavailable (offline, not cached)", file=sys.stderr)
        raise SystemExit(2)

    data = Path(path).read_bytes()
    clock = "NTSC" if is_ntsc(data) else "PAL"
    hz = 151227.0 if clock == "NTSC" else 156250.0
    cpf = 98524 if clock == "NTSC" else 97504
    seconds = args.seconds if args.seconds is not None else args.frames * cpf / hz

    work = root / ".oracle-cache"
    ocsv = survey.oracle_cache(work) / f"{Path(str(path)).stem}_{args.song}.csv.zst"
    rows = survey.oracle_rows(path, ocsv, seconds, args.song)
    _, oracle = survey.oracle_frames(rows, seconds, clock, nframes=args.frames)

    try:
        lib = render(data, args.song, args.frames, args.anchor)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"RENDER FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"tune : {path}")
    print(
        f"song : {args.song}   clock {clock}   oracle {len(oracle)} lib {len(lib)} frames"
    )
    verdict = gridcmp.verdict_lead(oracle, lib)
    print(gridcmp.lead_report(verdict))
    if not verdict.ok and verdict.first is not None:
        print(
            gridcmp.window_report(
                *gridcmp.aligned(oracle, lib, verdict.lead), verdict.first
            )
        )


if __name__ == "__main__":
    main()
