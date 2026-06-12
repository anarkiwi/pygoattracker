"""Read and write NinjaTracker 2 songs.

NinjaTracker 2 (Cadaver, 2013) is a C64-native editor; its work songs
are files starting with ``N2`` followed by an RLE-packed image of the
editor's song memory: the three two-column tables, 127 pattern slots,
16 subtune track blocks, 127 commands with names, per-track lengths,
table/command counts and the two global hardrestart parameters.

The model here mirrors what the editor shows. Patterns store typed
:class:`NinjaRow` values (note or keyon/keyoff, optional command
number with the $80 legato flag, optional duration 3-65). Tracks reuse
:class:`~pygoattracker.model.Orderlist` with
:class:`~pygoattracker.model.PlayPattern` (pattern numbers 1-127) and
:class:`~pygoattracker.model.Transpose` entries -- note NinjaTracker
encodes transpose differently from GoatTracker: byte $FF is zero, $80-
$BE are up 1-63 halftones and $BF-$FE are down 64-1 (the readme's
"$C0 = zero" description does not match the player or the example
tunes). Stale bytes the editor leaves after pattern terminators are
not preserved, so rewriting a file produces canonical (but
semantically identical) output.

There is no NinjaTracker playroutine port here; parsing and writing
only.
"""

import io
from dataclasses import dataclass, field
from pathlib import Path

from pygoattracker.errors import NinjaParseError, NinjaValidationError
from pygoattracker.model import Orderlist, PlayPattern, Subtune, Table, Transpose

NT2_MAGIC = b"N2"
NT2_ESCBYTE = 0xBF

NT2_MAX_SONGS = 16
NT2_MAX_PATT = 127
NT2_MAX_CMD = 127
NT2_MAX_CMDNAMELEN = 9
NT2_MAX_PATTLEN = 192
NT2_MAX_SONGLEN = 256
NT2_MAX_TBLLEN = 255

# Note column values (the on-disk byte halved): no note (command and/or
# duration only), keyon, keyoff, C-1..B-7.
NT2_NONE = 0x00
NT2_KEYON = 0x02
NT2_KEYOFF = 0x04
NT2_FIRSTNOTE = 0x0C
NT2_LASTNOTE = 0x5F

NT2_MIN_DURATION = 3
# The editor enters durations 3-65 (since V2.03), but the byte encoding
# reaches 66 and tunes made with older versions use it.
NT2_MAX_DURATION = 66

_TABLE_BYTES = NT2_MAX_TBLLEN + 1
_BLOCK_SIZE = (
    6 * _TABLE_BYTES
    + NT2_MAX_PATT * NT2_MAX_PATTLEN
    + NT2_MAX_SONGS * NT2_MAX_SONGLEN
    + 5 * NT2_MAX_CMD
    + NT2_MAX_CMD * (NT2_MAX_CMDNAMELEN + 1)
    + NT2_MAX_SONGS * 3
    + 3
    + 1
    + 2
)

_NOTE_NAMES = ("C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-", "A#", "B-")


def note_name(note: int) -> str:
    """Tracker-style display name for a NinjaTracker note value."""
    if note == NT2_NONE:
        return "..."
    if note == NT2_KEYOFF:
        return "---"
    if note == NT2_KEYON:
        return "+++"
    if NT2_FIRSTNOTE <= note <= NT2_LASTNOTE:
        return f"{_NOTE_NAMES[note % 12]}{note // 12}"
    raise ValueError(f"not a NinjaTracker note: {note:#04x}")


@dataclass
class NinjaRow:
    """One pattern row: note, optional command, optional duration.

    ``command`` 0 keeps the previous command; $01-$7F selects a
    command, $81-$FF the same command in legato mode. ``duration`` 0
    keeps the previous duration, otherwise 3-65 frames.
    """

    note: int = NT2_NONE
    command: int = 0
    duration: int = 0

    def __str__(self) -> str:
        command = f"{self.command:02X}" if self.command else ".."
        duration = f"{self.duration:02d}" if self.duration else ".."
        return f"{note_name(self.note)} {command} {duration}"


@dataclass
class NinjaPattern:
    """A pattern of :class:`NinjaRow` values."""

    rows: list[NinjaRow] = field(default_factory=list)


