"""Typed GoatTracker V1.x: every byte of a :class:`~pygoattracker.gt1.Gt1Song` as an operation.

Command and orderlist bytes are what the playroutine family reads them as; data
keeps its stored value, in the unit of the song's form (editor or packed, see
``docs/gt1.md``). The player image is not song data: ``to_song`` takes it from ``image``.
"""

from dataclasses import dataclass, field
from functools import partial
from typing import NamedTuple

from pysidtracker.bytegrammar import fits

from pygoattracker._gt1_refs import PLAYERS
from pygoattracker import gt1streams as streams
from pygoattracker.errors import SngParseError, SngValidationError
from pygoattracker.gt1 import (
    ORDERLIST_ENDSONG,
    ORDERLIST_LOOP_AFTER,
    PLAYER_ORG,
    SEGMENTS_V11,
    SEGMENTS_V15,
    Gt1Instrument,
    Gt1Song,
)
from pygoattracker.gt1sng import LAYOUT_V093, Gt1Sng
from pygoattracker.model import PlayPattern, Repeat, Transpose
from pygoattracker.typed import (
    Delay,
    Jump,
    KeyOff,
    Note,
    Rest,
    Row,
    Stop,
    WaveNote,
)

_fits = partial(fits, error=SngValidationError)

_KEYOFF, _REST, _NOCMD, _END = 0x5E, 0x5F, 0x60, 0xFF
_REPEAT, _TRANSDOWN, _TRANSUP = 0xD0, 0xE0, 0xF0

# ---- commands: the low three bits of a row's command byte -------------------


@dataclass(frozen=True, slots=True)
class Arpeggio:
    """0: two halftone offsets (bits 4-6 and 0-3) cycled with the note; 0 none. Bit 7
    halves the speed (editor, musicroutine packed; the V1.5x relocator inverts it)."""

    data: int


@dataclass(frozen=True, slots=True)
class PortamentoUp:
    """1 (standard): slide the frequency up each tick."""

    data: int


@dataclass(frozen=True, slots=True)
class PortamentoDown:
    """2 (standard): slide the frequency down each tick."""

    data: int


@dataclass(frozen=True, slots=True)
class FreqSlide:
    """1 (musicroutine): slide the frequency, down when bit 7 is set."""

    data: int


@dataclass(frozen=True, slots=True)
class CutoffSlide:
    """2 (musicroutine): add ``data`` to the filter cutoff each tick."""

    data: int


@dataclass(frozen=True, slots=True)
class TonePortamento:
    """3: slide to the row's note: standard step ``data * 4``, 0 ties at once;
    musicroutine step ``(data & $7F) * 2``, bit 7 the direction."""

    data: int


@dataclass(frozen=True, slots=True)
class Vibrato:
    """4: vibrato: half-period in the low nibble (packed; high in the editor), pitch
    step the byte (V1.5x: its high nibble)."""

    data: int


@dataclass(frozen=True, slots=True)
class SetFilter:
    """5 (standard): run the filtertable from step ``data``."""

    data: int


@dataclass(frozen=True, slots=True)
class SetFilterCtrl:
    """5 (musicroutine): filter resonance and routing ($D417)."""

    data: int


@dataclass(frozen=True, slots=True)
class SetSR:
    """6: sustain/release register."""

    data: int


@dataclass(frozen=True, slots=True)
class SetTempo:
    """7: tempo, below $80 every channel's, else this channel's; $C0-$FF a timing mark
    (11.3-11.5, V1.4x); V1.5x: $00/$01 funktempo, $EF timing mark, $F0-$FF volume."""

    data: int


STANDARD = (
    Arpeggio,
    PortamentoUp,
    PortamentoDown,
    TonePortamento,
    Vibrato,
    SetFilter,
    SetSR,
    SetTempo,
)
MUSICROUTINE = (
    Arpeggio,
    FreqSlide,
    CutoffSlide,
    TonePortamento,
    Vibrato,
    SetFilterCtrl,
    SetSR,
    SetTempo,
)
_NUMBER = {
    cls: num for table in (STANDARD, MUSICROUTINE) for num, cls in enumerate(table)
}


def family(player: str) -> str:
    """The playroutine design: ``1.5``, ``1.4`` or ``11`` (V0.9x songs are ``11``'s forebear)."""
    return PLAYERS[player]["family"] if player in PLAYERS else "11"


def commands(player: str) -> tuple:
    """The command of each number 0-7 for ``player``."""
    return MUSICROUTINE if family(player) == "11" else STANDARD


