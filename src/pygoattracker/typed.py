"""Typed GoatTracker 2: every byte of a song as the operation the player gives it.

:class:`~pygoattracker.model.Song` holds the editor's bytes; :class:`TypedSong`
states what they mean, per GoatTracker 2.76's ``readme.txt`` and ``player.s``,
and converts both ways without loss (``TypedSong.from_song(s).to_song() == s``).
"""

from dataclasses import dataclass, field

from pygoattracker import constants as C
from pygoattracker.model import Instrument as RawInstrument
from pygoattracker.model import Orderlist, Pattern, Song, Subtune, Table
from pygoattracker.model import Row as RawRow

# ---- the note column --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Note:
    """Key a note: ``pitch`` semitones above C-0."""

    pitch: int


@dataclass(frozen=True, slots=True)
class Rest:
    """No new note: the voice keeps sounding (or keeps silent)."""


@dataclass(frozen=True, slots=True)
class KeyOff:
    """Clear the gate bit: the envelope releases."""


@dataclass(frozen=True, slots=True)
class KeyOn:
    """Set the gate bit without a new note."""


NoteColumn = Note | Rest | KeyOff | KeyOn
_NOTE_BYTE = {C.REST: Rest(), C.KEYOFF: KeyOff(), C.KEYON: KeyOn()}


def note_of(byte: int) -> NoteColumn:
    """The note column byte ``$60-$BF`` as what it does."""
    if C.FIRSTNOTE <= byte <= C.LASTNOTE:
        return Note(byte - C.FIRSTNOTE)
    return _NOTE_BYTE[byte]


def note_byte(note: NoteColumn) -> int:
    """The note column byte for ``note``."""
    if isinstance(note, Note):
        return C.FIRSTNOTE + note.pitch
    return {type(v): k for k, v in _NOTE_BYTE.items()}[type(note)]


# ---- commands: pattern column 0XY-FXY, and wavetable rows $F0-$FE ------------


@dataclass(frozen=True, slots=True)
class DoNothing:
    """0XY: no command; the instrument's vibrato runs. ``data`` is ignored."""

    data: int = 0


@dataclass(frozen=True, slots=True)
class PortamentoUp:
    """1XY: slide the frequency up by speedtable row ``speed`` per tick."""

    speed: int


@dataclass(frozen=True, slots=True)
class PortamentoDown:
    """2XY: slide the frequency down by speedtable row ``speed`` per tick."""

    speed: int


@dataclass(frozen=True, slots=True)
class TonePortamento:
    """3XY: slide to the row's note at speedtable row ``speed``; 0 ties at once."""

    speed: int


@dataclass(frozen=True, slots=True)
class Vibrato:
    """4XY: vibrato with speedtable row ``speed``: its rate and its depth."""

    speed: int


@dataclass(frozen=True, slots=True)
class SetAD:
    """5XY: attack/decay register."""

    value: int


@dataclass(frozen=True, slots=True)
class SetSR:
    """6XY: sustain/release register."""

    value: int


@dataclass(frozen=True, slots=True)
class SetWave:
    """7XY, a wavetable waveform, an instrument's first wave: the waveform register."""

    wave: int


@dataclass(frozen=True, slots=True)
class SetWavePtr:
    """8XY: run the wavetable from ``row``; 0 stops it."""

    row: int


@dataclass(frozen=True, slots=True)
class SetPulsePtr:
    """9XY: run the pulsetable from ``row``; 0 stops it."""

    row: int


@dataclass(frozen=True, slots=True)
class SetFilterPtr:
    """AXY: run the filtertable from ``row``; 0 stops it."""

    row: int


@dataclass(frozen=True, slots=True)
class SetFilterCtrl:
    """BXY: resonance (high nibble) and the voices routed through the filter."""

    resonance: int
    channels: int


@dataclass(frozen=True, slots=True)
class SetCutoff:
    """CXY: filter cutoff, the high 8 bits."""

    cutoff: int


