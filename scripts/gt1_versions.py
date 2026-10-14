#!/usr/bin/env python3
"""Classify an HVSC survey's GoatTracker V1.x tunes by embedded playroutine.

    python scripts/gt1_versions.py $HVSC .hvsc-survey/tunes.csv --out gt1_versions.csv

Prints a version -> tunes table; ``--sample N`` writes up to N tunes per version
to a survey ``tunes.csv``.
"""

from __future__ import annotations

import argparse
import collections
import csv
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from tqdm import tqdm

from pygoattracker.errors import SidParseError
from pygoattracker.gt1 import decompile_image, find_player
from pygoattracker.sid import _load_image

GT1 = "GoatTracker_V1.x"


def classify(path: str) -> tuple[str, str]:
    """``(version, detail)`` for one ``.sid``: the matched playroutine or ``none``."""
    try:
        image = _load_image(Path(path).read_bytes())
    except SidParseError as exc:
        return "unreadable", str(exc)
    found = find_player(image.mem)
    if found is None:
        return "none", ""
    if not found.ref.native:
        return found.ref.name, "recognized"
    try:
        decompile_image(image.mem, found)
    except SidParseError as exc:
        return found.ref.name, str(exc)
    return found.ref.name, "decompiled"


def main(argv=None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("hvsc", type=Path)
    parser.add_argument("tunes", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--sample", type=int, default=0)
    parser.add_argument("--sample-out", type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args(argv)
    with args.tunes.open(newline="") as fh:
        rows = [row for row in csv.DictReader(fh) if row["player"] == GT1]
    paths = [str(args.hvsc / row["path"]) for row in rows]
    with ProcessPoolExecutor(args.jobs) as pool:
        results = list(
            tqdm(pool.map(classify, paths, chunksize=16), total=len(paths), unit="tune")
        )
    tally = collections.Counter(version for version, _ in results)
    subtunes = collections.Counter()
    for row, (version, _) in zip(rows, results):
        subtunes[version] += int(row["songs"])
    print("| version | tunes | subtunes |\n| --- | ---: | ---: |")
    for version, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"| {version} | {count} | {subtunes[version]} |")
    if args.out:
        with args.out.open("w", newline="") as fh:
            out = csv.writer(fh)
            out.writerow(["path", "md5", "version", "detail"])
            for row, (version, detail) in zip(rows, results):
                out.writerow([row["path"], row["md5"], version, detail])
    if args.sample and args.sample_out:
        picked = collections.Counter()
        with args.sample_out.open("w", newline="") as fh:
            out = csv.DictWriter(fh, fieldnames=list(rows[0]))
            out.writeheader()
            for row, (version, detail) in zip(rows, results):
                if detail == "decompiled" and picked[version] < args.sample:
                    picked[version] += 1
                    out.writerow(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
