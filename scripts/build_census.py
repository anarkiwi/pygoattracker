"""Tally which :class:`PlayerBuild` defines each GoatTracker 2 tune's player states.

python scripts/build_census.py $HVSC tunes.csv [--jobs N]

``ambiguous`` names the code fragment found at more than one site, which voids the build.
"""

import argparse
import collections
import csv
from concurrent.futures import ProcessPoolExecutor

from tqdm import tqdm

from pygoattracker import packed
from pygoattracker.sid import _find_freq_hi_anchor, _load_image


def census(path):
    """``(why no build was read or None, build)`` of one tune's anchored player."""
    try:
        image = _load_image(open(path, "rb").read())
        anchor = _find_freq_hi_anchor(image)
    except Exception:  # pylint: disable=broad-exception-caught
        return "unloadable", None
    if anchor is None:
        return "no frequency table", None
    code = packed.player_span(image, anchor[0])
    try:
        return None, packed._read_build(code)  # pylint: disable=protected-access
    except packed._Ambiguous as e:  # pylint: disable=protected-access
        return f"ambiguous: {e}", None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("hvsc")
    ap.add_argument("tunes")
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args(argv)
    paths = sorted(
        {
            f"{a.hvsc}/{r['path']}"
            for r in csv.DictReader(open(a.tunes))
            if "V2" in r["player"]
        }
    )
    tally = collections.Counter()
    with ProcessPoolExecutor(a.jobs) as ex:
        for amb, build in tqdm(ex.map(census, paths, chunksize=16), total=len(paths)):
            tally[amb or "read"] += 1
            if build is not None:
                tally["adparam unread"] += (
                    build.adparam is None and build.firstnohr != 1
                )
    for k, v in tally.most_common():
        print(f"{v:6d}  {k}")


if __name__ == "__main__":
    main()