@dataclass(frozen=True, slots=True)
class SetMasterVolume:
    """DXY below $10: master volume."""

    volume: int


@dataclass(frozen=True, slots=True)
class TimingMark:
    """DXY from $10: a timing mark the player writes to the author area, not the chip."""

    value: int


@dataclass(frozen=True, slots=True)
class FunkTempo:
    """EXY: alternate the two tempos speedtable row ``speed`` holds."""

    speed: int


@dataclass(frozen=True, slots=True)
class SetTempo:
    """FXY: ticks per row; ``channel`` sets this voice's alone. 0 and 1 recall the funk pair."""

    tempo: int
    channel: bool = False


Command = (
    DoNothing
    | PortamentoUp
    | PortamentoDown
    | TonePortamento
    | Vibrato
    | SetAD
    | SetSR
    | SetWave
    | SetWavePtr
    | SetPulsePtr
    | SetFilterPtr
    | SetFilterCtrl
    | SetCutoff
    | SetMasterVolume
    | TimingMark
    | FunkTempo
    | SetTempo
)
_ONE = (
    DoNothing,
    PortamentoUp,
    PortamentoDown,
    TonePortamento,
    Vibrato,
    SetAD,
    SetSR,
    SetWave,
    SetWavePtr,
    SetPulsePtr,
    SetFilterPtr,
    None,
    SetCutoff,
    None,
    FunkTempo,
    None,
)
SPEED_COMMANDS = (PortamentoUp, PortamentoDown, TonePortamento, Vibrato, FunkTempo)


def command_of(num: int, data: int) -> Command:
    """Command number ``num`` with ``data`` as what it does."""
    cls = _ONE[num]
    if cls is not None:
        return cls(data)
    if num == C.CMD_SETFILTERCTRL:
        return SetFilterCtrl(data >> 4, data & 0x0F)
    if num == C.CMD_SETMASTERVOL:
        return SetMasterVolume(data) if data < 0x10 else TimingMark(data)
    return SetTempo(data & 0x7F, bool(data & 0x80))


def command_bytes(cmd: Command) -> tuple[int, int]:
    """``(number, data)`` for ``cmd``."""
    match cmd:
        case SetFilterCtrl(res, chans):
            return C.CMD_SETFILTERCTRL, res << 4 | chans
        case SetMasterVolume(v) | TimingMark(v):
            return C.CMD_SETMASTERVOL, v
        case SetTempo(t, chan):
            return C.CMD_SETTEMPO, t | (0x80 if chan else 0)
    return _ONE.index(type(cmd)), getattr(cmd, cmd.__slots__[0])


# ---- a pattern row ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Row:
    """One row: a note column, an instrument, a command, and the rows a rest spans.

    ``instrument`` None keeps the voice's; ``command`` None keeps the command the
    voice holds (the packed grammar writes one only where it changes).
    """

    note: NoteColumn = Rest()
    instrument: int | None = None
    command: Command | None = None
    length: int = 1


# ---- table rows -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Jump:
    """Continue at ``row`` of the same table."""

    row: int


@dataclass(frozen=True, slots=True)
class Stop:
    """End the table program (a jump to row 0)."""


@dataclass(frozen=True, slots=True)
class Delay:
    """Hold this wavetable row ``ticks`` more ticks before it runs."""

    ticks: int


@dataclass(frozen=True, slots=True)
class WaveNote:
    """A wavetable note: ``value`` semitones from the row's note, or an absolute note."""

    value: int
    relative: bool


@dataclass(frozen=True, slots=True)
class WaveStep:
    """A wavetable row: a waveform (or a delay, or neither) and a note (or none)."""

    wave: SetWave | Delay | None = None
    note: WaveNote | None = None


@dataclass(frozen=True, slots=True)
class WaveCommand:
    """A wavetable row that runs a pattern command for this tick."""

    command: Command


@dataclass(frozen=True, slots=True)
class PulseSet:
    """Set the pulse width; the chip reads its low 12 bits."""

    width: int