@dataclass
class NinjaCommand:
    """A NinjaTracker command: instrument ADSR plus table pointers."""

    attack_decay: int = 0
    sustain_release: int = 0
    wave_ptr: int = 0
    pulse_ptr: int = 0
    filter_ptr: int = 0
    name: str = ""


def _default_subtunes() -> list[Subtune]:
    return [Subtune(channels=[Orderlist(entries=[PlayPattern(1)]) for _ in range(3)])]


def _default_patterns() -> list[NinjaPattern]:
    return [NinjaPattern()]


@dataclass
class NinjaSong:
    """A complete NinjaTracker 2 song.

    Patterns and commands are numbered from 1 in the editor:
    ``patterns[0]`` is pattern 1 and ``commands[0]`` is command 1.
    """

    subtunes: list[Subtune] = field(default_factory=_default_subtunes)
    patterns: list[NinjaPattern] = field(default_factory=_default_patterns)
    commands: list[NinjaCommand] = field(default_factory=list)
    wavetable: Table = field(default_factory=Table)
    pulsetable: Table = field(default_factory=Table)
    filtertable: Table = field(default_factory=Table)
    hr_param: int = 0x00
    first_wave: int = 0x09

    def pattern(self, num: int) -> NinjaPattern:
        """Pattern by tracker number (1-based; empty if not defined)."""
        if 1 <= num <= len(self.patterns):
            return self.patterns[num - 1]
        return NinjaPattern()

    def command(self, num: int) -> NinjaCommand:
        """Command by tracker number (1-based; empty if not defined)."""
        num &= 0x7F
        if 1 <= num <= len(self.commands):
            return self.commands[num - 1]
        return NinjaCommand()

    def tables(self) -> tuple[Table, Table, Table]:
        """The three tables in on-disk order (wave, pulse, filter)."""
        return (self.wavetable, self.pulsetable, self.filtertable)


def rle_decode(stream: bytes) -> bytes:
    """Decode NinjaTracker's save RLE (escape byte $BF)."""
    out = bytearray()
    pos = 0
    while pos < len(stream):
        value = stream[pos]
        pos += 1
        if value == NT2_ESCBYTE:
            if pos + 2 > len(stream):
                raise NinjaParseError("truncated RLE run")
            out += bytes([stream[pos]]) * stream[pos + 1]
            pos += 2
        else:
            out.append(value)
    return bytes(out)


def rle_encode(block: bytes) -> bytes:
    """Encode a memory block exactly like the editor's song save."""
    out = bytearray()
    pos = 0
    while pos < len(block):
        value = block[pos]
        if pos + 1 < len(block) and block[pos + 1] == value:
            run = 1
            while run < 255 and pos + run < len(block) and block[pos + run] == value:
                run += 1
            out += bytes((NT2_ESCBYTE, value, run))
            pos += run
        elif value == NT2_ESCBYTE:
            out += bytes((NT2_ESCBYTE, NT2_ESCBYTE, 1))
            pos += 1
        else:
            out.append(value)
            pos += 1
    return bytes(out)


def track_entry_from_byte(value: int):
    """Decode one track byte ($00 loop excluded) into a typed entry."""
    if 0 < value < 0x80:
        return PlayPattern(value)
    if value >= 0x80:
        delta = (value + 1) & 0x7F
        return Transpose(delta - 0x80 if delta >= 0x40 else delta)
    raise NinjaParseError(f"not a track entry byte: {value:#04x}")


def track_entry_to_byte(entry) -> int:
    """Encode a typed track entry to its NinjaTracker byte."""
    if isinstance(entry, PlayPattern):
        if not 1 <= entry.num <= NT2_MAX_PATT:
            raise NinjaValidationError(f"pattern number out of range: {entry.num}")
        return entry.num
    if isinstance(entry, Transpose):
        if not -64 <= entry.semitones <= 63:
            raise NinjaValidationError(f"transpose out of range: {entry.semitones}")
        return ((entry.semitones - 1) & 0x7F) | 0x80
    raise NinjaValidationError(f"not a track entry: {entry!r}")


