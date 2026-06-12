"""Typed model of a GoatTracker 2 song.

A :class:`Song` holds everything a .SNG file stores: header strings, one
or more subtunes (each with three channel orderlists), shared patterns,
instruments and the four tables (wave/pulse/filter/speed). You describe
content; byte packing is handled by :mod:`pygoattracker.writer`.
"""

from dataclasses import dataclass, field

from pygoattracker import constants
from pygoattracker.errors import GoatTrackerError


@dataclass(frozen=True)
class PlayPattern:
    """Orderlist entry: play pattern ``num`` (0-207)."""

    num: int

    def to_byte(self) -> int:
        """On-disk orderlist byte."""
        if not 0 <= self.num < constants.MAX_PATT:
            raise GoatTrackerError(f"pattern number out of range: {self.num}")
        return self.num


@dataclass(frozen=True)
class Repeat:
    """Orderlist entry: repeat the following pattern.

    ``count`` is the raw 0-15 value from the orderlist byte ``$DX``; the
    playroutine plays the next pattern ``count + 1`` times in total.
    """

    count: int

    def to_byte(self) -> int:
        """On-disk orderlist byte."""
        if not 0 <= self.count <= 0x0F:
            raise GoatTrackerError(f"repeat count out of range: {self.count}")
        return constants.REPEAT + self.count


@dataclass(frozen=True)
class Transpose:
    """Orderlist entry: transpose following patterns by -16..+14 halftones.

    The editor only enters -15..+14; byte ``$E0`` (-16) is still
    accepted for round-trip fidelity.
    """

    semitones: int

    def to_byte(self) -> int:
        """On-disk orderlist byte."""
        if not -17 < self.semitones < 15:
            raise GoatTrackerError(f"transpose out of range: {self.semitones}")
        return (constants.TRANSUP + self.semitones) & 0xFF


OrderEntry = PlayPattern | Repeat | Transpose


def entry_from_byte(value: int) -> OrderEntry:
    """Decode one orderlist byte into a typed entry."""
    if 0 <= value < constants.REPEAT:
        return PlayPattern(value)
    if constants.REPEAT <= value < constants.TRANSDOWN:
        return Repeat(value - constants.REPEAT)
    if constants.TRANSDOWN <= value < constants.LOOPSONG:
        return Transpose(value - constants.TRANSUP)
    raise GoatTrackerError(f"not an orderlist entry byte: {value:#04x}")


@dataclass
class Orderlist:
    """One channel's orderlist plus its restart position."""

    entries: list[OrderEntry] = field(default_factory=lambda: [PlayPattern(0)])
    restart: int = 0


@dataclass
class Subtune:
    """Three channel orderlists."""

    channels: list[Orderlist] = field(
        default_factory=lambda: [Orderlist() for _ in range(constants.MAX_CHN)]
    )


@dataclass
class Row:
    """One pattern row: note, instrument (0 = no change), command, data."""

    note: int = constants.REST
    instrument: int = 0
    command: int = 0
    data: int = 0

    def __str__(self) -> str:
        return (
            f"{constants.note_name(self.note)} "
            f"{self.instrument:02X}{self.command:X}{self.data:02X}"
        )


@dataclass
class Pattern:
    """A single-channel pattern of up to 128 rows."""

    rows: list[Row] = field(default_factory=lambda: [Row() for _ in range(64)])

    @classmethod
    def empty(cls, length: int = 64) -> "Pattern":
        """A pattern of ``length`` rest rows (GoatTracker's default)."""
        return cls(rows=[Row() for _ in range(length)])


@dataclass
class Instrument:
    """Instrument parameters, as in the instrument editor.

    Table pointers are 1-based positions into the song tables (0 = table
    execution not used). ``gateoff_timer`` bit $80 disables hard restart
    and bit $40 disables gateoff.
    """

    attack_decay: int = 0
    sustain_release: int = 0
    wave_ptr: int = 0
    pulse_ptr: int = 0
    filter_ptr: int = 0
    vibrato_param: int = 0
    vibrato_delay: int = 0
    gateoff_timer: int = 0
    first_wave: int = 0
    name: str = ""


@dataclass
class Table:
    """Left/right byte columns of a wave/pulse/filter/speed table."""

    left: list[int] = field(default_factory=list)
    right: list[int] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.left)

    def add(self, left: int, right: int) -> int:
        """Append a row and return its 1-based table pointer."""
        if len(self.left) >= constants.MAX_TABLELEN:
            raise GoatTrackerError("table full")
        self.left.append(left)
        self.right.append(right)
        return len(self.left)


def _default_subtunes() -> list[Subtune]:
    return [Subtune()]


def _default_patterns() -> list[Pattern]:
    return [Pattern.empty()]


@dataclass
class Song:
    """A complete GoatTracker 2 song.

    ``instruments[0]`` is instrument number 1: the empty instrument 0 is
    implicit and never stored in a .SNG file. Use :meth:`instrument` to
    look up by tracker instrument number.
    """

    name: str = ""
    author: str = ""
    copyright: str = ""
    subtunes: list[Subtune] = field(default_factory=_default_subtunes)
    instruments: list[Instrument] = field(default_factory=list)
    patterns: list[Pattern] = field(default_factory=_default_patterns)
    wavetable: Table = field(default_factory=Table)
    pulsetable: Table = field(default_factory=Table)
    filtertable: Table = field(default_factory=Table)
    speedtable: Table = field(default_factory=Table)

    def instrument(self, num: int) -> Instrument:
        """Instrument by tracker number (1-based; empty if not defined)."""
        if 1 <= num <= len(self.instruments):
            return self.instruments[num - 1]
        return Instrument()

    def tables(self) -> tuple[Table, Table, Table, Table]:
        """The four tables in on-disk order (wave, pulse, filter, speed)."""
        return (self.wavetable, self.pulsetable, self.filtertable, self.speedtable)
