"""Decompile real GoatTracker ``.sid`` tunes from the local HVSC tree.

Unlike :mod:`tests.test_sid` (which assembles a synthetic packed image), this
exercises the ``.sid`` decompiler against a deterministic, committed sample of
real High Voltage SID Collection tunes that ``sidid`` identifies as GoatTracker
players. HVSC tunes are copyright works and are never committed; only their
relative paths are (``tests/data/hvsc_goattracker_sample.txt``). Each tune is
resolved from a local HVSC ``C64Music`` tree (``$HVSC``) when present, else
fetched from the public HVSC mirror into the gitignored ``tests/.tunecache``
(with retries), so the test runs for real in CI and skips an individual tune
only when it is genuinely unreachable.

Point it at a local HVSC ``C64Music`` directory with the ``HVSC`` environment
variable (otherwise it fetches from ``$HVSC_MIRROR``)::

    HVSC=/path/to/C64Music pytest tests/test_hvsc_sid.py

Scope. The decompiler targets gt2reloc-*packed* GoatTracker 2 images (the
``decompile_sid`` path). Empirically every HVSC GoatTracker tune is a
direct-load image -- none are crunched/relocated, so the emulated-init unpack
path is never needed for this corpus (it stays covered by the synthetic tests).
The song(order)-table, pattern-pointer table and wave/pulse/filter/speed table
bases are located from the player's own table-access code (relocation-invariant
``LDA table,Y`` operand capture), so gt2reloc/player revisions that move the
song data off the stock "right after the frequency table" position, or whose
instrument/table region the exact-fit tiling cannot segment, still decode.

A residual cluster remains *out of scope* and is *excluded* here (the public
parse raises :class:`SidParseError`, which the test tallies rather than
asserting against):

  * GoatTracker **V1.x** tunes -- an older player with a different on-image
    layout after the frequency table.
  * A minority of **V2.x** tunes whose packer stores the song(order)-table in a
    different column geometry (its high column does not hold orderlist-pointer
    high bytes), so the gt2reloc pointer grammar does not apply.

Roughly 79% of the sampled corpus is in scope and must decompile; the remainder
raises a clean ``SidParseError`` (never any other exception).
"""

import sys
from pathlib import Path

import pytest

from pysidtracker import PlayroutineKind
from pysidtracker.testing import resolve_tune

from pygoattracker import sid
from pygoattracker.errors import SidParseError
from pygoattracker.reader import parse_sng
from pygoattracker.writer import build_sng

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import fetch_tunes  # noqa: E402  (after sys.path tweak)

_SAMPLE_FILE = Path(__file__).parent / "data" / "hvsc_goattracker_sample.txt"

# Fraction of the (present) sample that must decompile: a regression floor for
# the packed decoder. The committed sample decompiles ~79%; the rest are the
# documented out-of-scope variants above.
_MIN_PASS_RATE = 0.70
# Exact floor asserted when the whole local sample is present (measured 243/307
# with the current decoder); guards against regressions the rate alone would
# let through.
_FULL_SAMPLE_MIN_PASS = 235

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
    # gt2reloc revision whose song data is NOT right after the frequency table:
    # the song(order)-table base + subtune count are read from the player's
    # sequencer code (``mt_songtbllo``/``mt_songtblhi`` operands), not assumed.
    "DEMOS/M-R/Penumbra.sid": dict(subtunes=1, min_patterns=10),
    # instrument/table region that the exact-fit tiling cannot segment: the
    # wave/pulse/filter table bases are located from the player's table-access
    # code (the "nextstep" chain) and the instrument flags from ``K``.
    "DEMOS/M-R/Robots_Can_Dance.sid": dict(subtunes=1, min_patterns=10),
}


def _sample() -> list:
    return [
        line.strip() for line in _SAMPLE_FILE.read_text().splitlines() if line.strip()
    ]


def _resolve(rel: str):
    """Local ``$HVSC`` path or a mirror-fetched cache path for ``rel``.

    Prefers a local HVSC ``C64Music`` tree (``$HVSC``); otherwise fetches the
    tune from the public HVSC mirror into the gitignored cache (with retries).
    Returns ``None`` only when the tune is genuinely unreachable after retries,
    so an individual tune skips cleanly rather than failing offline CI.
    """
    return resolve_tune(rel, cache_dir=fetch_tunes.CACHE)


def _present() -> list:
    """``(rel, path)`` for each sampled tune resolvable locally or via mirror."""
    out = []
    for rel in _sample():
        path = _resolve(rel)
        if path is not None:
            out.append((rel, path))
    return out


def test_sample_list_is_deterministic():
    sample = _sample()
    assert sample == sorted(sample), "sample must stay sorted for determinism"
    assert len(sample) == len(set(sample))
    assert len(sample) > 250


def test_hvsc_corpus_decompiles():
    present = _present()
    if len(present) < 100:
        pytest.skip(f"only {len(present)} sample tunes available (local or mirror)")

    parsed = 0
    excluded = 0
    for rel, path in present:
        raw = path.read_bytes()
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
    present = _present()
    if len(present) < 100:
        pytest.skip(f"only {len(present)} sample tunes available (local or mirror)")
    parser = sid.GoatTrackerSidParser()
    non_direct = []
    for rel, path in present:
        raw = path.read_bytes()
        detection = parser.detect(raw, init=False)
        if detection.kind is not PlayroutineKind.DIRECT:
            non_direct.append(rel)
    # Only tunes whose frequency table is absent entirely (a couple of
    # non-gt2reloc oddities) fail static recognition; allow a tiny tail.
    assert len(non_direct) <= 3, f"unexpected non-direct tunes: {non_direct}"


@pytest.mark.parametrize("rel,expect", _REPRESENTATIVES.items())
def test_hvsc_representatives(rel, expect):
    path = _resolve(rel)
    if path is None:
        pytest.skip(f"representative {rel} unavailable (local or mirror)")
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