def _parse_track(data: bytes, base: int, what: str) -> Orderlist:
    """Decode one track; the loop position byte is an offset within the
    subtune's whole 256-byte block, stored relative to this track."""
    if len(data) < 2 or 0 in data[:-2] or data[-2] != 0:
        raise NinjaParseError(f"{what}: missing loop terminator")
    restart = data[-1] - base
    entries = [track_entry_from_byte(value) for value in data[:-2]]
    if not 0 <= restart < len(entries):
        raise NinjaParseError(f"{what}: loop position {data[-1]} out of range")
    return Orderlist(entries=entries, restart=restart)


def _parse_pattern_stream(data: bytes, what: str) -> NinjaPattern:
    rows = []
    pos = 0
    while True:
        if pos >= len(data):
            raise NinjaParseError(f"{what}: missing terminator")
        value = data[pos]
        pos += 1
        if value == 0:
            return NinjaPattern(rows=rows)
        if value >= NT2_FIRSTNOTE * 2:
            note = (value & 0xFE) >> 1
            has_command = bool(value & 0x01)
        else:
            note = (value & 0xFC) >> 1
            has_command = not value & 0x02
            if note not in (NT2_NONE, NT2_KEYON, NT2_KEYOFF):
                raise NinjaParseError(f"{what}: bad note byte {value:#04x}")
        command = 0
        if has_command:
            if pos >= len(data):
                raise NinjaParseError(f"{what}: truncated command")
            command = data[pos]
            pos += 1
        duration = 0
        if pos < len(data) and data[pos] >= 0xC0:
            duration = (data[pos] ^ 0xFF) + NT2_MIN_DURATION
            pos += 1
        rows.append(NinjaRow(note=note, command=command, duration=duration))


def _build_pattern_stream(pattern: NinjaPattern, num: int) -> bytes:
    out = bytearray()
    for row_num, row in enumerate(pattern.rows):
        what = f"pattern {num} row {row_num}"
        if NT2_FIRSTNOTE <= row.note <= NT2_LASTNOTE:
            value = row.note << 1
            if row.command:
                value |= 0x01
        elif row.note in (NT2_NONE, NT2_KEYON, NT2_KEYOFF):
            # The editor stores note-less rows with base byte $01.
            value = 0x01 if row.note == NT2_NONE else row.note << 1
            if not row.command:
                value |= 0x02
        else:
            raise NinjaValidationError(f"{what}: bad note {row.note}")
        out.append(value)
        if row.command:
            if not row.command & 0x7F:
                raise NinjaValidationError(f"{what}: bad command {row.command}")
            out.append(row.command)
        if row.duration:
            if not NT2_MIN_DURATION <= row.duration <= NT2_MAX_DURATION:
                raise NinjaValidationError(f"{what}: bad duration {row.duration}")
            out.append((row.duration - NT2_MIN_DURATION) ^ 0xFF)
    out.append(0)
    if len(out) > NT2_MAX_PATTLEN:
        raise NinjaValidationError(f"pattern {num} too long ({len(out)} bytes)")
    return bytes(out)


class _Block:
    """Field cursor over the decoded song memory block."""

    def __init__(self, block: bytes):
        self.block = block
        self.pos = 0

    def take(self, size: int) -> bytes:
        chunk = self.block[self.pos : self.pos + size]
        self.pos += size
        return chunk


