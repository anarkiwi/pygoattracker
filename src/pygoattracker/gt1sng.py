"""GoatTracker V1.25 editor songs (``GTS!`` for musicroutines 11.1-11.5).

Parses to a native :class:`Gt1Song`, round-trips byte-faithfully through
``write_gts1``, and ``relocate`` ports the editor's F9 relocator so the
packed tune matches the editor's output byte for byte.
"""

from __future__ import annotations

from dataclasses import dataclass

from pysidtracker import ByteCursor
from pysidtracker import SidParseError as BaseSidParseError

from pygoattracker._gt1_refs import PLAYERS
from pygoattracker.errors import SngParseError, SngValidationError
from pygoattracker.gt1 import (
    Gt1Instrument,
    Gt1Song,
    PLAYER_ORG,
    SEGMENTS_V11,
    reference,
)

GTS1_MAGIC = b"GTS!"
_MAX_PATT = 255
_MAX_PATTROWS = 80
_MAX_INSTR = 31
_MAX_WAVELEN = 127
_REST_PREFIX = 0x60
_REST = 0x5F
_LOOPSONG = 0xFF
_OLD_ENDMARK = 0xFE
_STRIP_TIMING = frozenset(("11.1", "11.2"))
_AUTHOR_PLAYERS = frozenset(("11.3", "11.4", "11.5"))


@dataclass(frozen=True)
class Gt1Sng:
    """The raw V1.25 header fields: song name, author, copyright (NUL-trimmed)."""

    name: bytes
    author: bytes
    copyright: bytes


class _Cursor(ByteCursor):
    """:class:`~pysidtracker.ByteCursor` raising :class:`SngParseError`."""

    __slots__ = ()

    def take(self, n: int, what: str) -> bytes:
        try:
            return super().take(n, what)
        except BaseSidParseError as exc:
            raise SngParseError(str(exc)) from exc


def _require_musicroutine11(player: str) -> None:
    try:
        family = PLAYERS[player]["family"]
    except KeyError:
        raise SngParseError(f"unknown playroutine {player!r}") from None
    if family != "11":
        raise SngParseError(
            f"playroutine {player!r} is not a musicroutine 11.x routine; "
            "V1.25 editor songs require 11.1 to 11.5"
        )
    reference(player)


def _endmark(blob: bytes) -> int:
    """Index of the orderlist endmark (``$FF`` restart or old ``$FE``)."""
    if blob[-1] == _OLD_ENDMARK:
        return len(blob) - 1
    if len(blob) >= 2 and blob[-2] == _LOOPSONG:
        return len(blob) - 2
    raise SngParseError(f"orderlist missing endmark, ends {bytes(blob[-2:])!r}")


def parse_gts1(data: bytes, player: str = "11.3") -> Gt1Song:
    """Parse a V1.25 ``GTS!`` editor song into a native :class:`Gt1Song`.

    ``player`` (11.1-11.5, default 11.3) fixes the musicroutine for
    ``relocate``, ``Gt1Player`` and ``to_sid``.
    """
    _require_musicroutine11(player)
    cur = _Cursor(data)
    magic = cur.take(4, "identifier")
    if magic != GTS1_MAGIC:
        raise SngParseError(f"not a GoatTracker 1.x song (identifier {magic!r})")
    sng = Gt1Sng(
        cur.take(32, "song name").rstrip(b"\x00"),
        cur.take(32, "author name").rstrip(b"\x00"),
        cur.take(32, "copyright name").rstrip(b"\x00"),
    )
    num_subtunes = cur.u8("subtune count")
    if not 1 <= num_subtunes <= 32:
        raise SngParseError(f"bad subtune count {num_subtunes}")
    orderlists = []
    for subtune in range(num_subtunes):
        chans = []
        for channel in range(3):
            what = f"orderlist (subtune {subtune}, channel {channel})"
            length = cur.u8(f"{what} length")
            if length < 1:
                raise SngParseError(f"{what} has zero length")
            blob = cur.take(length + 1, what)
            _endmark(blob)
            chans.append(blob)
        orderlists.append(chans)
    instruments = []
    wavetable = []
    for num in range(1, _MAX_INSTR + 1):
        what = f"instrument {num}"
        params = cur.take(8, what)
        name = cur.take(16, f"{what} name")
        wavelen = params[7]
        if wavelen & 1 or wavelen > _MAX_WAVELEN * 2:
            raise SngParseError(f"{what} has bad wavetable size {wavelen}")
        wave = cur.take(wavelen, f"{what} wavetable")
        pointer = len(wavetable) + 1
        wavetable.extend(zip(wave[0::2], wave[1::2]))
        instruments.append(
            Gt1Instrument(
                *params[:7],
                pointer,
                name=name,
            )
        )
    num_patterns = cur.u8("pattern count")
    if not 1 <= num_patterns <= _MAX_PATT:
        raise SngParseError(f"bad pattern count {num_patterns}")
    patterns = []
    for num in range(num_patterns):
        what = f"pattern {num}"
        length = cur.u8(f"{what} length")
        if not length or length % 3 or length > _MAX_PATTROWS * 3 + 3:
            raise SngParseError(f"{what} has bad row length {length}")
        blob = cur.take(length, what)
        if blob[-3] != _LOOPSONG:
            raise SngParseError(
                f"{what} is missing the $FF end row, got {blob[-3]:#04x}"
            )
        patterns.append(blob)
    remaining = len(data) - cur.pos
    if remaining == 256:
        raise SngParseError(
            "V1.4b-V1.53 layout (trailing 256-byte filtertable): read it with "
            "the GoatTracker 2 import (parse_sng) instead"
        )
    if remaining:
        raise SngParseError(
            f"{remaining} unexpected trailing bytes at offset {cur.pos}"
        )
    for chans in orderlists:
        for blob in chans:
            for value in blob[: _endmark(blob)]:
                if value >= num_patterns:
                    raise SngParseError(
                        f"orderlist references pattern {value} but only "
                        f"{num_patterns} patterns are stored"
                    )
    return Gt1Song(
        player=player,
        address=PLAYER_ORG,
        params=(),
        instruments=instruments,
        wavetable=wavetable,
        orderlists=orderlists,
        patterns=patterns,
        segments=SEGMENTS_V11,
        sng=sng,
    )


