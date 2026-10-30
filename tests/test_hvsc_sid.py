"""Decompile real GoatTracker ``.sid`` tunes from the local HVSC tree.

Unlike :mod:`tests.test_sid` (which assembles a synthetic packed image), this
exercises the ``.sid`` decompiler against a deterministic, committed sample of
real High Voltage SID Collection tunes that ``sidid`` identifies as GoatTracker
players. HVSC tunes are copyright works and are never committed; only their
relative paths are (``tests/data/hvsc_goattracker_sample.txt``). Each tune is
resolved from a local HVSC ``C64Music`` tree (``$HVSC``) when present, else
fetched from the public HVSC mirror into the gitignored ``.tunecache``
(with retries), so the test runs for real in CI and skips an individual tune
only when it is genuinely unreachable.

Point it at a local HVSC ``C64Music`` directory with the ``HVSC`` environment
variable (otherwise it fetches from ``$HVSC_MIRROR``)::

    HVSC=/path/to/C64Music pytest tests/test_hvsc_sid.py

Scope. The decompiler targets gt2reloc-*packed* GoatTracker 2 images (the
``decompile_sid`` path). Where the song data lies is read off the player by
running it (:mod:`pygoattracker.observe`), so packer revisions that move or
reorder it still decode. So do the binary-relocator players of GoatTracker
V2.0-V2.28 (before V2.29's assembler-based relocator), whose song tables follow the
instrument and wave/pulse/filter tables.

A residual cluster remains *out of scope* and is *excluded* here (the public
parse raises :class:`SidParseError`, which the test tallies rather than
asserting against):

  * GoatTracker **V1.x** tunes, decoded by :mod:`pygoattracker.gt1` instead.

Most of the sampled corpus is in scope and must decompile; the remainder
raises a clean ``SidParseError`` (never any other exception).
"""

import functools
from pathlib import Path

import pytest

from pysidtracker import PlayroutineKind
from pysidtracker.testing import (
    assert_matches,
    check_outcome,
    corpus_params,
    resolve_tune,
)

from pygoattracker import sid
from pygoattracker.errors import SidParseError
from pygoattracker.packed import pattern_rows
from pygoattracker.reader import parse_sng
from pygoattracker.typed import TypedSong
from pygoattracker.writer import build_sng

_SAMPLE_FILE = Path(__file__).parent / "data" / "hvsc_goattracker_sample.txt"

# Sample tunes the packed path does not decode, by the SidParseError each raises.
_NOT_DECODED = {
    "DEMOS/M-R/Oldschool_Streamer.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/A/Ames_John/MMX-Storm_Eagle.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/B/B0rje/Interlude.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/B/Beyond_Reproach/Sexy_Robot_3_Intro.sid": "play call did not return",
    "MUSICIANS/C/Cadaver/Metal_Warrior_4_preview.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/C/Cerror/Dintrosid.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/C/Chapelier/Swamp_Walk.sid": "song and pattern tables do not tile one block",
    "MUSICIANS/C/Ciaran/Turrican_3_World_1_Level_1.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/D/Deizi/Bubble_Bobble_cover.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/D/Dexter/As_Usual.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/D/Dexter/Speedlimit.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/G/Garvalf/Bransle_de_Champaigne_1.sid": "song and pattern tables do not tile one block",
    "MUSICIANS/G/Ghormak/Percy_88_Remix.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/H/Hoffmann_Michal/Sahara_Takeover.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/H/Holt_Hein/Apollo_vs_Mr_T.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/H/Holt_Hein/Dialogue_Wallys_Tetris_Poem.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/H/Hukka/Rove-Sidigi.sid": (
        "no GoatTracker song table read: the player reads no song and pattern tables"
    ),
    "MUSICIANS/J/Jammer/Cloud_Swimming_2x.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/J/Josstintimberlake/Fudgepackerz.sid": "song and pattern tables do not tile one block",
    "MUSICIANS/J/Josstintimberlake/o2.sid": "song and pattern tables do not tile one block",
    "MUSICIANS/L/Laserboy/Juhon_Faijan_Kupla.sid": "song and pattern tables do not tile one block",
    "MUSICIANS/L/Linus/Yoshi-blutig_bis_medium_bitte.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/M/Magnar/Star_Wars-Imperial_March_Upbeat.sid": "play call did not return",
    "MUSICIANS/M/Maktone/Crusin.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/M/Maktone/King_Size.sid": "song and pattern tables do not tile one block",
    "MUSICIANS/M/Maktone/Simple.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/M/Maktone/fairlight_20_years.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/M/Mankeli/Martti_on_Jopen_Kaveri.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/M/Mice/Sexty4.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/M/Morton_Adam/Jive_Witit.sid": "song and pattern tables do not tile one block",
    "MUSICIANS/M/Mueller_Manuel/Way_of_the_Saint.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/N/Nata/Dreamers_of_Mirage.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/N/Nata/Maja.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/N/Nata/Stereoid_Loader.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/N/Ne7/Boys.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/N/Ne7/Glitch_Bitch.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/N/Ne7/Manifold_tune_05.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/N/Ne7/Oyss_Window.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/N/Ne7/Thru_Horizons_Edge.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/N/Necrophobic/Undead_Again_part_2.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/N/Nordischsound/Ghostbusters.sid": "song and pattern tables do not tile one block",
    "MUSICIANS/O/Oedipus/Hybris.sid": "song and pattern tables do not tile one block",
    "MUSICIANS/O/Oedipus/Worktunes/Other_Side.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/P/Pontonius/Poke.sid": "song and pattern tables do not tile one block",
    "MUSICIANS/R/Radiantx/Hacked_to_Oblivion.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/R/Rupert_Dissident/Subterfuge_Pastime.sid": "no column indexes the wavetable after the instruments",
    "MUSICIANS/S/Six/GC_cover.sid": "song and pattern tables do not tile one block",
    "MUSICIANS/S/Speedball/Banarnold.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/T/TMR/Nadira/Lurve.sid": "instrument columns are not evenly spaced",
    "MUSICIANS/T/Topaz/Lost.sid": "instrument columns are not evenly spaced",
}

