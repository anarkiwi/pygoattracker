"""Every GoatTracker 2 tune in an HVSC tree, through the typed model and back.

    python scripts/roundtrip_hvsc.py C64Music --out roundtrip.jsonl

A tune ``decompile_sid`` reads must re-encode from its typed song to its packed bytes and its ``.sng``.
"""

import argparse
import json
import os
import sys

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from tqdm import tqdm

from pygoattracker.errors import SidParseError, SngValidationError
from pygoattracker.sid import decompile_sid
from pygoattracker.typed import TypedSong
from pygoattracker.writer import build_sng


def check(path):
    """One tune's verdict: not GoatTracker, or what of it does not come back."""
    row = {"tune": str(path)}
    try:
        d = decompile_sid(Path(path).read_bytes())
    except SidParseError:
        return dict(row, gt2=False)
    except Exception as e:  # pylint: disable=broad-except
        return dict(row, gt2=False, error=f"{type(e).__name__}: {e}")
    row["gt2"] = True
    try:
        row["repack"] = [[a, x.hex(), y.hex()] for a, x, y in d.repack()]
        song = TypedSong.from_song(d.song, d.info.playback.simplepulse).to_song()
        row["song"] = song == d.song
        try:
            row["sng"] = build_sng(song) == build_sng(d.song)
        except SngValidationError as e:
            row["sng"] = str(e)
    except Exception as e:  # pylint: disable=broad-except
        row["error"] = f"{type(e).__name__}: {e}"
    return row


def verdict(row):
    """exact, an error, a packed or song difference, or a song no .sng can hold."""
    if "error" in row:
        return "error"
    if row["repack"] or not row["song"] or row["sng"] is False:
        return "differs"
    return "exact" if row["sng"] is True else "exact, no .sng"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    ap.add_argument("hvsc", nargs="?")
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--tunes", help="a file of tune paths instead of a scan of HVSC")
    ap.add_argument(
        "--resume", action="store_true", help="skip tunes --out already holds"
    )
    a = ap.parse_args(argv)
    if a.tunes:
        paths = [Path(x) for x in Path(a.tunes).read_text().split()]
    else:
        paths = sorted(Path(a.hvsc).rglob("*.sid"))
    done = set()
    if a.resume and Path(a.out).is_file():
        done = {json.loads(x)["tune"] for x in Path(a.out).read_text().splitlines()}
    paths = [p for p in paths if str(p) not in done]
    tally = Counter()
    mode = "a" if a.resume else "w"
    with ProcessPoolExecutor(a.jobs) as ex, open(a.out, mode, encoding="utf-8") as f:
        for row in tqdm(
            ex.map(check, paths, chunksize=16), total=len(paths), unit="tune"
        ):
            f.write(json.dumps(row) + "\n")
            if not row["gt2"]:
                continue
            tally[verdict(row)] += 1
    print(json.dumps(dict(tally)))
    return 0 if set(tally) <= {"exact", "exact, no .sng"} else 1


if __name__ == "__main__":
    sys.exit(main())