def _instrument_rows(song: Gt1Song) -> list:
    """Per-instrument inline wavetable row counts from the flat-table pointers."""
    if len(song.instruments) != _MAX_INSTR:
        raise SngValidationError(
            f"V1.25 editor songs carry {_MAX_INSTR} instruments, "
            f"got {len(song.instruments)}"
        )
    pointers = [ins.wave_ptr for ins in song.instruments]
    rows = [pointers[i + 1] - pointers[i] for i in range(_MAX_INSTR - 1)]
    rows.append(len(song.wavetable) - (pointers[-1] - 1))
    if any(ptr < 1 for ptr in pointers) or any(row < 0 for row in rows):
        raise SngValidationError(
            "instrument wavetable pointers must tile the wavetable"
        )
    return rows


def write_gts1(song: Gt1Song) -> bytes:
    """Serialize a V1.25 editor song (``song.sng`` set) back to ``GTS!`` bytes."""
    if song.sng is None:
        raise SngValidationError(
            "not a V1.25 editor song: use parse_gts1 to read one first"
        )
    orderlists = song.orderlists
    if not 1 <= len(orderlists) <= 32 or any(len(chans) != 3 for chans in orderlists):
        raise SngValidationError("orderlists must carry 3 channels per subtune")
    rows = _instrument_rows(song)
    out = bytearray(GTS1_MAGIC)
    for text in (song.sng.name, song.sng.author, song.sng.copyright):
        if len(text) > 32:
            raise SngValidationError("header field longer than 32 bytes")
        out += bytes(text).ljust(32, b"\x00")
    out.append(len(orderlists))
    for chans in orderlists:
        for blob in chans:
            if len(blob) < 2:
                raise SngValidationError("orderlist too short for an endmark")
            out.append(len(blob) - 1)
            out += blob
    offset = 0
    for ins, count in zip(song.instruments, rows):
        out += bytes(
            (
                ins.attack_decay,
                ins.sustain_release,
                ins.pulse,
                ins.pulse_speed,
                ins.pulse_low,
                ins.pulse_high,
                ins.filter,
                count * 2,
            )
        )
        out += ins.name[:16].ljust(16, b"\x00")
        out += b"".join(bytes(pair) for pair in song.wavetable[offset : offset + count])
        offset += count
    if offset != len(song.wavetable):
        raise SngValidationError("wavetable does not tile the 31 instruments")
    if not 1 <= len(song.patterns) <= _MAX_PATT:
        raise SngValidationError("bad pattern count")
    out.append(len(song.patterns))
    for blob in song.patterns:
        if not blob or blob[-3] != _LOOPSONG:
            raise SngValidationError("patterns must end in the $FF end row")
        out.append(len(blob))
        out += blob
    return bytes(out)


def _swap_nybbles(value: int) -> int:
    return ((value & 0x0F) << 4) | (value >> 4)


