"""Tally which :class:`PlayerBuild` defines each GoatTracker 2 tune's player shows.

python scripts/build_census.py $HVSC tunes.csv [--jobs N]

A define is ``shown`` where :mod:`pygoattracker.probe` measured it, else ``unshown``.
"""

import argparse
import collections
import csv
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict

from tqdm import tqdm

from pygoattracker.sid import decompile_sid


def census(path):
    """``(why no build was measured or None, build fields)`` of one tune."""
    try:
        with open(path, "rb") as f:
            return None, asdict(decompile_sid(f.read()).info.build)
    except Exception as e:  # pylint: disable=broad-exception-caught
        return type(e).__name__, None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("hvsc")
    ap.add_argument("tunes")
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args(argv)
    with open(a.tunes, encoding="utf-8") as f:
        paths = sorted(
            {f"{a.hvsc}/{r['path']}" for r in csv.DictReader(f) if "V2" in r["player"]}
        )
    tally = collections.Counter()
    with ProcessPoolExecutor(a.jobs) as ex:
        for why, build in tqdm(ex.map(census, paths, chunksize=16), total=len(paths)):
            tally[why or "decompiled"] += 1
            for k, v in (build or {}).items():
                tally[f"{k} {'unshown' if v is None else 'shown'}"] += 1
    for k, v in sorted(tally.items()):
        print(f"{v:6d}  {k}")


if __name__ == "__main__":
    main()