@dataclass(frozen=True, slots=True)
class PulseMod:
    """Add ``speed`` to the pulse width each tick for ``time`` ticks."""

    time: int
    speed: int


@dataclass(frozen=True, slots=True)
class SimplePulseSet:
    """SIMPLEPULSE build: one byte for both pulse registers, the chip reads $X0YY as $0YYY."""

    value: int


@dataclass(frozen=True, slots=True)
class SimplePulseMod:
    """SIMPLEPULSE build: an 8-bit add with end-around carry, ``time`` ticks."""

    time: int
    step: int


@dataclass(frozen=True, slots=True)
class FilterSet:
    """Filter mode: ``passband`` bits $10 low, $20 band, $40 high; resonance; voices."""

    passband: int
    resonance: int
    channels: int


@dataclass(frozen=True, slots=True)
class FilterMod:
    """Add ``speed`` to the cutoff each tick for ``time`` ticks."""

    time: int
    speed: int


@dataclass(frozen=True, slots=True)
class CutoffSet:
    """Set the filter cutoff."""

    cutoff: int


@dataclass(frozen=True, slots=True)
class FixedSpeed:
    """A speedtable row as a 16-bit value: a slide's step; vibrato rate (high) and depth."""

    value: int

    @property
    def rate(self) -> int:
        return self.value >> 8

    @property
    def depth(self) -> int:
        return self.value & 0xFF


@dataclass(frozen=True, slots=True)
class CalculatedSpeed:
    """A speedtable row the player computes: the note's semitone interval ``>> shift``."""

    rate: int
    shift: int


WaveRow = WaveStep | WaveCommand | Jump | Stop
PulseRow = PulseSet | PulseMod | SimplePulseSet | SimplePulseMod | Jump | Stop
FilterRow = FilterSet | FilterMod | CutoffSet | Jump | Stop
SpeedRow = FixedSpeed | CalculatedSpeed


def _signed(b: int) -> int:
    return b - 0x100 if b & 0x80 else b


def _jump(right: int) -> Jump | Stop:
    return Jump(right) if right else Stop()


def _jump_bytes(row) -> tuple[int, int] | None:
    if isinstance(row, Stop):
        return C.TABLEJUMP, 0
    if isinstance(row, Jump):
        return C.TABLEJUMP, row.row
    return None


def wave_row(left: int, right: int) -> WaveRow:
    """An editor wavetable row."""
    if left == C.TABLEJUMP:
        return _jump(right)
    if left >= C.WAVECMD:
        return WaveCommand(command_of(left & 0x0F, right))
    if left == 0:
        wave = None
    elif left <= C.WAVELASTDELAY:
        wave = Delay(left)
    else:
        wave = SetWave(left & 0x0F if left >= C.WAVESILENT else left)
    if right == 0x80:
        note = None
    elif right < 0x80:
        note = WaveNote(right - 0x80 if right >= 0x60 else right, True)
    else:
        note = WaveNote(right & 0x7F, False)
    return WaveStep(wave, note)


def wave_bytes(row: WaveRow) -> tuple[int, int]:
    """The editor bytes of a wavetable row."""
    jump = _jump_bytes(row)
    if jump:
        return jump
    if isinstance(row, WaveCommand):
        num, data = command_bytes(row.command)
        return C.WAVECMD | num, data
    match row.wave:
        case None:
            left = 0
        case Delay(t):
            left = t
        case SetWave(w):
            left = w if w > C.WAVELASTDELAY else C.WAVESILENT | w
    note = row.note
    if note is None:
        return left, 0x80
    return left, (note.value & 0x7F) if note.relative else 0x80 | note.value


def pulse_row(left: int, right: int, simple: bool = False) -> PulseRow:
    """A pulsetable row; ``simple`` reads greloc's SIMPLEPULSE form."""
    if left == C.TABLEJUMP:
        return _jump(right)
    if simple:
        return SimplePulseSet(right) if left == 0x80 else SimplePulseMod(left, right)
    if left & 0x80:
        return PulseSet((left & 0x7F) << 8 | right)
    return PulseMod(left, _signed(right))


