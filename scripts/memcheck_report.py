"""Tally a ``spec_memcheck.py`` CSV by playroutine and verdict, with examples.

``python scripts/memcheck_report.py mc.csv [--examples 5]``
"""

import argparse
import collections
import csv

from pysidtracker import SidImage

from pygoattracker.gt1 import find_player


def _routine(path: str) -> str:
    try:
        found = find_player(SidImage.from_bytes(open(path, "rb").read()).mem)
    except Exception:  # pylint: disable=broad-except
        return "?"
    return found.ref.name if found else "gt2"


def main() -> None:
    """Print verdict counts per routine and the first differing rows."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv")
    parser.add_argument("--examples", type=int, default=5)
    args = parser.parse_args()
    with open(args.csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    groups = collections.defaultdict(list)
    for row in rows:
        kind = row["frame"] if row["frame"] in ("ok", "error") else "differs"
        groups[_routine(row["path"]), kind].append(row)
    for (routine, kind), members in sorted(groups.items()):
        print(f"{routine:12} {kind:8} {len(members)}")
        if kind != "ok":
            for row in members[: args.examples]:
                print(
                    "   ",
                    row["path"],
                    row["song"],
                    row["frame"],
                    row["at"],
                    row["mine"],
                    row["real"],
                )


if __name__ == "__main__":
    main()