# ---- orderlists -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Loop:
    """``$FF`` and a restart byte: continue at entry ``restart``."""

    restart: int


@dataclass(frozen=True, slots=True)
class End:
    """An end mark with no restart byte: ``$FE`` repeats the last pattern; before
    V1.15, ``$FF`` loops to the start."""

    mark: int


@dataclass(frozen=True)
class Orderlist:
    """One channel's orderlist: patterns, and repeats and transposes in the standard family."""

    entries: tuple = ()
    end: Loop | End = Loop(0)


def transup(player: str) -> int:
    """The orderlist byte of a zero transpose: ``$EF`` in V1.50, else ``$F0``."""
    return _TRANSUP - (player in ORDERLIST_LOOP_AFTER)


def entry_of(byte: int, standard: bool, zero: int = _TRANSUP):
    """An orderlist byte: the standard family's ``$D0-$FE`` repeat (the next pattern
    plays ``count + 1`` times) and transpose (``zero`` is no transpose)."""
    if not standard or byte < _REPEAT:
        return PlayPattern(byte)
    if byte < _TRANSDOWN:
        return Repeat(byte - _REPEAT)
    return Transpose(byte - zero)


def entry_byte(entry, zero: int = _TRANSUP) -> int:
    """The orderlist byte of ``entry``."""
    match entry:
        case Repeat(count):
            return _REPEAT + count
        case Transpose(semitones):
            return (zero + semitones) & 0xFF
    return entry.num


def _orderlist(blob: bytes, standard: bool, no_restart: bool, editor: bool, zero):
    rule = streams.order_rule(standard, editor, no_restart)
    body, (kind, value) = streams.STREAMS.parse_exact(blob, rule)
    end = Loop(value) if kind == "loop" else End(value)
    return Orderlist(tuple(entry_of(b, standard, zero) for b in body), end)


def _orderlist_bytes(order: Orderlist, zero: int) -> bytes:
    end = order.end
    tail = (end.mark,) if isinstance(end, End) else (_END, end.restart)
    return bytes([*(entry_byte(e, zero) for e in order.entries), *tail])


# ---- instruments and tables -------------------------------------------------


@dataclass(frozen=True, slots=True)
class Instrument:
    """An instrument row: envelope, pulse program, filter and wavetable entry point.
    Bit 0 set skips the hard restart: of ``pulse`` in a V1.4b-V1.53 editor song, of
    ``pulse_speed`` packed."""

    attack_decay: int = 0
    sustain_release: int = 0
    pulse: int = 0
    pulse_speed: int = 0
    pulse_low: int = 0
    pulse_high: int = 0
    filter: int = 0
    wave_ptr: int = 0
    name: str = ""


@dataclass(frozen=True, slots=True)
class WaveStep:
    """A wavetable row: a waveform, a delay (V1.5x $01-$07, one tick more each) or
    none (0: keep it; ends a V0.9x table), and the note it plays."""

    wave: int | Delay | None
    note: WaveNote


@dataclass(frozen=True, slots=True)
class FilterSet:
    """A filter step: $D417 control, $D418 mode/volume, cutoff (0 keeps it), next step."""

    ctrl: int
    mode: int
    cutoff: int
    next: int


@dataclass(frozen=True, slots=True)
class FilterSlide:
    """A filter step: add ``speed`` (V1.4x: subtract) to the cutoff for ``time`` ticks,
    then the next step. V1.5x step 0 holds the funktempo pair in ``speed`` and ``next``.
    """

    time: int
    speed: int
    next: int


def wave_row(wave: int, note: int, delays: bool = False):
    """A wavetable row; a ``$FF`` waveform jumps to the instrument's own row ``note``;
    ``delays``: waveforms $01-$07 delay (V1.5x)."""
    if wave == _END:
        return Jump(note) if note else Stop()
    step = None if not wave else Delay(wave) if delays and wave < 8 else wave
    return WaveStep(
        step, WaveNote(note, True) if note < 0x80 else WaveNote(note & 0x7F, False)
    )


def wave_pair(row) -> tuple:
    """The ``(wave, note)`` bytes of a wavetable row."""
    match row:
        case Stop():
            return _END, 0
        case Jump(target):
            return _END, target
    note, wave = row.note, row.wave
    wave = 0 if wave is None else wave.ticks if isinstance(wave, Delay) else wave
    return wave, note.value if note.relative else 0x80 | note.value


def filter_step(ctrl: int, b1: int, b2: int, nxt: int):
    """A four-byte filtertable step."""
    return FilterSet(ctrl, b1, b2, nxt) if ctrl else FilterSlide(b1, b2, nxt)


