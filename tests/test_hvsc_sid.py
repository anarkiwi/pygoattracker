"""Decompile real GoatTracker ``.sid`` tunes from the local HVSC tree.

Unlike :mod:`tests.test_sid` (which assembles a synthetic packed image), this
exercises the ``.sid`` decompiler against a deterministic, committed sample of
real High Voltage SID Collection tunes that ``sidid`` identifies as GoatTracker
players. HVSC tunes are copyright works and are never committed; only their
relative paths are (``tests/data/hvsc_goattracker_sample.txt``). The test runs
for real when the local tree is available and skips cleanly otherwise, so
offline CI stays green.

Point it at a local HVSC ``C64Music`` directory with the ``HVSC`` environment
variable::

    HVSC=/path/to/C64Music pytest tests/test_hvsc_sid.py

Scope. The decompiler targets gt2reloc-*packed* GoatTracker 2 images (the
``decompile_sid`` path). Empirically every HVSC GoatTracker tune is a
direct-load image -- none are crunched/relocated, so the emulated-init unpack
path is never needed for this corpus (it stays covered by the synthetic tests).
Two tune families share the GoatTracker frequency-table signature but not the
packed data layout and are therefore out of scope; they are *excluded* here (the
public parse raises :class:`SidParseError`, which the test tallies rather than
asserting against):

  * GoatTracker **V1.x** tunes -- an older player with a different on-image
    layout after the frequency table.
  * A minority of **V2** tunes whose player stores the frequency table and song
    data in a different (non-gt2reloc) arrangement (e.g. the table in high/low
    rather than low/high column order).

Roughly 70% of the sampled corpus is in scope and must decompile; the remainder
raises a clean ``SidParseError`` (never any other exception).
"""

import os
from pathlib import Path

import pytest

from pysidtracker import PlayroutineKind

from pygoattracker import sid
from pygoattracker.errors import SidParseError
from pygoattracker.reader import parse_sng
from pygoattracker.writer import build_sng

_SAMPLE_FILE = Path(__file__).parent / "data" / "hvsc_goattracker_sample.txt"

# Fraction of the (present) sample that must decompile: a regression floor for
# the packed decoder. The committed sample decompiles ~70%; the rest are the
# documented out-of-scope variants above.
_MIN_PASS_RATE = 0.60
# Exact floor asserted when the whole local sample is present (measured 215/307
# with the current decoder); guards against regressions the rate alone would
# let through.
_FULL_SAMPLE_MIN_PASS = 210

# Representatives, one per decoder capability, that must fully decompile and
# round-trip. Each stresses a specific real-corpus fix:
_REPRESENTATIVES = {
    # plain stock frequency table, single subtune.
    "GAMES/G-L/Hammurabi.sid": dict(subtunes=1, min_patterns=10),
    # finetuned frequency table (stock high column, retuned low column): found
    # only by the high-column anchor fallback.
    "DEMOS/A-F/A_Crack_in_the_Facade.sid": dict(subtunes=1, min_patterns=1),
    # PSID header advertises 1 subtune but the song(order)-table holds 2: the
    # subtune count is recovered from the packed layout, not trusted from it.
    "DEMOS/G-L/In_High_Spirits.sid": dict(subtunes=2, min_patterns=10),
}


def _hvsc_root() -> Path:
    root = os.environ.get("HVSC")
    if not root:
        pytest.skip("set HVSC to a local C64Music tree to run the corpus test")
    path = Path(root)
    if not path.is_dir():
        pytest.skip(f"HVSC path {root} is not a directory")
    return path


def _sample() -> list:
    return [
        line.strip() for line in _SAMPLE_FILE.read_text().splitlines() if line.strip()
    ]


def _present(root: Path) -> list:
    return [rel for rel in _sample() if (root / rel).is_file()]


def test_sample_list_is_deterministic():
    sample = _sample()
    assert sample == sorted(sample), "sample must stay sorted for determinism"
    assert len(sample) == len(set(sample))
    assert len(sample) > 250


def test_hvsc_corpus_decompiles():
    root = _hvsc_root()
    present = _present(root)
    if len(present) < 100:
        pytest.skip(f"only {len(present)} sample tunes present under {root}")

    parsed = 0
    excluded = 0
    for rel in present:
        raw = (root / rel).read_bytes()
        try:
            result = sid.decompile_sid(raw)
        except SidParseError:
            # Documented out-of-scope variant (see module docstring). A clean
            # SidParseError is the contract for tunes the packed path cannot
            # decode; any other exception propagates and fails the test.
            excluded += 1
            continue
        song = result.song
        assert song.patterns, f"{rel}: decompiled to zero patterns"
        assert song.subtunes, f"{rel}: decompiled to zero subtunes"
        parsed += 1

    total = parsed + excluded
    assert total == len(present)
    assert parsed >= _MIN_PASS_RATE * total, (
        f"only {parsed}/{total} sampled GoatTracker tunes decompiled "
        f"(< {_MIN_PASS_RATE:.0%} floor); excluded={excluded}"
    )
    if len(present) == len(_sample()):
        assert parsed >= _FULL_SAMPLE_MIN_PASS, (
            f"packed-decoder regression: {parsed} decompiled, "
            f"expected >= {_FULL_SAMPLE_MIN_PASS}"
        )


def test_hvsc_corpus_all_direct_load():
    # Every recognised HVSC GoatTracker tune is a direct-load image: static
    # recognition succeeds without emulating init. init=False proves no tune in
    # the sample needs the unpack path (and keeps the test emulator-free/fast).
    root = _hvsc_root()
    present = _present(root)
    if len(present) < 100:
        pytest.skip(f"only {len(present)} sample tunes present under {root}")
    parser = sid.GoatTrackerSidParser()
    non_direct = []
    for rel in present:
        raw = (root / rel).read_bytes()
        detection = parser.detect(raw, init=False)
        if detection.kind is not PlayroutineKind.DIRECT:
            non_direct.append(rel)
    # Only tunes whose frequency table is absent entirely (a couple of
    # non-gt2reloc oddities) fail static recognition; allow a tiny tail.
    assert len(non_direct) <= 3, f"unexpected non-direct tunes: {non_direct}"


@pytest.mark.parametrize("rel,expect", _REPRESENTATIVES.items())
def test_hvsc_representatives(rel, expect):
    root = _hvsc_root()
    path = root / rel
    if not path.is_file():
        pytest.skip(f"representative {rel} not present under {root}")
    raw = path.read_bytes()

    detection = sid.GoatTrackerSidParser().detect(raw, init=False)
    assert detection.kind is PlayroutineKind.DIRECT
    assert detection.trustworthy_header

    result = sid.decompile_sid(raw)
    song = result.song
    assert len(song.subtunes) == expect["subtunes"]
    assert len(song.patterns) >= expect["min_patterns"]
    assert song.instruments

    # The recovered song is a valid .SNG: it survives a writer/reader round-trip.
    reparsed = parse_sng(build_sng(song))
    assert len(reparsed.patterns) == len(song.patterns)
    assert len(reparsed.instruments) == len(song.instruments)