def pulse_bytes(row: PulseRow) -> tuple[int, int]:
    """The bytes of a pulsetable row."""
    match row:
        case PulseSet(w):
            return 0x80 | w >> 8, w & 0xFF
        case PulseMod(t, s) | SimplePulseMod(t, s):
            return t, s & 0xFF
        case SimplePulseSet(v):
            return 0x80, v
    return _jump_bytes(row)


def filter_row(left: int, right: int) -> FilterRow:
    """An editor filtertable row."""
    if left == C.TABLEJUMP:
        return _jump(right)
    if left & 0x80:
        return FilterSet(left & 0x7F, right >> 4, right & 0x0F)
    if left:
        return FilterMod(left, _signed(right))
    return CutoffSet(right)


def filter_bytes(row: FilterRow) -> tuple[int, int]:
    """The editor bytes of a filtertable row."""
    match row:
        case FilterSet(p, r, c):
            return 0x80 | p, r << 4 | c
        case FilterMod(t, s):
            return t, s & 0xFF
        case CutoffSet(c):
            return 0, c
    return _jump_bytes(row)


def speed_row(left: int, right: int) -> SpeedRow:
    """A speedtable row."""
    if left & 0x80:
        return CalculatedSpeed(left & 0x7F, right)
    return FixedSpeed(left << 8 | right)


def speed_bytes(row: SpeedRow) -> tuple[int, int]:
    """The bytes of a speedtable row."""
    if isinstance(row, CalculatedSpeed):
        return 0x80 | row.rate, row.shift
    return row.value >> 8, row.value & 0xFF


# ---- instruments ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateOff:
    """An instrument's first frame: clear the gate, keep the waveform."""


@dataclass(frozen=True, slots=True)
class GateOn:
    """An instrument's first frame: set the gate, keep the waveform."""


FirstWave = SetWave | GateOff | GateOn | None


@dataclass(frozen=True, slots=True)
class Instrument:
    """An instrument: envelope, table entry points, vibrato, gate timing, first frame.

    Pointers are 1-based table rows, 0 for none; ``vibrato_speed`` is a speedtable
    row. ``hard_restart`` and ``legato`` are the gate timer's $80 and $40 flags read
    as what they do: a hard restart before each note, and no gate-off at all.
    """

    attack_decay: int = 0
    sustain_release: int = 0
    wave_ptr: int = 0
    pulse_ptr: int = 0
    filter_ptr: int = 0
    vibrato_speed: int = 0
    vibrato_delay: int = 0
    gate_timer: int = 0
    hard_restart: bool = True
    legato: bool = False
    first_wave: FirstWave = None
    name: str = ""

    @classmethod
    def of(cls, raw: RawInstrument) -> "Instrument":
        gt, fw = raw.gateoff_timer, raw.first_wave
        first = {0: None, 0xFE: GateOff(), 0xFF: GateOn()}.get(fw, SetWave(fw))
        return cls(
            raw.attack_decay,
            raw.sustain_release,
            raw.wave_ptr,
            raw.pulse_ptr,
            raw.filter_ptr,
            raw.vibrato_param,
            raw.vibrato_delay,
            gt & 0x3F,
            not gt & 0x80,
            bool(gt & 0x40),
            first,
            raw.name,
        )

    def raw(self) -> RawInstrument:
        fw = {GateOff: 0xFE, GateOn: 0xFF, type(None): 0}.get(type(self.first_wave))
        return RawInstrument(
            attack_decay=self.attack_decay,
            sustain_release=self.sustain_release,
            wave_ptr=self.wave_ptr,
            pulse_ptr=self.pulse_ptr,
            filter_ptr=self.filter_ptr,
            vibrato_param=self.vibrato_speed,
            vibrato_delay=self.vibrato_delay,
            gateoff_timer=self.gate_timer
            | (0 if self.hard_restart else 0x80)
            | (0x40 if self.legato else 0),
            first_wave=self.first_wave.wave if fw is None else fw,
            name=self.name,
        )


