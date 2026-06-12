"""Read GoatTracker 2 .SNG files into :class:`~pygoattracker.model.Song`.

The byte layout follows GoatTracker 2.76 ``readme.txt`` section 6.1 and
matches ``gsong.c``: header, orderlists per subtune/channel, instruments,
the four tables, then patterns.
"""

import io
from pathlib import Path

from pygoattracker import constants
from pygoattracker.errors import SngParseError
from pygoattracker.model import (
    Instrument,
    Orderlist,
    Pattern,
    Row,
    Song,
    Subtune,
    Table,
    entry_from_byte,
)


class _Cursor:
    """Byte cursor with offset-aware errors."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, size: int, what: str) -> bytes:
        if self.pos + size > len(self.data):
            raise SngParseError(
                f"truncated file: needed {size} bytes for {what} "
                f"at offset {self.pos}, have {len(self.data) - self.pos}"
            )
        chunk = self.data[self.pos : self.pos + size]
        self.pos += size
        return chunk

    def u8(self, what: str) -> int:
        return self.take(1, what)[0]


def _decode_str(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin-1")


def _parse_orderlist(cur: _Cursor, subtune: int, channel: int) -> Orderlist:
    what = f"orderlist (subtune {subtune}, channel {channel})"
    length = cur.u8(f"{what} length")
    if length < 1:
        raise SngParseError(f"{what}: zero length")
    data = cur.take(length + 1, what)
    if data[length - 1] != constants.LOOPSONG:
        raise SngParseError(f"{what}: missing RST endmark, got {data[length - 1]:#04x}")
    try:
        entries = [entry_from_byte(value) for value in data[: length - 1]]
    except Exception as exc:
        raise SngParseError(f"{what}: {exc}") from exc
    return Orderlist(entries=entries, restart=data[length])


def _parse_instrument(cur: _Cursor, num: int) -> Instrument:
    what = f"instrument {num}"
    params = cur.take(9, what)
    name = _decode_str(cur.take(constants.MAX_INSTRNAMELEN, f"{what} name"))
    return Instrument(
        attack_decay=params[0],
        sustain_release=params[1],
        wave_ptr=params[2],
        pulse_ptr=params[3],
        filter_ptr=params[4],
        vibrato_param=params[5],
        vibrato_delay=params[6],
        gateoff_timer=params[7],
        first_wave=params[8],
        name=name,
    )


def _parse_table(cur: _Cursor, num: int) -> Table:
    what = f"table {num}"
    length = cur.u8(f"{what} length")
    left = list(cur.take(length, f"{what} left side"))
    right = list(cur.take(length, f"{what} right side"))
    return Table(left=left, right=right)


def _parse_pattern(cur: _Cursor, num: int) -> Pattern:
    what = f"pattern {num}"
    length = cur.u8(f"{what} length")
    if not 1 <= length <= constants.MAX_PATTROWS + 1:
        raise SngParseError(f"{what}: bad length {length}")
    data = cur.take(length * 4, what)
    if data[-4] != constants.ENDPATT:
        raise SngParseError(f"{what}: missing end marker, got {data[-4]:#04x}")
    rows = [
        Row(
            note=data[i],
            instrument=data[i + 1],
            command=data[i + 2],
            data=data[i + 3],
        )
        for i in range(0, (length - 1) * 4, 4)
    ]
    return Pattern(rows=rows)


def parse_sng(data: bytes, finevibrato: bool = True) -> Song:
    """Parse .SNG bytes into a :class:`~pygoattracker.model.Song`.

    All GoatTracker song generations are accepted: GTS5/GTS4/GTS3,
    GTS2 (early 3-table GoatTracker 2) and GTS! (GoatTracker 1.x).
    Pre-GTS5 songs are converted on load exactly as GoatTracker 2.76
    imports them, so they only round-trip semantically, not
    byte-identically. ``finevibrato`` selects how old GTS2 vibrato
    parameters convert (the editor's default is on).
    """
    magic = bytes(data[:4])
    if magic not in constants.SNG_COMPATIBLE_MAGICS:
        raise SngParseError(f"not a GoatTracker song (identifier {magic!r})")
    if magic in (b"GTS!", b"GTS2"):
        # Imported here to avoid a circular import: legacy.py builds on
        # this module's section parsers.
        from pygoattracker import legacy

        if magic == b"GTS!":
            return legacy.parse_gts1(data)
        return legacy.parse_gts2(data, finevibrato=finevibrato)
    cur = _Cursor(data)
    cur.take(4, "identifier")
    name = _decode_str(cur.take(constants.MAX_STR, "song name"))
    author = _decode_str(cur.take(constants.MAX_STR, "author name"))
    copyright_ = _decode_str(cur.take(constants.MAX_STR, "copyright"))

    num_subtunes = cur.u8("subtune count")
    if not 1 <= num_subtunes <= constants.MAX_SONGS:
        raise SngParseError(f"bad subtune count {num_subtunes}")
    subtunes = [
        Subtune(
            channels=[
                _parse_orderlist(cur, subtune, channel)
                for channel in range(constants.MAX_CHN)
            ]
        )
        for subtune in range(num_subtunes)
    ]

    num_instruments = cur.u8("instrument count")
    if num_instruments >= constants.MAX_INSTR:
        raise SngParseError(f"bad instrument count {num_instruments}")
    instruments = [_parse_instrument(cur, num) for num in range(1, num_instruments + 1)]

    tables = [_parse_table(cur, num) for num in range(constants.MAX_TABLES)]

    num_patterns = cur.u8("pattern count")
    if not 1 <= num_patterns <= constants.MAX_PATT:
        raise SngParseError(f"bad pattern count {num_patterns}")
    patterns = [_parse_pattern(cur, num) for num in range(num_patterns)]

    if cur.pos != len(data):
        raise SngParseError(
            f"{len(data) - cur.pos} unexpected trailing bytes at offset {cur.pos}"
        )

    song = Song(
        name=name,
        author=author,
        copyright=copyright_,
        subtunes=subtunes,
        instruments=instruments,
        patterns=patterns,
        wavetable=tables[constants.WTBL],
        pulsetable=tables[constants.PTBL],
        filtertable=tables[constants.FTBL],
        speedtable=tables[constants.STBL],
    )
    if magic != constants.SNG_MAGIC:
        from pygoattracker import legacy

        legacy.apply_legacy_conversions(song, magic)
    return song


def read_sng(src, finevibrato: bool = True) -> Song:
    """Read a .SNG from a path, bytes, or binary file-like object."""
    if isinstance(src, bytes):
        return parse_sng(src, finevibrato=finevibrato)
    if isinstance(src, (str, Path)):
        return parse_sng(Path(src).read_bytes(), finevibrato=finevibrato)
    if isinstance(src, io.IOBase) or hasattr(src, "read"):
        return parse_sng(src.read(), finevibrato=finevibrato)
    raise TypeError(f"cannot read a song from {type(src).__name__}")