def parse_nt2(data: bytes) -> NinjaSong:
    """Parse NinjaTracker 2 song bytes into a :class:`NinjaSong`."""
    if data[:2] != NT2_MAGIC:
        raise NinjaParseError(f"not a NinjaTracker 2 song (got {data[:2]!r})")
    block = rle_decode(data[2:])
    if len(block) != _BLOCK_SIZE:
        raise NinjaParseError(
            f"bad song memory size {len(block)}, expected {_BLOCK_SIZE}"
        )
    cur = _Block(block)
    table_sides = [cur.take(_TABLE_BYTES) for _ in range(6)]
    pattern_slots = [cur.take(NT2_MAX_PATTLEN) for _ in range(NT2_MAX_PATT)]
    track_blocks = [cur.take(NT2_MAX_SONGLEN) for _ in range(NT2_MAX_SONGS)]
    cmd_fields = [cur.take(NT2_MAX_CMD) for _ in range(5)]
    cmd_names = [cur.take(NT2_MAX_CMDNAMELEN + 1) for _ in range(NT2_MAX_CMD)]
    songlen = cur.take(NT2_MAX_SONGS * 3)
    tbllen = cur.take(3)
    num_commands = cur.take(1)[0]
    hr_param = cur.take(1)[0]
    first_wave = cur.take(1)[0]

    if num_commands > NT2_MAX_CMD:
        raise NinjaParseError(f"bad command count {num_commands}")
    song = NinjaSong(hr_param=hr_param, first_wave=first_wave)
    for table, num in zip(song.tables(), range(3)):
        if tbllen[num] > NT2_MAX_TBLLEN:
            raise NinjaParseError(f"bad table {num} length {tbllen[num]}")
        table.left = list(table_sides[num * 2][: tbllen[num]])
        table.right = list(table_sides[num * 2 + 1][: tbllen[num]])

    song.subtunes = []
    for num in range(NT2_MAX_SONGS):
        lengths = songlen[num * 3 : num * 3 + 3]
        if not any(lengths):
            song.subtunes.append(None)
            continue
        if sum(lengths) > NT2_MAX_SONGLEN:
            raise NinjaParseError(f"subtune {num}: tracks overflow")
        tracks = []
        offset = 0
        for channel, length in enumerate(lengths):
            tracks.append(
                _parse_track(
                    track_blocks[num][offset : offset + length],
                    offset,
                    f"subtune {num} track {channel}",
                )
            )
            offset += length
        song.subtunes.append(Subtune(channels=tracks))
    while len(song.subtunes) > 1 and song.subtunes[-1] is None:
        song.subtunes.pop()
    song.subtunes = [subtune or Subtune(channels=[]) for subtune in song.subtunes]

    song.patterns = [
        _parse_pattern_stream(slot, f"pattern {num + 1}")
        for num, slot in enumerate(pattern_slots)
    ]
    highest_ref = max(
        (
            entry.num
            for subtune in song.subtunes
            for track in subtune.channels
            for entry in track.entries
            if isinstance(entry, PlayPattern)
        ),
        default=1,
    )
    while len(song.patterns) > max(highest_ref, 1) and not song.patterns[-1].rows:
        song.patterns.pop()

    song.commands = [
        NinjaCommand(
            attack_decay=cmd_fields[0][num],
            sustain_release=cmd_fields[1][num],
            wave_ptr=cmd_fields[2][num],
            pulse_ptr=cmd_fields[3][num],
            filter_ptr=cmd_fields[4][num],
            name=cmd_names[num].split(b"\0", 1)[0].decode("latin-1").rstrip(),
        )
        for num in range(num_commands)
    ]
    return song


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise NinjaValidationError(message)


def validate_nt2(song: NinjaSong) -> None:
    """Raise :class:`NinjaValidationError` unless ``song`` fits NT2."""
    _check(
        1 <= len(song.subtunes) <= NT2_MAX_SONGS,
        f"need 1-{NT2_MAX_SONGS} subtunes, have {len(song.subtunes)}",
    )
    _check(
        1 <= len(song.patterns) <= NT2_MAX_PATT,
        f"need 1-{NT2_MAX_PATT} patterns, have {len(song.patterns)}",
    )
    _check(len(song.commands) <= NT2_MAX_CMD, "too many commands")
    for num, subtune in enumerate(song.subtunes):
        what = f"subtune {num}"
        _check(
            len(subtune.channels) in (0, 3),
            f"{what} must have 3 tracks (or none at all)",
        )
        total = 0
        for channel, track in enumerate(subtune.channels):
            track_what = f"{what} track {channel}"
            _check(track.entries != [], f"{track_what} is empty")
            _check(
                0 <= track.restart < len(track.entries),
                f"{track_what}: loop position out of range",
            )
            total += len(track.entries) + 2
            for entry in track.entries:
                track_entry_to_byte(entry)
                num_ref = getattr(entry, "num", None)
                _check(
                    num_ref is None or num_ref <= len(song.patterns),
                    f"{track_what}: pattern {num_ref} not defined",
                )
        _check(total <= NT2_MAX_SONGLEN, f"{what}: tracks longer than 256 bytes")
    for num, pattern in enumerate(song.patterns, start=1):
        for row in pattern.rows:
            _check(
                not row.command or (row.command & 0x7F) <= len(song.commands),
                f"pattern {num}: command {row.command:#04x} not defined",
            )
        _build_pattern_stream(pattern, num)
    for num, command in enumerate(song.commands, start=1):
        what = f"command {num}"
        for field_name in ("attack_decay", "sustain_release"):
            value = getattr(command, field_name)
            _check(0 <= value <= 0xFF, f"{what} {field_name} out of byte range")
        for ptr_name, table in (
            ("wave_ptr", song.wavetable),
            ("pulse_ptr", song.pulsetable),
            ("filter_ptr", song.filtertable),
        ):
            ptr = getattr(command, ptr_name)
            _check(0 <= ptr <= len(table), f"{what}: {ptr_name} beyond table end")
        try:
            raw = command.name.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise NinjaValidationError(f"{what} name not latin-1") from exc
        _check(len(raw) <= NT2_MAX_CMDNAMELEN, f"{what} name too long")
    for num, table in enumerate(song.tables()):
        _check(
            len(table.left) == len(table.right),
            f"table {num}: left/right lengths differ",
        )
        _check(len(table.left) <= NT2_MAX_TBLLEN, f"table {num} too long")
        for value in table.left + table.right:
            _check(0 <= value <= 0xFF, f"table {num} value out of byte range")
    _check(0 <= song.hr_param <= 0xFF, "hr_param out of byte range")
    _check(0 <= song.first_wave <= 0xFF, "first_wave out of byte range")