# Tunes whose PSID ``songs`` exceeds the decoded subtunes: each song resolves through
# the player's own init to a subtune (True past the decoded ones, False within) or
# raises SidParseError (None).
_OVERCLAIMED = {
    "GAMES/G-L/Lester.sid": (False,) * 2 + (True,) * 10,
    "MUSICIANS/B/Bayliss_Richard/Synthia_in_the_Cyber_Crypt.sid": (False,)
    + (True,) * 3,
    "MUSICIANS/B/Bayliss_Richard/Trance_Sector.sid": (False,) * 3 + (True,) * 11,
    "MUSICIANS/L/Linus/Darkness.sid": (False,) + (True,) * 5,
}

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
    return resolve_tune(rel)


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


def test_sample_list_is_deterministic():
    sample = _sample()
    assert sample == sorted(sample), "sample must stay sorted for determinism"
    assert len(sample) == len(set(sample))
    assert len(sample) > 250


@pytest.mark.parametrize("rel,message", corpus_params(_sample(), _NOT_DECODED))
def test_hvsc_sample_tune(rel, message):
    """A sampled tune is recognized as a direct-load image, decompiles, and
    re-encodes from its typed song to its own packed bytes (or, for a pattern
    spelt outside greloc's grammar, to bytes the player reads the same) and to
    the same Song; or it raises the SidParseError it is listed with."""
    path = _resolve(rel)
    if path is None:
        pytest.skip(f"{rel} unavailable (local or mirror)")
    result = check_outcome(_decompile, path, message, SidParseError)
    if result is None:
        return
    parser = sid.GoatTrackerSidParser()
    assert parser.detect(path.read_bytes(), init=False).kind is PlayroutineKind.DIRECT
    assert result.song.patterns and result.song.subtunes
    patterns = set(result.info.layout.patt_addrs)
    # inline effect parameters become speed rows, which cannot be packed back
    for addr, packed, held in [] if result.info.layout.inline else result.repack():
        assert addr in patterns
        assert pattern_rows(packed, 0)[0] == pattern_rows(held, 0)[0]
    typed = TypedSong.from_song(result.song, result.info.playback.simplepulse)
    assert typed.to_song() == result.song


@pytest.mark.parametrize("rel,songs", _OVERCLAIMED.items())
def test_overclaimed_songs_resolve_through_the_player(rel, songs):
    """Each song of a PSID claiming more songs than the decoded subtunes resolves
    through the player's own init: to a subtune it plays, or to SidParseError."""
    path = _resolve(rel)
    if path is None:
        pytest.skip(f"{rel} unavailable (local or mirror)")
    decoded = len(_decompile(path).song.subtunes)
    outcomes = []
    for song in range(len(songs)):
        try:
            _decompile(path, song).player()
        except SidParseError:
            outcomes.append(None)
            continue
        outcomes.append(song >= decoded)
    assert tuple(outcomes) == songs


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
    """Issue #34: a wrapper rewrites the init and play jumps per song to pick a
    player.

    Ewe_Woz_Ere_DX bundles two players. Songs 1-9 run the one at $8000, whose
    play jump leads to a wrapper calling ``mt_play`` and flushing its zero-page
    ghost; song 10 the one at $7900. The init selects the player, and the
    wrapper runs around ``mt_play`` into the ghost it flushes.
    """
    rel = "MUSICIANS/B/Bop/Ewe_Woz_Ere_DX.sid"
    path = _resolve(rel)
    if path is None:
        pytest.skip(f"{rel} unavailable (local or mirror)")
    raw = path.read_bytes()
    for subtune in (1, 8):  # songs 2 and 9
        wrap = sid.decompile_sid(raw, subtune=subtune).info.wrap
        assert (wrap.entries, wrap.sink) == ({0x80B3}, 0xE2)
    wrap = sid.decompile_sid(raw, subtune=9).info.wrap
    assert (wrap.entries, wrap.sink) == ({0x7903, 0x79B2}, None)


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


