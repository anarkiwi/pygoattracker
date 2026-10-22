"""Tally ``results.csv`` verdicts over named key lists, or against a baseline.

python scripts/survey_tally.py results.csv regress.txt guard.txt [--min]
python scripts/survey_tally.py results.csv --base baseline/results.csv
"""

import argparse
import csv
from collections import Counter
from pathlib import Path


def tally(rows, keys):
    """Verdict counts, and ``min_bad`` buckets of the ``differs``, over ``keys``."""
    verdicts = Counter(rows[k]["verdict"] if k in rows else "missing" for k in keys)
    near = Counter(
        min(int(rows[k]["min_bad"] or 0), 4)
        for k in keys
        if k in rows and rows[k]["verdict"] == "differs"
    )
    return verdicts, near


def transitions(base, rows):
    """``(before, after)`` verdict counts over the keys of ``rows``, and the keys
    exact in ``base`` but not in ``rows``."""
    moves = Counter(
        (base[k]["verdict"] if k in base else "missing", r["verdict"])
        for k, r in rows.items()
    )
    lost = sorted(
        k
        for k, r in rows.items()
        if k in base and base[k]["verdict"] == "exact" and r["verdict"] != "exact"
    )
    return moves, lost


def _read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return {r["key"]: r for r in csv.DictReader(f)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results")
    ap.add_argument("keylists", nargs="*")
    ap.add_argument("--base", help="baseline results.csv to compare verdicts with")
    a = ap.parse_args(argv)
    rows = _read(a.results)
    if a.base:
        base = _read(a.base)
        moves, lost = transitions(base, rows)
        print("| before | after | subtunes |\n| --- | --- | ---: |")
        for (before, after), n in sorted(moves.items()):
            print(f"| {before} | {after} | {n:,} |")
        print(f"exact -> non-exact: {len(lost)}")
        for k in lost:
            print(f"  {k} {rows[k]['path']} {rows[k]['song']} {rows[k]['verdict']}")
    for path in a.keylists:
        keys = Path(path).read_text("utf-8").split()
        verdicts, near = tally(rows, keys)
        print(f"{path}: {len(keys)} {dict(sorted(verdicts.items()))}")
        print(f"  differs by min_bad (4 = 4+): {dict(sorted(near.items()))}")


if __name__ == "__main__":
    main()