# ---- the song ---------------------------------------------------------------


def rows_of(pattern: Pattern) -> tuple[Row, ...]:
    """An editor pattern's rows, each stating its own command."""
    return tuple(
        Row(note_of(r.note), r.instrument or None, command_of(r.command, r.data))
        for r in pattern.rows
    )


def raw_rows(rows) -> list[RawRow]:
    """Editor rows: a held command restated, a rest spread over the rows it spans."""
    out, held = [], (0, 0)
    for r in rows:
        if r.command is not None:
            held = command_bytes(r.command)
        out.append(RawRow(note_byte(r.note), r.instrument or 0, *held))
        out += [RawRow(C.REST, 0, *held) for _ in range(r.length - 1)]
    return out


@dataclass(frozen=True)
class TypedSong:
    """A song as operations: patterns of typed rows, typed tables and instruments.

    ``simplepulse``: the pulsetable is greloc's SIMPLEPULSE form. A packed image
    carries ``freq``, its tuning (``freq[n]`` for note ``firstnote + n``), and
    ``speed0``, the speedtable row the packed player reads for index 0.
    """

    name: str = ""
    author: str = ""
    copyright: str = ""
    subtunes: tuple[Subtune, ...] = ()
    patterns: tuple[tuple[Row, ...], ...] = ()
    instruments: tuple[Instrument, ...] = ()
    wavetable: tuple[WaveRow, ...] = ()
    pulsetable: tuple[PulseRow, ...] = ()
    filtertable: tuple[FilterRow, ...] = ()
    speedtable: tuple[SpeedRow, ...] = ()
    simplepulse: bool = False
    firstnote: int = 0
    freq: tuple[int, ...] = field(default=(), repr=False)
    speed0: SpeedRow | None = None

    def speed(self, ref) -> SpeedRow | None:
        """The speedtable row a command or an instrument names; row 0 is ``speed0``."""
        if not isinstance(ref, int):
            ref = ref.vibrato_speed if isinstance(ref, Instrument) else ref.speed
        row = ref
        return self.speedtable[row - 1] if row else self.speed0

    def instrument(self, num: int) -> Instrument:
        """Instrument by tracker number (1-based); the empty instrument otherwise."""
        return (
            self.instruments[num - 1]
            if 1 <= num <= len(self.instruments)
            else Instrument()
        )

    @classmethod
    def from_song(cls, song: Song, simplepulse: bool = False) -> "TypedSong":
        def tab(t, f):
            return tuple(f(left, right) for left, right in zip(t.left, t.right))

        return cls(
            song.name,
            song.author,
            song.copyright,
            tuple(song.subtunes),
            tuple(rows_of(p) for p in song.patterns),
            tuple(Instrument.of(i) for i in song.instruments),
            tab(song.wavetable, wave_row),
            tab(song.pulsetable, lambda a, b: pulse_row(a, b, simplepulse)),
            tab(song.filtertable, filter_row),
            tab(song.speedtable, speed_row),
            simplepulse,
        )

    def to_song(self) -> Song:
        def tab(rows, f):
            pairs = [f(r) for r in rows]
            return Table(left=[a for a, _ in pairs], right=[b for _, b in pairs])

        return Song(
            name=self.name,
            author=self.author,
            copyright=self.copyright,
            subtunes=[
                Subtune([Orderlist(list(o.entries), o.restart) for o in s.channels])
                for s in self.subtunes
            ],
            instruments=[i.raw() for i in self.instruments],
            patterns=[Pattern(raw_rows(p)) for p in self.patterns],
            wavetable=tab(self.wavetable, wave_bytes),
            pulsetable=tab(self.pulsetable, pulse_bytes),
            filtertable=tab(self.filtertable, filter_bytes),
            speedtable=tab(self.speedtable, speed_bytes),
        )
