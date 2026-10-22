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
``decompile_sid`` path). Where the song data lies is read off the player by
running it (:mod:`pygoattracker.observe`), so packer revisions that move or
reorder it still decode.

A residual cluster remains *out of scope* and is *excluded* here (the public
parse raises :class:`SidParseError`, which the test tallies rather than
asserting against):

  * GoatTracker **V1.x** tunes, decoded by :mod:`pygoattracker.gt1` instead.
  * The **GoatTracker 2.0x** precompiled players, whose song tables follow the
    instrument tables.

Most of the sampled corpus is in scope and must decompile; the remainder
raises a clean ``SidParseError`` (never any other exception).
"""

import functools
import sys
from pathlib import Path

import pytest

from pysidtracker import PlayroutineKind
from pysidtracker.testing import resolve_tune

from pygoattracker import sid
from pygoattracker.errors import GoatTrackerError, SidParseError
from pygoattracker.reader import parse_sng
from pygoattracker.typed import TypedSong
from pygoattracker.writer import build_sng

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import fetch_tunes  # noqa: E402  (after sys.path tweak)

_SAMPLE_FILE = Path(__file__).parent / "data" / "hvsc_goattracker_sample.txt"

# Fraction of the (present) sample that must decompile, the rest the documented
# out-of-scope variants above; and the floor when the whole sample is present.
_MIN_PASS_RATE = 0.70
_FULL_SAMPLE_MIN_PASS = 235

# Representatives, one per decoder capability, that must fully decompile and
# round-trip. Each stresses a specific real-corpus fix:
_REPRESENTATIVES = {
    # plain stock frequency table, single subtune.
    "GAMES/G-L/Hammurabi.sid": dict(subtunes=1, min_patterns=10),
    # finetuned frequency table (stock high column, retuned low column): found
    # only by the high-column anchor fallback.
    "DEMOS/A-F/A_Crack_in_the_Facade.sid": dict(subtunes=1, min_patterns=1),
    # PSID header advertises 1 subtune but the song(order)-table holds 2.
    "DEMOS/G-L/In_High_Spirits.sid": dict(subtunes=2, min_patterns=10),
    # gt2reloc revision whose song data is NOT right after the frequency table.
    "DEMOS/M-R/Penumbra.sid": dict(subtunes=1, min_patterns=10),
    # instrument/table region greloc's executed-row tiling could not segment.
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


@functools.lru_cache(maxsize=None)
def _decoded(path, subtune=0):
    """``decompile_sid`` of ``path``, or the :class:`SidParseError` it raises; the
    corpus tests share one decompile per tune."""
    try:
        return sid.decompile_sid(Path(path).read_bytes(), subtune=subtune)
    except SidParseError as exc:
        return exc


def _decompile(path, subtune=0):
    result = _decoded(str(path), subtune)
    if isinstance(result, SidParseError):
        raise result
    return result


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
        try:
            result = _decompile(path)
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


def test_hvsc_corpus_recognized_tunes_are_direct_load():
    # Static recognition (init=False, emulator-free) finds every decodable HVSC
    # GoatTracker tune as a direct-load image; a tune it does not recognize (no
    # packed-player idiom) is one the decompiler cannot decode either.
    present = _present()
    if len(present) < 100:
        pytest.skip(f"only {len(present)} sample tunes available (local or mirror)")
    parser = sid.GoatTrackerSidParser()
    decodable = []
    for rel, path in present:
        raw = path.read_bytes()
        if parser.detect(raw, init=False).kind is PlayroutineKind.DIRECT:
            continue
        try:
            sid.decompile_sid(raw)
        except SidParseError:
            continue
        decodable.append(rel)
    assert not decodable, f"decodable tunes not recognized: {decodable}"


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


def test_wrapper_selected_player():
    """Issue #34: a wrapper rewrites the play entry per song to pick a player.

    Ewe_Woz_Ere_DX bundles two players. Songs 1-9 enter the wrapper-selected
    build (zero-page ghost that the play wrapper flushes to the chip), which the
    decoder must select -- not the frequency anchor it sees first. Songs 2 and 9
    are the issue's named cases; they decode to the wrapper-flush build.
    """
    rel = "MUSICIANS/B/Bop/Ewe_Woz_Ere_DX.sid"
    path = _resolve(rel)
    if path is None:
        pytest.skip(f"{rel} unavailable (local or mirror)")
    raw = path.read_bytes()
    for subtune in (1, 8):  # songs 2 and 9
        result = sid.decompile_sid(raw, subtune=subtune)
        assert (
            result.info.wrapper_flush
        ), f"song {subtune + 1}: the wrapper-selected ghost-flush build must be the one decoded"
        assert result.player() is not None, f"song {subtune + 1}: no player"


def _tune(rel):
    path = _resolve(rel)
    if path is None:
        pytest.skip(f"{rel} unavailable (local or mirror)")
    return path.read_bytes()


def test_the_song_table_load_selects_the_player_and_its_song():
    """Captain_Future_Preview bundles one-song players; its init enters the
    second for song 2, whose player reads its own song-table entry 0."""
    result = sid.decompile_sid(_tune("GAMES/A-F/Captain_Future_Preview.sid"), 1)
    assert result.subtune == 0 and len(result.song.subtunes) == 1
    assert result.info.layout.songtbl == 0x1E3A


def test_a_song_no_goattracker_player_plays():
    """Lord_of_Dragonspire's song 2 is played by another routine: no song table
    is read, and the decoder says so."""
    raw = _tune("MUSICIANS/P/Page_Jason/Lord_of_Dragonspire.sid")
    with pytest.raises(SidParseError, match="no GoatTracker song table read"):
        sid.decompile_sid(raw, subtune=1)


def test_hvsc_corpus_round_trips_through_the_typed_model():
    """Every sampled tune re-encodes from its typed song to its own packed bytes,
    and to the same Song (so the same .sng, where a .sng can hold it)."""
    present = _present()
    if len(present) < 100:
        pytest.skip(f"only {len(present)} sample tunes available (local or mirror)")
    checked = 0
    for rel, path in present:
        try:
            result = _decompile(path)
        except SidParseError:
            continue
        assert result.repack() == [], rel
        typed = TypedSong.from_song(result.song, result.info.simplepulse)
        assert typed.to_song() == result.song, rel
        checked += 1
    assert checked >= _FULL_SAMPLE_MIN_PASS or len(present) < len(_sample())


def test_hvsc_corpus_resolves_overclaimed_songs():
    """A PSID ``songs`` count taller than the decoded subtune count.

    Each song is resolved through the player's own init, so a song the player
    remaps to a real subtune decodes and plays rather than erroring, and one
    that selects nothing decomposes as not_decompiled (``SidParseError``) rather
    than the ``GoatTrackerError`` that used to surface as a ``library_error``.
    """
    present = _present()
    if len(present) < 100:
        pytest.skip(f"only {len(present)} sample tunes available (local or mirror)")
    overclaimed = 0
    resolved_to_music = 0
    for rel, path in present:
        try:
            base = _decompile(path)
        except SidParseError:
            continue
        decoded = len(base.song.subtunes)
        if base.header.songs <= decoded:
            continue
        overclaimed += 1
        for s in range(base.header.songs):
            try:
                result = _decompile(path, s)
                result.player()
            except SidParseError:
                continue  # this song selects a subtune the player has no song for
            except GoatTrackerError as exc:
                pytest.fail(f"{rel} song {s + 1}: {exc!r}; expected SidParseError")
            if s >= decoded:
                resolved_to_music += 1  # an over-claimed song the player now plays
    assert overclaimed >= 1, "no over-claiming corpus tune to exercise song resolution"
    assert resolved_to_music >= 1, (
        "no over-claimed song resolved to a decodable subtune; "
        "song selection is not following the player's own init"
    )
