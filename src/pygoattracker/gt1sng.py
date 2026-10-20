"""GoatTracker V1.x editor songs (``GTS!``): the V0.9x, V1.25, and
V1.4b-V1.53 layouts, native as :class:`Gt1Song`. ``parse_gts1``/``write_gts1``
reproduce each body byte for byte; ``relocate`` ports the F9 relocator for
the V1.25 and V1.4b-V1.53 bodies (V0.9x is native-only).
"""

from __future__ import annotations

from dataclasses import dataclass

from pysidtracker import ByteCursor
from pysidtracker import SidParseError as BaseSidParseError

from pygoattracker._gt1_refs import PLAYERS
from pygoattracker.errors import SngParseError, SngValidationError
from pygoattracker.gt1 import (
    DEFAULT_PARAMS,
    Gt1Instrument,
    Gt1Song,
    PLAYER_ORG,
    SEGMENTS_V11,
    SEGMENTS_V15,
    V15,
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
_V15_PATTREF = 208  # V1.4b-V1.53 MAX_PATT; orderlist entries above are commands
_V15_FILT = 256
_V15_FILTSTEPS = 64
_V15_WAVETBL_MAX = 512
# The three editor-song layouts, and the V0.9x body's limits (goattrk093).
LAYOUT_V093 = "0.9x"
LAYOUT_V125 = "1.25"
LAYOUT_V153 = "1.4b-V1.53"
_PLAYER_V093 = "0.93"
_V093_WAVEMAX = 128  # MAX_WAVELEN*2: 64 rows, the last a $00-waveform endmark
_V093_ENDMARKS = (_LOOPSONG, _OLD_ENDMARK)


@dataclass(frozen=True)
class Gt1Sng:
    """The raw V1.x header fields: song name, author, copyright (NUL-trimmed),
    plus the editor-song layout the body was parsed from."""

    name: bytes
    author: bytes
    copyright: bytes
    layout: str = LAYOUT_V125


class _Cursor(ByteCursor):
    """:class:`~pysidtracker.ByteCursor` raising :class:`SngParseError`."""

    __slots__ = ()

    def take(self, n: int, what: str) -> bytes:
        try:
            return super().take(n, what)
        except BaseSidParseError as exc:
            raise SngParseError(str(exc)) from exc


def _require_family(player: str, family: str, expected: str, layout: str) -> None:
    """Validate ``player`` for an editor-song layout; load its reference code."""
    try:
        actual = PLAYERS[player]["family"]
    except KeyError:
        raise SngParseError(f"unknown playroutine {player!r}") from None
    if actual != family:
        raise SngParseError(
            f"playroutine {player!r} is not {expected}; "
            f"{layout} editor songs require {expected}"
        )
    reference(player)


def _endmark(blob: bytes) -> int:
    """Index of the orderlist endmark (``$FF`` restart or old ``$FE``)."""
    if blob[-1] == _OLD_ENDMARK:
        return len(blob) - 1
    if len(blob) >= 2 and blob[-2] == _LOOPSONG:
        return len(blob) - 2
    raise SngParseError(f"orderlist missing endmark, ends {bytes(blob[-2:])!r}")


def parse_gts1(data: bytes, player: str | None = None) -> Gt1Song:
    """Parse a V1.x ``GTS!`` editor song into a native :class:`Gt1Song`.

    Three layouts are accepted (V0.9x, V1.25, V1.4b-V1.53), detected from the
    first orderlist and the tail. ``player`` pins the playroutine; V0.9x
    songs are native-only (their playroutine is unportable).
    """
    cur = _Cursor(data)
    magic = cur.take(4, "identifier")
    if magic != GTS1_MAGIC:
        raise SngParseError(f"not a GoatTracker 1.x song (identifier {magic!r})")
    name = cur.take(32, "song name").rstrip(b"\x00")
    author = cur.take(32, "author name").rstrip(b"\x00")
    copyright_ = cur.take(32, "copyright name").rstrip(b"\x00")
    num_subtunes = cur.u8("subtune count")
    if not 1 <= num_subtunes <= 32:
        raise SngParseError(f"bad subtune count {num_subtunes}")
    if _detect_layout(data, cur.pos, num_subtunes) == LAYOUT_V093:
        return _parse_v093(
            cur, num_subtunes, Gt1Sng(name, author, copyright_, LAYOUT_V093)
        )
    sng = Gt1Sng(name, author, copyright_, LAYOUT_V125)
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
    if remaining == _V15_FILT:
        filtertable = cur.take(_V15_FILT, "filtertable")
        player = player or "1.53"
        _require_family(player, V15, "a standard V1.5x routine", "V1.4b-V1.53")
        segments, params = SEGMENTS_V15, DEFAULT_PARAMS
        sng = Gt1Sng(sng.name, sng.author, sng.copyright, LAYOUT_V153)
    elif not remaining:
        player = player or "11.3"
        _require_family(player, "11", "a musicroutine 11.x routine", "V1.25")
        filtertable, segments, params = b"", SEGMENTS_V11, ()
    else:
        raise SngParseError(
            f"{remaining} unexpected trailing bytes at offset {cur.pos}"
        )
    for chans in orderlists:
        for blob in chans:
            for value in blob[: _endmark(blob)]:
                is_ref = not filtertable or value < _V15_PATTREF
                if is_ref and value >= num_patterns:
                    raise SngParseError(
                        f"orderlist references pattern {value} but only "
                        f"{num_patterns} patterns are stored"
                    )
    return Gt1Song(
        player=player,
        address=PLAYER_ORG,
        params=params,
        instruments=instruments,
        wavetable=wavetable,
        filtertable=filtertable,
        orderlists=orderlists,
        patterns=patterns,
        segments=segments,
        sng=sng,
    )


def _detect_layout(data: bytes, body: int, num_subtunes: int) -> str:
    """Which editor layout the body uses, decided by the first wavetable.

    ``$FE`` endmark orderlists are byte-identical between V0.9x and the
    V1.25/V1.4b-V1.53 bodies, so they cannot decide; the first instrument
    can -- its last wave row is ``$00`` (V0.9x) or ``$FF`` (the later bodies).
    """
    off = body
    for _ in range(num_subtunes * 3):
        if off >= len(data):
            return LAYOUT_V125  # truncated; the full parse reports it
        off += data[off] + 2  # one orderlist: length byte + length+1 stored
    if off + 8 > len(data):
        return LAYOUT_V125
    wavelen = data[off + 7]
    if wavelen & 1 or wavelen < 2 or off + 24 + wavelen - 2 >= len(data):
        return LAYOUT_V125  # V0.9x wavetables are even, with a $00 terminator
    return LAYOUT_V093 if data[off + 24 + wavelen - 2] == 0 else LAYOUT_V125


def _parse_v093(cur: _Cursor, num_subtunes: int, sng: Gt1Sng) -> Gt1Song:
    """Parse a V0.9x body: orderlists with no restart byte, and inline
    wavetables terminated by a ``$00`` waveform row (at most 64 rows)."""
    orderlists = []
    for subtune in range(num_subtunes):
        chans = []
        for channel in range(3):
            what = f"orderlist (subtune {subtune}, channel {channel})"
            length = cur.u8(f"{what} length")
            if length < 1:
                raise SngParseError(f"{what} has zero length")
            blob = cur.take(length + 1, what)
            if blob[-1] not in _V093_ENDMARKS:
                raise SngParseError(f"{what} missing endmark, got {blob[-1]:#04x}")
            chans.append(blob)
        orderlists.append(chans)
    instruments = []
    wavetable = []
    for num in range(1, _MAX_INSTR + 1):
        what = f"instrument {num}"
        params = cur.take(8, what)
        name = cur.take(16, f"{what} name")
        wavelen = params[7]
        if wavelen & 1 or not 2 <= wavelen <= _V093_WAVEMAX:
            raise SngParseError(f"{what} has bad wavetable size {wavelen}")
        wave = cur.take(wavelen, f"{what} wavetable")
        if wave[-2] != 0:
            raise SngParseError(
                f"{what} wavetable is not $00-terminated, last row {wave[-2]:#04x}"
            )
        pointer = len(wavetable) + 1
        wavetable.extend(zip(wave[0::2], wave[1::2]))
        instruments.append(Gt1Instrument(*params[:7], pointer, name=name))
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
    if cur.pos != len(cur.data):
        raise SngParseError(
            f"{len(cur.data) - cur.pos} unexpected trailing bytes at offset {cur.pos}"
        )
    for chans in orderlists:
        for blob in chans:
            for value in blob[:-1]:
                if value >= num_patterns:
                    raise SngParseError(
                        f"orderlist references pattern {value} but only "
                        f"{num_patterns} patterns are stored"
                    )
    return Gt1Song(
        player=_PLAYER_V093,
        address=PLAYER_ORG,
        params=(),
        instruments=instruments,
        wavetable=wavetable,
        filtertable=b"",
        orderlists=orderlists,
        patterns=patterns,
        segments=SEGMENTS_V11,
        sng=sng,
    )


def _instrument_rows(song: Gt1Song) -> list:
    """Per-instrument inline wavetable row counts from the flat-table pointers."""
    if len(song.instruments) != _MAX_INSTR:
        raise SngValidationError(
            f"V1.x editor songs carry {_MAX_INSTR} instruments, "
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
    """Serialize a V1.x editor song (``song.sng`` set) back to ``GTS!`` bytes.

    :raises SngValidationError: if the song is packed or malformed.
    """
    if song.sng is None:
        raise SngValidationError(
            "not a V1.x editor song: use parse_gts1 to read one first"
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
    if not song.filtertable:
        return bytes(out)
    if len(song.filtertable) != _V15_FILT:
        raise SngValidationError(
            f"V1.4b-V1.53 editor songs carry a {_V15_FILT}-byte filtertable, "
            f"got {len(song.filtertable)} bytes"
        )
    out += song.filtertable
    return bytes(out)


def _swap_nybbles(value: int) -> int:
    return ((value & 0x0F) << 4) | (value >> 4)


def _optimize(temp: list) -> bytes:
    """The shared ``optimize`` phases: deduped rows, then packed rests."""
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


def _pack_pattern(src: bytes, rows: int, strip_timing: bool) -> bytes:
    """Pack editor rows the way the V1.25 editor's ``packpattern`` does."""
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
    return _optimize(temp)


def _pack_pattern_15(src: bytes, rows: int) -> bytes:
    """Pack editor rows the way the V1.53 editor's ``packpattern`` does."""
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
        if (byte & 7) == 7 and data < 0xEF and (data & 0x7F) >= 3:
            data -= 1
        elif (byte & 7) == 4:
            data = _swap_nybbles(data)
        elif (byte & 7) == 5:
            data = (data * 4) & 0xFF
        elif (byte & 7) == 0 and data:
            data ^= 0x80
        temp.append((note, byte, data))
    return _optimize(temp)


def _wave_block(pairs: list) -> list:
    """The inline wavetable the relocator copies: through its first 0xFF row."""
    for index, (wave, _note) in enumerate(pairs):
        if wave == 0xFF:
            return pairs[: index + 1]
    return list(pairs)


def _dedup(flat: list, block: list) -> int:
    """The wavetable row (1-based) the relocator points the instrument at."""
    for start in range(len(flat) - len(block) + 1):
        if flat[start : start + len(block)] == block:
            return start + 1
    found = len(flat) + 1
    if block:
        flat.extend(block)
    return found


def relocate(song: Gt1Song) -> Gt1Song:
    """Pack a V1.x editor song the way the editor's F9 relocator packs it.

    V1.25 keeps the musicroutine row and instrument forms; V1.4b-V1.53 gets
    the standard forms, the deduped wavetable, and the filtertable. Songs
    without ``sng`` come back unchanged; V0.9x songs are native-only.
    """
    if song.sng is None:
        return song
    if song.sng.layout == LAYOUT_V093:
        raise SngValidationError(
            "GoatTracker V0.9x editor songs are native-only: the V0.93 "
            "playroutine is not transcribed, so the song cannot be packed or "
            "played (write it back with write_gts1)"
        )
    if song.filtertable:
        return _relocate_v153(song)
    return _relocate_v125(song)


def _relocate_v125(song: Gt1Song) -> Gt1Song:
    _require_family(song.player, "11", "a musicroutine 11.x routine", "V1.25")
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
        found = _dedup(flat, block)
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


def _relocate_v153(song: Gt1Song) -> Gt1Song:
    _require_family(song.player, V15, "a standard V1.5x routine", "V1.4b-V1.53")
    orderlists = song.orderlists
    if not 1 <= len(orderlists) <= 32 or any(len(chans) != 3 for chans in orderlists):
        raise SngValidationError("orderlists must carry 3 channels per subtune")
    highest = 0
    for chans in orderlists:
        for blob in chans:
            for value in blob[: _endmark(blob)]:
                if value < _V15_PATTREF:
                    if value >= len(song.patterns):
                        raise SngValidationError(
                            f"orderlists reference pattern {value} but only "
                            f"{len(song.patterns)} patterns are stored"
                        )
                    highest = max(highest, value)
    rows = _instrument_rows(song)
    patterns = []
    instrmax = 0
    for num in range(highest + 1):
        blob = song.patterns[num]
        rowcount = (len(blob) - 3) // 3
        patterns.append(_pack_pattern_15(blob[:-3], rowcount))
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
        found = _dedup(flat, block)
        offset += rows[num]
        packed_instruments.append(
            Gt1Instrument(
                ins.attack_decay,
                ins.sustain_release,
                _swap_nybbles(ins.pulse & 0xFE),
                (ins.pulse_speed & 0xFE) | (ins.pulse & 1),
                ins.pulse_low >> 4,
                ins.pulse_high >> 4,
                (ins.filter * 4) & 0xFF,
                found,
            )
        )
    if len(flat) * 2 > _V15_WAVETBL_MAX:
        raise SngValidationError(
            f"wavetable needs {len(flat) * 2} bytes, over the "
            f"{_V15_WAVETBL_MAX}-byte limit"
        )
    amount = 0
    for ins in song.instruments:
        amount = max(amount, ins.filter)
    for blob in song.patterns:
        for num in range((len(blob) - 3) // 3):
            note, byte, data = blob[3 * num], blob[3 * num + 1], blob[3 * num + 2]
            if note == _LOOPSONG:
                break
            if (byte & 7) == 5:
                amount = max(amount, data)
    for num in range(_V15_FILTSTEPS):
        amount = max(amount, song.filtertable[num * 4 + 3])
    amount += 1
    size = 4 * amount
    if size > _V15_FILT:
        raise SngValidationError(
            f"filtertable needs {size} bytes, over the {_V15_FILT}-byte limit"
        )
    filtered = bytearray(song.filtertable[:size])
    for off in range(4, size, 4):
        filtered[off + 3] = (filtered[off + 3] * 4) & 0xFF
    filtered[2] = (filtered[2] - 1) & 0xFF
    filtered[3] = (filtered[3] - 1) & 0xFF
    kept = [chans for chans in orderlists if all(_endmark(blob) for blob in chans)]
    if not kept:
        raise SngValidationError(
            "every subtune is empty; the editor discards such songs"
        )
    song_lists = []
    for chans in kept:
        packed = []
        for blob in chans:
            end = _endmark(blob)
            packed.append(
                blob[:end]
                + b"\xff"
                + (b"\x00" if end == len(blob) - 1 else blob[end + 1 : end + 2])
            )
        song_lists.append(packed)
    ref = reference(song.player)
    state = {pos: ref.code[pos] for pos in ref.free}
    author = bytes(0x20 if byte == 0 else byte for byte in song.sng.author)
    author = author.ljust(32, b" ")
    return Gt1Song(
        player=song.player,
        address=song.address,
        params=song.params,
        author=author,
        state=state,
        data=bytes(ref.code[ref.code_end :]),
        instruments=packed_instruments,
        wavetable=flat,
        filtertable=bytes(filtered),
        orderlists=song_lists,
        patterns=patterns,
        segments=SEGMENTS_V15,
    )
