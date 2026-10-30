"""Every GoatTracker 2 tune in a list, against its own ``.sng`` round trip.

    python scripts/equiv_hvsc.py C64Music tests/data/hvsc_goattracker_sample.txt

The ``.sng`` export (the song text where a ``.sng`` cannot hold the song) written
back into the tune's image (:func:`song_into_sid`) must have the tune's canonical
text (:func:`pygoattracker.canonical.canonical`).
"""

import argparse
import os
import sys

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from tqdm import tqdm

from pygoattracker import text
from pygoattracker.canonical import canonical
from pygoattracker.errors import GoatTrackerError, SngValidationError
from pygoattracker.reader import read_sng
from pygoattracker.rebuild import song_into_sid
from pygoattracker.sid import decompile_sid
from pygoattracker.writer import build_sng


def check(path) -> tuple:
    """``(tune, verdict)``: identical bytes, equal, differs, not GoatTracker 2, or the
    error raised."""
    data = Path(path).read_bytes()
    try:
        dec = decompile_sid(data)
    except GoatTrackerError:
        return str(path), "not gt2"
    try:
        want = canonical(dec)
    except Exception as exc:  # pylint: disable=broad-except
        return str(path), f"canonical error: {type(exc).__name__}: {exc}"
    try:
        song, how = read_sng(build_sng(dec.song)), ""
    except SngValidationError:
        song, how = text.loads(text.dumps(dec.typed)), " (song text)"
    try:
        back = song_into_sid(data, song)
        same = want == canonical(decompile_sid(back))
    except GoatTrackerError as exc:
        return str(path), f"no round trip: {exc}"
    except Exception as exc:  # pylint: disable=broad-except
        return str(path), f"error: {type(exc).__name__}: {exc}"
    verdict = ("identical" if back == data else "equal") if same else "differs"
    return str(path), verdict + how


def main(argv=None) -> int:
    """Print each tune that is not equal, then the verdict counts."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    ap.add_argument("root", type=Path, help="HVSC C64Music directory")
    ap.add_argument("tunes", type=Path, help="file of tune paths relative to root")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    args = ap.parse_args(argv)
    paths = [args.root / rel for rel in args.tunes.read_text().split()]
    counts = Counter()
    with ProcessPoolExecutor(args.jobs) as pool:
        for tune, verdict in tqdm(pool.map(check, paths), total=len(paths)):
            counts[verdict.split(":")[0]] += 1
            if verdict.split(" (")[0] not in ("identical", "equal", "not gt2"):
                print(f"{tune}: {verdict}")
    print(dict(counts))
    return int(
        any(v.split(" (")[0] not in ("identical", "equal", "not gt2") for v in counts)
    )


if __name__ == "__main__":
    sys.exit(main())
