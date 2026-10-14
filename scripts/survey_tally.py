"""Tally ``results.csv`` verdicts over named key lists.

python scripts/survey_tally.py results.csv regress.txt guard.txt [--min]
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results")
    ap.add_argument("keylists", nargs="+")
    a = ap.parse_args(argv)
    with open(a.results, newline="", encoding="utf-8") as f:
        rows = {r["key"]: r for r in csv.DictReader(f)}
    for path in a.keylists:
        keys = Path(path).read_text("utf-8").split()
        verdicts, near = tally(rows, keys)
        print(f"{path}: {len(keys)} {dict(sorted(verdicts.items()))}")
        print(f"  differs by min_bad (4 = 4+): {dict(sorted(near.items()))}")


if __name__ == "__main__":
    main()
