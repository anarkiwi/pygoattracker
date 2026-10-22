"""Pack the test songs into ``tests/data`` with GoatTracker 2.76's own packer.

python scripts/pack_fixtures.py /path/to/gt2reloc

The songs are built here (no tracker artifacts); the packed player is
GoatTracker's ``player.s``, which its author releases for any use.
"""

import argparse
import subprocess
import tempfile
from pathlib import Path

from tqdm import tqdm

from pygoattracker import constants as C
from pygoattracker.model import (
    Instrument,
    Orderlist,
    Pattern,
    PlayPattern,
    Row,
    Song,
    Subtune,
)
from pygoattracker.writer import build_sng

DATA = Path(__file__).resolve().parent.parent / "tests" / "data"


def _pattern(rows, length=8):
    pattern = Pattern.empty(length)
    for k, row in rows.items():
        pattern.rows[k] = row
    return pattern


def _subtune(*patterns):
    return Subtune([Orderlist([PlayPattern(p)]) for p in patterns])


def minimal() -> Song:
    """One instrument and wave program, one note: greloc drops every option."""
    song = Song(name="MINIMAL", author="PYGOATTRACKER", copyright="2026")
    wave = song.wavetable.add(0x41, 0x00)
    song.wavetable.add(C.TABLEJUMP, 0x00)
    song.instruments.append(Instrument(0x09, 0x00, wave, gateoff_timer=2))
    song.patterns = [
        _pattern({k: Row(C.note_value("C-4") + k, 1) for k in range(0, 16, 4)}, 16),
        _pattern({}),
    ]
    song.subtunes = [_subtune(0, 1, 0)]
    return song


def full() -> Song:
    """Two subtunes and instruments using every instrument column and table."""
    song = Song(name="FULL", author="PYGOATTRACKER", copyright="2026")
    w1 = song.wavetable.add(0x41, 0x00)
    song.wavetable.add(C.TABLEJUMP, 0x00)
    w2 = song.wavetable.add(0x81, 0xC0)
    song.wavetable.add(0x11, 0x00)
    song.wavetable.add(C.TABLEJUMP, w2 + 1)
    p1 = song.pulsetable.add(0x88, 0x00)
    song.pulsetable.add(0x20, 0x20)
    song.pulsetable.add(C.TABLEJUMP, 0x00)
    f1 = song.filtertable.add(0x90, 0xF1)
    song.filtertable.add(0x00, 0x40)
    song.filtertable.add(0x20, 0x02)
    song.filtertable.add(C.TABLEJUMP, 0x00)
    vib = song.speedtable.add(0x03, 0x20)
    porta = song.speedtable.add(0x00, 0x40)
    song.instruments += [
        Instrument(0x09, 0x89, w1, p1, f1, vib, 4, 2, 0x09),
        Instrument(0x00, 0xF8, w2, 0, 0, 0, 0, 3, 0x41),
    ]
    note = C.note_value
    song.patterns = [
        _pattern(
            {
                k: (
                    Row(note("C-4") + k, 1 + k // 8, 0x01, porta)
                    if k == 4
                    else Row(note("C-4") + k, 1 + k // 8)
                )
                for k in range(0, 16, 2)
            },
            16,
        ),
        _pattern({0: Row(note("G-3"), 2, 0x04, vib), 8: Row(note("A-3"), 1)}, 16),
        _pattern({0: Row(note("C-2"), 2)}),
    ]
    song.subtunes = [_subtune(0, 1, 2), _subtune(1, 2, 0)]
    return song


SONGS = {"gt2_minimal": minimal, "gt2_full": full}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("gt2reloc")
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        for name, make in tqdm(SONGS.items()):
            sng = Path(tmp) / f"{name}.sng"
            sng.write_bytes(build_sng(make()))
            out = DATA / f"{name}.sid"
            subprocess.run(
                [a.gt2reloc, str(sng), str(out)],
                check=True,
                capture_output=True,
                cwd=tmp,
            )


if __name__ == "__main__":
    main()