def filter_bytes(step) -> bytes:
    """The four bytes of a filtertable step."""
    if isinstance(step, FilterSet):
        return bytes((step.ctrl, step.mode, step.cutoff, step.next))
    return bytes((0, step.time, step.speed, step.next))


def _ins_fields(ins: Instrument) -> list:
    return [getattr(ins, f) for f in Instrument.__slots__[:-1]]


# ---- patterns ---------------------------------------------------------------


@dataclass(frozen=True)
class Pattern:
    """Rows, then the bytes that end the pattern: the end mark (an editor's
    ``$FF`` row), or the bytes a load cut short."""

    rows: tuple = ()
    end: bytes = b"\xff"


def _note(byte: int):
    return KeyOff() if byte == _KEYOFF else Rest() if byte == _REST else Note(byte)


def _note_byte(note) -> int:
    if isinstance(note, KeyOff):
        return _KEYOFF
    return _REST if isinstance(note, Rest) else note.pitch


def _row(note: int, fx: int, data: int, cmds) -> Row:
    return Row(_note(note), fx >> 3 or None, cmds[fx & 7](data))


def _row_bytes(row: Row) -> bytes:
    if row.command is None:
        raise SngParseError(f"a row with an instrument needs a command: {row}")
    num = _NUMBER[type(row.command)]
    return bytes(
        (_note_byte(row.note), (row.instrument or 0) << 3 | num, row.command.data)
    )


def _typed_row(raw, cmds) -> Row:
    kind, *values = raw
    if kind == "cmd":
        return _row(*values, cmds)
    if kind == "note":
        return Row(_note(values[0]))
    return Row(Rest(), None, None, values[0])


def pattern_of(blob: bytes, cmds, editor: bool) -> Pattern:
    """An editor pattern (three-byte rows, the ``$FF`` end row) or a packed one
    (note-only rows that keep the command and rest runs too; ``$FF`` after a row
    ends it, else the end of the data cuts it)."""
    rows, end = streams.pattern_rows(blob, editor)
    return Pattern(tuple(_typed_row(r, cmds) for r in rows), end)


def editor_pattern_bytes(pattern: Pattern) -> bytes:
    """An editor pattern's bytes."""
    return b"".join(_row_bytes(r) for r in pattern.rows) + pattern.end


def packed_pattern_bytes(pattern: Pattern) -> bytes:
    """A packed pattern's bytes."""
    out = bytearray()
    for row in pattern.rows:
        if row.length > 1:
            out.append(0x100 - row.length)
        elif row.command is None and row.instrument is None:
            out.append(_NOCMD + _note_byte(row.note))
        else:
            out += _row_bytes(row)
    return bytes(out + pattern.end)


# ---- the song ---------------------------------------------------------------


class _Reading(NamedTuple):
    """How a song's playroutine and form read its bytes."""

    cmds: tuple
    standard: bool
    editor: bool
    no_restart: bool
    zero: int
    delays: bool

    @classmethod
    def of(cls, player: str, layout: str | None) -> "_Reading":
        """The reading of ``player`` songs in editor ``layout`` (None: packed)."""
        cmds, editor = commands(player), layout is not None
        no_restart = layout == LAYOUT_V093 if editor else player in ORDERLIST_ENDSONG
        delays = family(player) == "1.5"
        return cls(cmds, cmds is STANDARD, editor, no_restart, transup(player), delays)

    def orderlist(self, blob: bytes) -> Orderlist:
        """The orderlist ``blob`` holds."""
        return _orderlist(blob, self.standard, self.no_restart, self.editor, self.zero)

    def pattern(self, blob: bytes) -> "Pattern":
        """The pattern ``blob`` holds."""
        return pattern_of(blob, self.cmds, self.editor)


_IMAGE = ("address", "author", "state", "data", "initialized", "segments")


