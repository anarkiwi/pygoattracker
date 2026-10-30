"""Synthetic songs designed from the playroutines' 6502 code to reach every
branch direction, and the corpus songs that measure the baseline.

Each set is ``{name: (song, subtunes)}``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from pysidtracker import TuneFetchError, resolve_tune

from pysidtracker.formats import recognize_format

from pygoattracker.formats import GOATTRACKER2
from pygoattracker.reader import read_sng
from pygoattracker.sid import decompile_sid
from pygoattracker.gt1 import (
    MUSICROUTINE11_SFX,
    ORDERLIST_ENDSONG,
    Gt1Instrument,
    decompile_gt1,
    reference,
)
from pygoattracker.gt1sng import parse_gts1
from tests import _gt1_code, test_gt1
from tests._fixture_cache import (
    EXAMPLE_SONGS,
    GOATOLD_SONGS,
    GOATTRK_SONGS,
    example_song,
    goatold_song,
    goattrk_song,
)
from pygoattracker import constants as C
from pygoattracker.model import (
    Instrument,
    Orderlist,
    Pattern,
    PlayPattern,
    Repeat,
    Row,
    Song,
    Subtune,
    Table,
    Transpose,
)
from tests.conftest import basic_song
from tests.test_oracle_hvsc import TUNES


def resolved(relpath: str):
    """The cached path of HVSC tune ``relpath``."""
    path = resolve_tune(relpath)
    if path is None:
        raise TuneFetchError(f"tune {relpath} unavailable (offline, not cached)")
    return path


def _table(*rows) -> Table:
    return Table([left for left, _ in rows], [right for _, right in rows])


def _pattern(*rows) -> Pattern:
    """Rows given as ``(note, instrument, command, data)`` (a rest when omitted)."""
    return Pattern([Row(*r) if r else Row() for r in rows])


def _order(*entries, restart: int = 0) -> Orderlist:
    return Orderlist(list(entries), restart)


_C4, _E4, _G4 = 0x60 + 48, 0x60 + 52, 0x60 + 55
_REST, _OFF, _ON = C.REST, C.KEYOFF, C.KEYON


def _gt2_song(channels: int = 3) -> Song:
    """Every GoatTracker 2 command, table row kind, orderlist command and
    instrument class, each where the playroutine branches on it: channel 0
    runs the effects on a plain instrument, channel 1 the tables and the
    commands that set them, channel 2 the sequencer and the instrument classes."""
    song = Song(name="PROOF", author="PYGOATTRACKER", copyright="2026")
    song.speedtable = _table(
        (0x00, 0x40),
        (0x80, 0x00),
        (0x82, 0x02),
        (0x03, 0x20),
        (0x06, 0x04),
        (0x83, 0x01),
        (0x04, 0x00),
    )
    song.pulsetable = _table((0x88, 0x00), (0x04, 0x10), (0x04, 0xF0), (0xFF, 0x02))
    song.filtertable = _table(
        (0x90, 0xF1),
        (0x10, 0x02),
        (0xFF, 0x04),
        (0xA0, 0x21),
        (0x00, 0x40),
        (0x08, 0xFE),
        (0xFF, 0x00),
        (0x00, 0x60),
        (0xFF, 0x08),
        (0x90, 0x11),
        (0xFF, 0x0A),
    )
    song.wavetable = _table(
        (0x41, 0x00),
        (0xFF, 0x00),
        (0x03, 0x80),
        (0x21, 0x0C),
        (0x11, 0xA4),
        (0xE1, 0x80),
        (0xF4, 0x04),
        (0xF5, 0x5A),
        (0x00, 0x74),
        (0xFF, 0x04),
        (0x0A, 0x80),
        (0x41, 0x00),
        (0xFF, 0x00),
        (0x02, 0x80),
        (0x81, 0x00),
        (0xFF, 0x00),
    )
    song.instruments = [
        Instrument(0x09, 0x00, 1, 1, 0, 4, 2, 2, 0x09),
        Instrument(0x0A, 0xA9, 11, 0, 0, 0, 0, 0x82, 0x00),
        Instrument(0x21, 0x5A, 14, 0, 0, 1, 0, 0x42, 0xFE),
        Instrument(0x31, 0x6B, 3, 2, 1, 6, 1, 3, 0xFF),
        Instrument(0x09, 0x00, 1, 0, 0, 2, 1, 2, 0x09),
        Instrument(0x09, 0x00, 1, 0, 0, 3, 1, 2, 0x09),
        Instrument(0x09, 0x00, 1, 0, 8, 0, 0, 2, 0x09),
    ]
    effects = _pattern(
        (_C4, 1),
        (_G4, 0, 3, 1),
        (),
        (_C4, 0, 3, 1),
        (),
        (_G4, 0, 3, 7),
        (_C4, 0, 3, 7),
        (_E4, 0, 3, 0),
        (_G4, 0, 3, 2),
        (_REST, 0, 1, 1),
        (_REST, 0, 2, 3),
        (_REST, 0, 1, 2),
        (_E4, 0, 4, 4),
        (),
        (_REST, 0, 4, 6),
        (),
        (_C4, 0, 0, 0),
        (),
        (),
    )
    tables = _pattern(
        (_C4, 4),
        (_REST, 0, 7, 0x21),
        (_REST, 0, 0xD, 0x0C),
        (_REST, 0, 0xD, 0x1F),
        (_REST, 0, 5, 0x0A),
        (_REST, 0, 6, 0xF8),
        (_REST, 0, 8, 3),
        (_REST, 0, 9, 2),
        (_REST, 0, 0xA, 8),
        (_REST, 0, 0xA, 10),
        (_REST, 0, 0xA, 1),
        (_REST, 0, 0xB, 0x00),
        (_REST, 0, 0xB, 0xF2),
        (_REST, 0, 0xC, 0x80),
        (_REST, 0, 0xE, 5),
        (_REST, 0, 0xF, 0x84),
        (_REST, 0, 0xF, 0x01),
        (_REST, 0, 0xF, 0x06),
        (),
    )
    sequencer = _pattern(
        (_C4, 2, 0xF, 0x84),
        (_E4, 3),
        *([()] * 70),
        (_OFF,),
        (_ON,),
        (_G4, 1),
        (_REST, 2),
        (_C4, 3),
        (_G4, 5),
        (),
        (_C4, 6),
        (),
        (_E4, 7),
        (),
    )
    song.patterns = [effects, tables, sequencer, _pattern((_C4, 4), (), ())]
    order = [
        _order(PlayPattern(0), Transpose(2), PlayPattern(3), Transpose(0)),
        _order(Repeat(1), PlayPattern(1), PlayPattern(3), restart=1),
        _order(Transpose(-3), PlayPattern(2), Repeat(2), PlayPattern(3)),
    ]
    order += [_order(*o.entries, restart=o.restart) for o in order][: channels - 3]
    song.subtunes = [Subtune(order), Subtune([_order(PlayPattern(3)) for _ in order])]
    return song


def _variant(song: Song, **keep) -> Song:
    """``song`` without what ``keep`` turns off: ``hr`` (instruments become
    no-hard-restart), ``legato``, ``wavecmd`` (wavetable commands become no
    change), ``effects`` (pattern commands)."""
    if not keep.get("wavecmd", True):
        song.wavetable.left = [
            0 if 0xF0 <= w < 0xFF else w for w in song.wavetable.left
        ]
    for ins in song.instruments:
        if not keep.get("hr", True):
            ins.gateoff_timer |= 0x80
        if not keep.get("legato", True):
            ins.gateoff_timer &= ~0x40
    if not keep.get("effects", True):
        for pattern in song.patterns:
            for row in pattern.rows:
                row.command = row.data = 0
    return song


def _minimal() -> Song:
    """Notes on one instrument and a plain wavetable: no table, effect, gate,
    orderlist command or instrument parameter the optimizing packer keeps."""
    song = Song(name="PLAIN", author="PYGOATTRACKER", copyright="2026")
    song.wavetable = _table(
        (0x41, 0x00), (0x00, 0x80), (0x21, 0x0C), (0x00, 0xA4), (0xFF, 0x00)
    )
    song.instruments = [Instrument(0x09, 0x00, 1, 0, 0, 0, 0, 2, 0x09)]
    song.patterns = [_pattern((_C4, 1), (), (_E4,), (_G4,), *[()] * 4)]
    song.subtunes = [Subtune([_order(PlayPattern(0)) for _ in range(3)])] * 2
    return song


def _full() -> dict:
    return {"proof": (_gt2_song(), (0, 1))}


def _gt2_set(**keep):
    return lambda: {"proof": (_variant(_gt2_song(), **keep), (0, 1))}


def _stereo() -> dict:
    return {"proof": (_gt2_song(6), (0, 1))}


_LOADED: dict = {}


def songs(name: str) -> dict:
    """Song set ``name`` (``set@player`` for a GoatTracker V1.x playroutine's)."""
    if name not in _LOADED:
        base, _, player = name.partition("@")
        _LOADED[name] = _SETS[base](player) if player else _SETS[base]()
    return _LOADED[name]


def loaded() -> dict:
    """The song sets loaded so far, for :func:`preload` in another process."""
    return dict(_LOADED)


def preload(sets: dict) -> None:
    """Take song sets another process loaded (and register the V1.x code)."""
    _gt1_code.register_all()
    _LOADED.update(sets)


def _gt2_corpus() -> dict:
    """The GoatTracker 2 example songs (every subtune) and the songs the oracle
    tunes decode to, and the tests' basic song."""
    out = {"basic": (basic_song(), (0,))}
    for name in EXAMPLE_SONGS:
        song = read_sng(example_song(name))
        out[name] = (song, tuple(range(len(song.subtunes))))
    for name, (relpath, song) in TUNES.items():
        data = Path(resolved(relpath)).read_bytes()
        if recognize_format((GOATTRACKER2,), data) is GOATTRACKER2:
            dec = decompile_sid(data, song - 1)
            out[name] = (dec.typed, (dec.subtune,))
    return out


def _gt1_extend(song, instruments, wave, filters, channels):
    """``song`` with ``instruments``, wavetable rows ``wave`` (instrument wave
    pointers in ``instruments`` count from the first of them as 1), filtertable
    entries ``filters`` and one subtune playing pattern ``channels[c]`` (bytes,
    ended with a rest row: a packed rest cannot end a musicroutine 11.x
    pattern) then a rest on channel ``c``, and looping."""
    base = len(song.wavetable)
    song.instruments = list(song.instruments) + [
        replace(i, wave_ptr=i.wave_ptr + base if i.wave_ptr else 0) for i in instruments
    ]
    song.wavetable = list(song.wavetable) + list(wave)
    song.filtertable = bytes(song.filtertable) + bytes(filters)
    first = len(song.patterns)
    song.patterns = list(song.patterns) + [
        bytes(p) + _n(_GT1_REST) + b"\xff" for p in (*channels, b"")
    ]
    loop = (0xFF,) if song.player in ORDERLIST_ENDSONG else (0xFF, 0x00)
    end = first + len(channels)
    song.orderlists = [[bytes((first + c, end, *loop)) for c in range(len(channels))]]
    return song


def _n(note, instr=0, cmd=0, param=0) -> bytes:
    return bytes((note, instr << 3 | cmd, param))


_GT1_REST = 0x5F


def _gt1_15(player: str) -> dict:
    """make_song and a subtune reaching the V1.5x branches it leaves: the
    porta speed's carry, an even vibrato depth met exactly, toneportamentos
    that arrive within one step either way and one on a rest, a pulse sweep
    turning at its high limit, a filter step without cutoff, a wave loop
    relative to an instrument without a wave program and a wave delay
    entered with the vibrato's counter above it."""
    song = test_gt1.make_song(player)
    first = len(song.instruments) + 1
    song = _gt1_extend(
        song,
        [
            Gt1Instrument(0x09, 0x00, 0x80, 0x40, 0x00, 0x01, 0x00, 1),
            Gt1Instrument(0x09, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 3),
            Gt1Instrument(0x09, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0),
            Gt1Instrument(0x09, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 5),
        ],
        [
            (0x41, 0x00),
            (0xFF, 0x00),
            (0x41, 0x00),
            (0xFF, 0x01),
            (0x03, 0x00),
            (0x21, 0x00),
            (0xFF, 0x00),
        ],
        (0xF2, 0x1F, 0x00, 0x00),
        [
            _n(0x30, first)
            + _n(0x30, 0, 1, 0x41)
            + _n(0x30, 0, 4, 0x24)
            + _n(0x0C, 0, 0, 0)
            + _n(0x0D, 0, 3, 0x10)
            + _n(0x0C, 0, 3, 0x10)
            + _n(_GT1_REST, 0, 3, 0x10)
            + _n(0x30, 0, 0, 0),
            _n(0x30, first)
            + _n(_GT1_REST, 0, 5, len(song.filtertable))
            + _n(0x30, first + 1)
            + _n(_GT1_REST, first + 2)
            + bytes((0xF8,)),
            _n(0x30, first, 4, 0x2F)
            + bytes((0xFC,))
            + _n(0x30, first + 3)
            + bytes((0xF0, 0xF0, 0xF0)),
        ],
    )
    return {"make_song": (test_gt1.make_song(player), (0, 1)), "proof": (song, (0,))}


def _gt1_14(player: str) -> dict:
    """make_song14 and a subtune reaching the V1.4x branches it leaves: a
    vibrato meeting its depth, a toneportamento down that arrives, a wavetable
    row keeping the waveform and a wave loop relative to an instrument
    without a wave program."""
    song = test_gt1.make_song14(player)
    first = len(song.instruments) + 1
    song = _gt1_extend(
        song,
        [
            Gt1Instrument(0x09, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 1),
            Gt1Instrument(0x09, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 3),
            Gt1Instrument(0x09, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0),
        ],
        [(0x41, 0x00), (0x00, 0x0C), (0xFF, 0x00), (0x41, 0x00), (0xFF, 0x01)],
        (),
        [
            _n(0x30, first)
            + _n(0x30, 0, 4, 0x02)
            + bytes((0xFC,))
            + _n(0x0D, 0, 0, 0)
            + _n(0x0C, 0, 3, 0x10)
            + bytes((0xFC,)),
            _n(0x30, first + 1) + _n(_GT1_REST, first + 2) + bytes((0xFC,)),
            _n(0x30, first) + bytes((0xFC,)),
        ],
    )
    return {
        "make_song14": (test_gt1.make_song14(player), (0, 1)),
        "proof": (song, (0,)),
    }


def _gt1_11_proof(player: str):
    """make_song11's data and a subtune reaching the musicroutine 11.x branches
    it leaves: a slow arpeggio, a vibrato meeting its depth, toneportamentos
    arriving from below and above within the target's frequency page, from
    above across it and upward from above it, the last channel ending its call on a continuous effect,
    a sustain set under a sound effect, a wavetable row keeping the waveform, a
    wave loop relative to an instrument without a wave program and a hard
    restart timer; the sound effect stays the last pattern."""
    song = test_gt1.make_song11(player)
    sfx = song.patterns[-1]
    first = len(song.instruments) + 1
    song = _gt1_extend(
        song,
        [
            Gt1Instrument(0x09, 0x00, 0x08, 0x20, 0x00, 0x0F, 0x00, 1),
            Gt1Instrument(0x09, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 3),
            Gt1Instrument(0x09, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0),
        ],
        [(0x41, 0x00), (0x00, 0x00), (0xFF, 0x00), (0x41, 0x00), (0xFF, 0x01)],
        (),
        [
            _n(0x30, first, 0, 0x85)
            + bytes((0xFC,))
            + _n(0x30, 0, 4, 0x02)
            + bytes((0xFC,))
            + _n(0x0C, 0, 0, 0)
            + bytes((0xFE,))
            + _n(0x0D, 0, 3, 0x01)
            + bytes((0xFC,))
            + _n(0x0C, 0, 3, 0x83)
            + bytes((0xFE,))
            + _n(0x0D, 0, 0, 0)
            + bytes((0xFE,))
            + _n(0x0C, 0, 3, 0xFF)
            + bytes((0xFE,))
            + _n(0x18, 0, 0, 0)
            + bytes((0xFE,))
            + _n(0x0C, 0, 3, 0x01)
            + bytes((0xFE,)),
            _n(_GT1_REST, 0, 6, 0x88)
            + _n(_GT1_REST, 0, 6, 0x89)
            + _n(0x30, first + 1)
            + _n(_GT1_REST, first + 2)
            + bytes((0xFC,)),
            _n(0x30, first, 1, 0x04)
            + bytes((0xFC,))
            + _n(0x30, 0, 4, 0x24)
            + bytes((0xFC,)),
        ],
    )
    song.patterns.append(sfx)
    timer = song.ref.labels.get("mt_hrtimer")
    if timer is not None:
        song.state = {**song.state, timer: 3}
    return song


def _gt1_11(player: str) -> dict:
    out = {
        "make_song11": (test_gt1.make_song11(player), (0, 1)),
        "proof": (_gt1_11_proof(player), (0,)),
    }
    if player in MUSICROUTINE11_SFX:
        out["with_sfx"] = (test_gt1.with_sfx(test_gt1.make_song11(player)), (0,))
        out["proof_sfx"] = (test_gt1.with_sfx(_gt1_11_proof(player)), (0,))
    return out


def _tests_songs_15(player: str) -> dict:
    """make_song of ``player``."""
    return {"make_song": (test_gt1.make_song(player), (0, 1))}


def _tests_songs_14(player: str) -> dict:
    """make_song14 of ``player``."""
    return {"make_song14": (test_gt1.make_song14(player), (0, 1))}


def _tests_songs_11(player: str) -> dict:
    """make_song11 of ``player`` (and with a sound effect where it plays one)."""
    out = {"make_song11": (test_gt1.make_song11(player), (0, 1))}
    if player in MUSICROUTINE11_SFX:
        out["with_sfx"] = (test_gt1.with_sfx(test_gt1.make_song11(player)), (0,))
    return out


def _gt1_corpus(player: str) -> dict:
    """The tests' synthetic songs of the release's family, the example songs of
    the release that ships ``player`` and the oracle tunes of ``player``."""
    family = reference(player).family
    out = {
        "1.5": _tests_songs_15,
        "1.4": _tests_songs_14,
        "11": _tests_songs_11,
    }[
        family
    ](player)
    if player == "1.53":
        for member in GOATTRK_SONGS:
            out[member] = (parse_gts1(goattrk_song(member), player), (0,))
    elif player in _gt1_code.ARCHIVE and family == "11":
        for name in GOATOLD_SONGS:
            out[name] = (parse_gts1(goatold_song(name), player), (0,))
    for name, (relpath, song) in TUNES.items():
        if name.startswith("gt1_"):
            data = Path(resolved(relpath)).read_bytes()
            decoded = decompile_gt1(data, song - 1)
            if decoded.player == player:
                out[name] = (decoded, (song - 1,))
    return out


_SETS = {
    "gt2": _full,
    "gt2_plain": _gt2_set(wavecmd=False, effects=False),
    "gt2_nohr": _gt2_set(hr=False),
    "gt2_nolegato": _gt2_set(legato=False),
    "gt2_nowavecmd": _gt2_set(wavecmd=False),
    "gt2_minimal": lambda: {"plain": (_minimal(), (0, 1))},
    "stereo": _stereo,
    "gt2_corpus": _gt2_corpus,
    "gt1_15": _gt1_15,
    "gt1_14": _gt1_14,
    "gt1_11": _gt1_11,
    "gt1_corpus": _gt1_corpus,
}