# GoatTracker V2.0-V2.28 binary-relocator players: ``(HVSC path, what it shows)``.
_BINARY_RELOCATOR = (
    # player1b (fixed gatetimer and first wave): 7 instrument columns, 3 tables,
    # effect parameters inline, waveforms as stored (delays $01-$07).
    ("MUSICIANS/R/Rusty46/A_Love_Story.sid", "7 columns"),
    ("MUSICIANS/R/Rusty46/Baked_and_Boiled.sid", "an empty filtertable"),
    ("MUSICIANS/F/Freedom/Planets_intro_version.sid", "7 columns"),
    ("MUSICIANS/F/Freedom/Tough_Boy_Remake.sid", "7 columns"),
    ("MUSICIANS/Z/Zabutom/Nordic_Scene_Review.sid", "fine vibrato, effect 0 on a row"),
    ("MUSICIANS/B/Bayliss_Richard/Sub_Hunter_preview.sid", "pulse width left by init"),
    ("MUSICIANS/T/Tomaes/Sid_Vicious.sid", "7 columns"),
    # the full 9 columns, 3 tables.
    ("MUSICIANS/M/Morton_Adam/Arpo4.sid", "9 columns, inline parameters"),
    # V2.2x: 4 tables, the speedtable and a waveform shift of $10.
    ("MUSICIANS/R/Rusty46/Freestyle.sid", "no octave 0 (firstnote 12)"),
    ("MUSICIANS/R/Rusty46/Grandmas_Toe_Jam.sid", "instrument vibrato from effect 0"),
    ("MUSICIANS/Z/Zabutom/Sad_Pillow_Cyber_Prophecy.sid", "7 columns, init $2642"),
    ("MUSICIANS/R/Radiantx/Insects.sid", "9 columns"),
    ("MUSICIANS/R/Rusty46/Shadowboxin.sid", "9 columns, instrument vibrato"),
)


@pytest.mark.parametrize("rel", [rel for rel, _ in _BINARY_RELOCATOR])
def test_binary_relocator_players_decompile(rel):
    result = sid.decompile_sid(_tune(rel))
    assert result.song.subtunes and result.song.patterns and result.song.instruments
    assert result.info.layout.binary
    assert result.player() is not None


# Binary-relocator tunes whose replay parts from their 6502 later in the song.
_BINARY_DIVERGES = {
    "MUSICIANS/M/Morton_Adam/Arpo4.sid": "the player's output freezes where the 6502 plays on",
    "MUSICIANS/Z/Zabutom/Sad_Pillow_Cyber_Prophecy.sid": (
        "voice 2's frequency holds where the 6502's slides"
    ),
    "MUSICIANS/R/Rusty46/Freestyle.sid": (
        "voice 1's frequency high byte is $00 where the 6502's is $03"
    ),
}


@pytest.mark.parametrize(
    "rel",
    [
        pytest.param(
            rel,
            marks=(
                [
                    pytest.mark.xfail(
                        strict=True,
                        raises=AssertionError,
                        reason=_BINARY_DIVERGES[rel],
                    )
                ]
                if rel in _BINARY_DIVERGES
                else []
            ),
        )
        for rel, _ in _BINARY_RELOCATOR
    ],
)
def test_binary_relocator_players_play_as_their_6502(rel):
    assert_matches(
        _tune(rel), lambda data: sid.decompile_sid(data).player(), None, name=rel
    )


@pytest.mark.parametrize(
    "rel",
    ["MUSICIANS/B/Booker/Hz_Jazz.sid", "MUSICIANS/X/X-agon/Accept_All_Cookies.sid"],
)
def test_empty_packed_song_is_not_an_orderlist(rel):
    result = sid.decompile_sid(_tune(rel))
    assert len(result.song.subtunes) == result.header.songs == 1
    assert result.player() is not None


_IRQ_DRIVEN = (
    "MUSICIANS/J/Jammer/Come_at_Me_Bro.sid",
    "MUSICIANS/J/Jammer/El_Loco_Challenge_intro.sid",
)


@pytest.mark.parametrize("rel", _IRQ_DRIVEN)
def test_rsid_whose_init_never_returns_decompiles(rel):
    """An RSID without a play address runs its player from an IRQ, so its init ends
    in an idle loop instead of an RTS."""
    result = sid.decompile_sid(_tune(rel))
    assert result.song.subtunes and result.song.patterns and result.song.instruments