@dataclass(frozen=True)
class Gt1TypedSong:
    """A V1.x song as operations. ``layout``: a ``GTS!`` song's editor layout (None:
    packed); ``params``: the packer's parameter slots (tick, gate timer, hard-restart
    AD and SR); ``filtertail``: bytes past the last whole filtertable step."""

    player: str
    layout: str | None = None
    name: str = ""
    author: str = ""
    copyright: str = ""
    params: tuple = ()
    subtunes: tuple = ()
    instruments: tuple = ()
    wavetable: tuple = ()
    filtertable: tuple = ()
    filtertail: bytes = b""
    patterns: tuple = field(default=())

    @property
    def editor(self) -> bool:
        """An editor (``GTS!``) song rather than packed data."""
        return self.layout is not None

    @classmethod
    def from_song(cls, song: Gt1Song) -> "Gt1TypedSong":
        """The typed form of ``song``."""
        sng = song.sng
        editor = sng is not None
        read = _Reading.of(song.player, sng.layout if editor else None)
        filt, whole = song.filtertable, len(song.filtertable) & ~3
        header = (sng.name, sng.author, sng.copyright) if editor else (b"",) * 3
        return cls(
            song.player,
            sng.layout if editor else None,
            *(text.decode("latin-1") for text in header),
            params=tuple(song.params),
            subtunes=tuple(
                tuple(read.orderlist(o) for o in chans) for chans in song.orderlists
            ),
            instruments=tuple(
                Instrument(*ins.to_bytes(), ins.name.rstrip(b"\0").decode("latin-1"))
                for ins in song.instruments
            ),
            wavetable=tuple(wave_row(w, n, read.delays) for w, n in song.wavetable),
            filtertable=tuple(
                filter_step(*filt[k : k + 4]) for k in range(0, whole, 4)
            ),
            filtertail=bytes(filt[whole:]),
            patterns=tuple(read.pattern(p) for p in song.patterns),
        )

    def check(self) -> None:
        """Raise :class:`~pygoattracker.errors.SngValidationError` unless every value
        fits its field as this playroutine and form read it."""
        read = _Reading.of(self.player, self.layout)
        latin1 = (lambda s: s.encode("latin-1"), lambda raw: raw.decode("latin-1"))
        for text in (self.name, self.author, self.copyright):
            _fits(text, *latin1, "header")
        _fits(tuple(self.params), lambda p: p, tuple, "params")
        for num, ins in enumerate(self.instruments, 1):
            what = f"instrument {num}"
            _fits(ins, _ins_fields, lambda raw, n=ins.name: Instrument(*raw, n), what)
            _fits(ins.name, *latin1, what)
        for num, row in enumerate(self.wavetable, 1):
            _fits(
                row,
                wave_pair,
                lambda raw: wave_row(*raw, read.delays),
                f"wavetable row {num}",
            )
        for num, step in enumerate(self.filtertable):
            _fits(
                step,
                filter_bytes,
                lambda raw: filter_step(*raw),
                f"filtertable step {num}",
            )
        for s_num, chans in enumerate(self.subtunes):
            for c_num, order in enumerate(chans):
                encode = lambda o: _orderlist_bytes(o, read.zero)
                _fits(
                    order, encode, read.orderlist, f"subtune {s_num} orderlist {c_num}"
                )
        encode = editor_pattern_bytes if read.editor else packed_pattern_bytes
        end = b"\xff\x00\x00" if read.editor else b"\xff"
        alone = lambda row: encode(Pattern((row,), end))
        first = lambda raw: next(iter(read.pattern(raw).rows), None)
        for p_num, pattern in enumerate(self.patterns):
            for r_num, row in enumerate(pattern.rows):
                _fits(row, alone, first, f"pattern {p_num} row {r_num}")
            _fits(pattern, encode, read.pattern, f"pattern {p_num}")

    def to_song(self, image: Gt1Song | None = None) -> Gt1Song:
        """The :class:`Gt1Song`, its player image from ``image`` (None: the
        playroutine as assembled at ``$1000``)."""
        editor, zero = self.editor, transup(self.player)
        pattern = editor_pattern_bytes if editor else packed_pattern_bytes
        header = (self.name, self.author, self.copyright)
        song = Gt1Song(
            player=self.player,
            address=PLAYER_ORG,
            params=tuple(self.params),
            instruments=[
                Gt1Instrument(
                    *_ins_fields(i),
                    name=i.name.encode("latin-1").ljust(16, b"\0") if editor else b"",
                )
                for i in self.instruments
            ],
            wavetable=[wave_pair(r) for r in self.wavetable],
            filtertable=b"".join(filter_bytes(s) for s in self.filtertable)
            + self.filtertail,
            orderlists=[[_orderlist_bytes(o, zero) for o in s] for s in self.subtunes],
            patterns=[pattern(p) for p in self.patterns],
            segments=SEGMENTS_V11 if family(self.player) == "11" else SEGMENTS_V15,
            sng=(
                Gt1Sng(*(t.encode("latin-1") for t in header), self.layout)
                if editor
                else None
            ),
        )
        if image is not None:
            for attr in _IMAGE:
                setattr(song, attr, getattr(image, attr))
        return song