def _pack_pattern(src: bytes, rows: int, strip_timing: bool) -> bytes:
    """Pack editor rows the way the editor's ``packpattern`` does."""
    temp = []
    instr = 0
    for count in range(rows):
        note = src[count * 3]
        byte = src[count * 3 + 1]
        data = src[count * 3 + 2]
        if count and not byte & 0xF8:
            byte |= instr
        else:
            instr = byte & 0xF8
        if (byte & 7) == 7 and data < 0xC0:
            data -= 1
        elif (byte & 7) == 7 and strip_timing:
            byte &= 0xF8
            data = 0
        if (byte & 7) == 4:
            data = _swap_nybbles(data)
        temp.append((note, byte, data))
    stream = []
    command, databyte = -1, -1
    for note, byte, data in temp:
        if (byte, data) != (command, databyte):
            command, databyte = byte, data
            stream.extend((note, byte, data))
        else:
            stream.append((note + _REST_PREFIX) & 0xFF)
    out = []
    pos = 0
    restbyte = _REST_PREFIX + _REST
    while pos < len(stream):
        value = stream[pos]
        if value < _REST_PREFIX:
            out.extend(stream[pos : pos + 3])
            pos += 3
        elif value != restbyte or pos >= len(stream) - 1:
            out.append(value)
            pos += 1
        else:
            end = pos
            while end < len(stream) - 1 and stream[end] == restbyte:
                end += 1
                if end - pos == 64:
                    break
            end -= pos
            if end > 1:
                out.append((0x100 - end) & 0xFF)
                pos += end
            else:
                out.append(value)
                pos += 1
    out.append(_LOOPSONG)
    return bytes(out)


def _wave_block(pairs: list) -> list:
    """The inline wavetable the relocator copies: through its first 0xFF row."""
    for index, (wave, _note) in enumerate(pairs):
        if wave == 0xFF:
            return pairs[: index + 1]
    return list(pairs)


def relocate(song: Gt1Song) -> Gt1Song:
    """Pack a V1.25 editor song the way the editor's F9 relocator packs it.

    Packed patterns use the musicroutine row form; instruments and
    wavetables are trimmed as the relocator does. Songs without ``sng``
    come back unchanged.
    """
    if song.sng is None:
        return song
    _require_musicroutine11(song.player)
    strip_timing = song.player in _STRIP_TIMING
    highest = 0
    for chans in song.orderlists:
        for blob in chans:
            for value in blob[: _endmark(blob)]:
                highest = max(highest, value)
    if highest + 1 > len(song.patterns):
        raise SngValidationError(
            f"orderlists reference pattern {highest} but only "
            f"{len(song.patterns)} patterns are stored"
        )
    rows = _instrument_rows(song)
    patterns = []
    instrmax = 0
    for num in range(highest + 1):
        blob = song.patterns[num]
        rowcount = (len(blob) - 3) // 3
        patterns.append(_pack_pattern(blob[:-3], rowcount, strip_timing))
        instrmax = max(
            instrmax, max((blob[3 * r + 1] & 0xF8 for r in range(rowcount)), default=0)
        )
    ninstr = max(instrmax // 8, 1)
    flat = []
    packed_instruments = []
    offset = 0
    for num in range(ninstr):
        ins = song.instruments[num]
        block = _wave_block(song.wavetable[offset : offset + rows[num]])
        found = -1
        if block:
            for start in range(len(flat) - len(block) + 1):
                if flat[start : start + len(block)] == block:
                    found = start + 1
                    break
        if found < 0:
            found = len(flat) + 1
            flat.extend(block)
        offset += rows[num]
        packed_instruments.append(
            Gt1Instrument(
                ins.attack_decay,
                ins.sustain_release,
                ins.pulse,
                ins.pulse_speed,
                ins.pulse_low,
                ins.pulse_high,
                ins.filter,
                found,
            )
        )
    orderlists = []
    for chans in song.orderlists:
        packed = []
        for blob in chans:
            end = _endmark(blob)
            packed.append(
                blob[:end]
                + b"\xff"
                + (b"\x00" if end == len(blob) - 1 else blob[end + 1 : end + 2])
            )
        if all(_endmark(blob) for blob in chans):
            orderlists.append(packed)
    author = b""
    if song.player in _AUTHOR_PLAYERS:
        author = bytes(0x20 if byte == 0 else byte for byte in song.sng.author)
        author = author.ljust(32, b" ")
        if song.player == "11.5":
            author += b"\x00"
    return Gt1Song(
        player=song.player,
        address=song.address,
        params=song.params,
        author=author,
        instruments=packed_instruments,
        wavetable=flat,
        orderlists=orderlists,
        patterns=patterns,
        segments=SEGMENTS_V11,
    )
