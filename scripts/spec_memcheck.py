"""Check the spec player against the tune's own 6502, call by call, over the whole song.

After every call the SID registers and the RAM outside the stack page and I/O must
equal :class:`~pysidtracker.irqhost.TunePlayer`'s; a row gives the first difference.
``python scripts/spec_memcheck.py $HVSC tunes.csv --player V1 --jobs 20 --out mc.csv``
"""

import argparse
import csv
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from pysidtracker.formats import recognize_format
from pysidtracker.irqhost import TunePlayer
from pysidtracker.testing import song_frames
from tqdm import tqdm

from pygoattracker.formats import GOATTRACKER1, GOATTRACKER2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RAM = ((0, 0x100), (0x200, 0xD000), (0xE000, 0x10000))
FIELDS = ["path", "song", "frames", "frame", "at", "mine", "real"]


class Host(TunePlayer):
    """The tune's 6502 called each frame with the flags its init left, as an
    interrupt entering from the driver's idle loop finds them."""

    def _init(self, subtune: int) -> None:
        super()._init(subtune)
        self._flags = self._mpu.p

    def _frame(self) -> None:
        self._mpu.p = self._flags
        super()._frame()


def first_difference(mine: bytes, real: bytes):
    """``(address, mine, real)`` of the first RAM byte that differs, else None."""
    for lo, hi in RAM:
        if mine[lo:hi] != real[lo:hi]:
            at = next(a for a in range(lo, hi) if mine[a] != real[a])
            return at, mine[at], real[at]
    return None


def spec_player(data: bytes, song: int):
    """The spec player of 1-based ``song`` running on the tune's own memory: a V1.x
    format player, a GoatTracker 2 spec bound in place (:mod:`tests._gt2inplace`)."""
    fmt = recognize_format((GOATTRACKER1, GOATTRACKER2), data)
    if fmt is GOATTRACKER2:
        from tests._gt2inplace import InPlace  # pylint: disable=import-outside-toplevel

        return InPlace(data, song - 1)
    return fmt.player(fmt.load(data, song - 1))


def compare(spec, cpu, frames: int) -> dict:
    """Play ``frames`` calls on ``spec`` and ``cpu`` side by side: ``frame`` "ok",
    else the first call whose SID registers or RAM differ, and where."""
    for frame in range(frames):
        spec.play_frame()
        cpu.play_frame()
        regs = [r for r, (a, b) in enumerate(zip(spec.regs, cpu.regs)) if a != b]
        if regs:
            reg = regs[0]
            return dict(
                frames=frames,
                frame=frame,
                at=f"D4{reg:02X}",
                mine=spec.regs[reg],
                real=cpu.regs[reg],
            )
        diff = first_difference(spec.memory, bytes(cpu._mem))  # pylint: disable=W0212
        if diff:
            at, mine, real = diff
            return dict(
                frames=frames, frame=frame, at=f"{at:04X}", mine=mine, real=real
            )
    return dict(frames=frames, frame="ok")


def check(data: bytes, song: int) -> dict:
    """Play 1-based ``song`` of ``data`` on the spec and on the 6502, whole song."""
    return compare(
        spec_player(data, song), Host(data, song - 1), song_frames(data, song)
    )


def _job(job):
    path, song = job
    try:
        return dict(path=str(path), song=song, **check(path.read_bytes(), song))
    except Exception as exc:  # pylint: disable=broad-except
        return dict(path=str(path), song=song, frame="error", at=repr(exc)[:200])


def _jobs(args):
    for item in args.tunes:
        if not item.endswith(".csv"):
            yield Path(args.hvsc) / item, args.song
            continue
        with open(item, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if args.player in row["player"]:
                    for song in range(1, int(row["songs"]) + 1):
                        yield Path(args.hvsc) / row["path"], song


def main() -> None:
    """Check every subtune named, one CSV row each."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hvsc")
    parser.add_argument("tunes", nargs="+", help="find CSVs or paths under HVSC")
    parser.add_argument("--song", type=int, default=1)
    parser.add_argument("--player", default="", help="find CSV player substring")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--out")
    args = parser.parse_args()
    jobs = list(_jobs(args))
    with open(args.out or "/dev/stdout", "w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, FIELDS)
        writer.writeheader()
        tally = {}
        with ProcessPoolExecutor(args.jobs) as pool:
            bar = tqdm(pool.map(_job, jobs), total=len(jobs), unit="song")
            for row in bar:
                kind = row["frame"] if row["frame"] in ("ok", "error") else "differs"
                tally[kind] = tally.get(kind, 0) + 1
                bar.set_postfix(tally)
                writer.writerow(row)
                out.flush()
    print(tally, file=sys.stderr)


if __name__ == "__main__":
    main()
