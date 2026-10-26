"""Read GoatTracker .SNG files into playable songs (GT2 ``Song`` or native ``Gt1Song``)."""

from pysidtracker import ByteCursor, decode_cstr, read_bytes
from pysidtracker import SidParseError as BaseSidParseError

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


class _Cursor(ByteCursor):
    """:class:`~pysidtracker.ByteCursor` raising :class:`SngParseError`."""

    __slots__ = ()

    def take(self, n: int, what: str) -> bytes:
        try:
            return super().take(n, what)
        except BaseSidParseError as exc:
            raise SngParseError(str(exc)) from exc


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


def determine_channels(data: bytes, pos: int, num_subtunes: int) -> int:
    """3 or 6: the orderlists per subtune of the song whose orderlists start at ``pos``.

    Ports GoatTracker 2 Stereo's ``determinechannels()``: 6 if
    ``num_subtunes * 6`` orderlists each end ``$FF`` plus an in-range restart,
    else 3.
    """
    for _ in range(num_subtunes * constants.MAX_STEREO_CHN):
        if pos >= len(data):
            return constants.MAX_CHN
        length = data[pos]
        stored = data[pos + 1 : pos + length + 2]
        if (
            length < 1
            or len(stored) < length + 1
            or stored[length - 1] != constants.LOOPSONG
            or stored[length] >= length + 1
        ):
            return constants.MAX_CHN
        pos += length + 2
    return constants.MAX_STEREO_CHN


def _parse_instrument(cur: _Cursor, num: int) -> Instrument:
    what = f"instrument {num}"
    params = cur.take(9, what)
    name = decode_cstr(cur.take(constants.MAX_INSTRNAMELEN, f"{what} name"))
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


def parse_sng(
    data: bytes,
    finevibrato: bool = True,
    convert: bool = False,
    multiplier: int = 1,
):
    """Parse .SNG bytes into a playable song.

    GTS5/GTS4/GTS3 and GTS2 yield a GT2 :class:`~pygoattracker.model.Song`
    (converted on load as GoatTracker 2.76 imports them). GTS! (GoatTracker
    1.x, both editor layouts) parses natively to a :class:`Gt1Song`; pass
    ``convert=True`` to get the lossy GT2 import instead. ``finevibrato``
    selects how old GTS2 vibrato parameters convert (the editor's default);
    ``multiplier`` is the editor's speed multiplier, used by the GTS! import.
    """
    magic = bytes(data[:4])
    if magic not in constants.SNG_COMPATIBLE_MAGICS:
        raise SngParseError(f"not a GoatTracker song (identifier {magic!r})")
    if magic in (b"GTS!", b"GTS2"):
        # Imported here to avoid a circular import: legacy.py builds on
        # this module's section parsers.
        from pygoattracker import legacy

        if magic == b"GTS!":
            from pygoattracker import gt1sng

            if not convert:
                return gt1sng.parse_gts1(data)
            try:
                native = gt1sng.parse_gts1(data)
            except SngParseError:
                native = None  # the GT2 import reports what is malformed
            if native is not None and native.sng.layout == gt1sng.LAYOUT_V093:
                data = gt1sng.v093_to_v125(native)
            return legacy.parse_gts1(data, multiplier=multiplier)
        return legacy.parse_gts2(data, finevibrato=finevibrato)
    cur = _Cursor(data)
    cur.take(4, "identifier")
    name = decode_cstr(cur.take(constants.MAX_STR, "song name"))
    author = decode_cstr(cur.take(constants.MAX_STR, "author name"))
    copyright_ = decode_cstr(cur.take(constants.MAX_STR, "copyright"))

    num_subtunes = cur.u8("subtune count")
    if not 1 <= num_subtunes <= constants.MAX_SONGS:
        raise SngParseError(f"bad subtune count {num_subtunes}")
    channels = determine_channels(data, cur.pos, num_subtunes)
    subtunes = [
        Subtune(
            channels=[
                _parse_orderlist(cur, subtune, channel) for channel in range(channels)
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


def read_sng(src, finevibrato: bool = True, convert: bool = False, multiplier: int = 1):
    """Read a .SNG from a path, bytes, or binary file-like object."""
    return parse_sng(
        read_bytes(src),
        finevibrato=finevibrato,
        convert=convert,
        multiplier=multiplier,
    )