def build_nt2(song: NinjaSong) -> bytes:
    """Serialize ``song`` to NinjaTracker 2 bytes (validating first)."""
    validate_nt2(song)
    block = bytearray()
    for table in song.tables():
        for side in (table.left, table.right):
            block += bytes(side).ljust(_TABLE_BYTES, b"\0")
    for num in range(NT2_MAX_PATT):
        stream = b"\0"
        if num < len(song.patterns):
            stream = _build_pattern_stream(song.patterns[num], num + 1)
        block += stream.ljust(NT2_MAX_PATTLEN, b"\0")
    songlen = bytearray()
    for num in range(NT2_MAX_SONGS):
        track_block = bytearray()
        lengths = [0, 0, 0]
        if num < len(song.subtunes):
            for channel, track in enumerate(song.subtunes[num].channels):
                # The loop position is an offset into the whole block.
                data = bytes(
                    track_entry_to_byte(entry) for entry in track.entries
                ) + bytes((0, len(track_block) + track.restart))
                lengths[channel] = len(data)
                track_block += data
        block += bytes(track_block).ljust(NT2_MAX_SONGLEN, b"\0")
        songlen += bytes(lengths)
    for field_name in (
        "attack_decay",
        "sustain_release",
        "wave_ptr",
        "pulse_ptr",
        "filter_ptr",
    ):
        values = [getattr(command, field_name) for command in song.commands]
        block += bytes(values).ljust(NT2_MAX_CMD, b"\0")
    for num in range(NT2_MAX_CMD):
        name = song.commands[num].name if num < len(song.commands) else ""
        block += name.encode("latin-1").ljust(NT2_MAX_CMDNAMELEN, b" ") + b"\0"
    block += songlen
    block += bytes(len(table) for table in song.tables())
    block += bytes((len(song.commands), song.hr_param, song.first_wave))
    assert len(block) == _BLOCK_SIZE
    return NT2_MAGIC + rle_encode(bytes(block))


def read_nt2(src) -> NinjaSong:
    """Read an NT2 song from a path, bytes, or binary file-like object."""
    if isinstance(src, bytes):
        return parse_nt2(src)
    if isinstance(src, (str, Path)):
        return parse_nt2(Path(src).read_bytes())
    if isinstance(src, io.IOBase) or hasattr(src, "read"):
        return parse_nt2(src.read())
    raise TypeError(f"cannot read a song from {type(src).__name__}")


def write_nt2(song: NinjaSong, dst) -> None:
    """Write ``song`` to a path or binary file-like object."""
    data = build_nt2(song)
    if isinstance(dst, (str, Path)):
        Path(dst).write_bytes(data)
        return
    if isinstance(dst, io.IOBase) or hasattr(dst, "write"):
        dst.write(data)
        return
    raise TypeError(f"cannot write a song to {type(dst).__name__}")
