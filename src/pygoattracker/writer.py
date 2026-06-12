"""Write :class:`~pygoattracker.model.Song` objects as GoatTracker 2 .SNG.

The writer validates format limits up front (counts, value ranges, and
references to patterns, instruments and table rows) so that the output
always loads in GoatTracker. Reading a .SNG and writing it back produces
byte-identical output.
"""

import io
from pathlib import Path

from pygoattracker import constants
from pygoattracker.errors import SngValidationError
from pygoattracker.model import Row, Song


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise SngValidationError(message)


def _encode_str(text: str, size: int, what: str) -> bytes:
    try:
        raw = text.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise SngValidationError(f"{what} is not latin-1 encodable") from exc
    _check(len(raw) < size, f"{what} longer than {size - 1} characters")
    return raw.ljust(size, b"\0")


def _byte_range(value: int, what: str) -> int:
    _check(0 <= value <= 0xFF, f"{what} out of byte range: {value}")
    return value


def _validate_row(row: Row, song: Song, what: str) -> None:
    note_ok = constants.FIRSTNOTE <= row.note <= constants.KEYON
    _check(note_ok, f"{what}: bad note {row.note:#04x}")
    _check(
        0 <= row.instrument < constants.MAX_INSTR,
        f"{what}: bad instrument {row.instrument}",
    )
    _check(
        row.instrument <= len(song.instruments),
        f"{what}: instrument {row.instrument} not defined",
    )
    _check(
        0 <= row.command <= constants.CMD_SETTEMPO,
        f"{what}: bad command {row.command}",
    )
    _byte_range(row.data, f"{what} command data")


def validate_song(song: Song) -> None:
    """Raise :class:`SngValidationError` unless ``song`` fits the format."""
    _check(
        1 <= len(song.subtunes) <= constants.MAX_SONGS,
        f"need 1-{constants.MAX_SONGS} subtunes, have {len(song.subtunes)}",
    )
    _check(
        1 <= len(song.patterns) <= constants.MAX_PATT,
        f"need 1-{constants.MAX_PATT} patterns, have {len(song.patterns)}",
    )
    _check(
        len(song.instruments) < constants.MAX_INSTR,
        f"more than {constants.MAX_INSTR - 1} instruments",
    )

    for s_num, subtune in enumerate(song.subtunes):
        _check(
            len(subtune.channels) == constants.MAX_CHN,
            f"subtune {s_num} must have {constants.MAX_CHN} channels",
        )
        for c_num, orderlist in enumerate(subtune.channels):
            what = f"orderlist (subtune {s_num}, channel {c_num})"
            _check(orderlist.entries != [], f"{what} is empty")
            _check(
                len(orderlist.entries) <= constants.MAX_SONGLEN,
                f"{what} longer than {constants.MAX_SONGLEN} entries",
            )
            _check(
                0 <= orderlist.restart < len(orderlist.entries),
                f"{what}: restart {orderlist.restart} out of range",
            )
            for entry in orderlist.entries:
                num = getattr(entry, "num", None)
                _check(
                    num is None or num < len(song.patterns),
                    f"{what}: pattern {num} not defined",
                )

    for num, instrument in enumerate(song.instruments, start=1):
        what = f"instrument {num}"
        for field_name in (
            "attack_decay",
            "sustain_release",
            "vibrato_delay",
            "gateoff_timer",
            "first_wave",
        ):
            _byte_range(getattr(instrument, field_name), f"{what} {field_name}")
        for ptr_name, table in (
            ("wave_ptr", song.wavetable),
            ("pulse_ptr", song.pulsetable),
            ("filter_ptr", song.filtertable),
            ("vibrato_param", song.speedtable),
        ):
            ptr = getattr(instrument, ptr_name)
            _check(
                0 <= ptr <= len(table),
                f"{what}: {ptr_name} {ptr} beyond table end",
            )
        _encode_str(instrument.name, constants.MAX_INSTRNAMELEN, f"{what} name")

    for num, table in enumerate(song.tables()):
        what = f"table {num}"
        _check(
            len(table.left) == len(table.right),
            f"{what}: left/right lengths differ",
        )
        _check(
            len(table.left) <= constants.MAX_TABLELEN,
            f"{what} longer than {constants.MAX_TABLELEN} rows",
        )
        for value in table.left + table.right:
            _byte_range(value, f"{what} value")

    for p_num, pattern in enumerate(song.patterns):
        what = f"pattern {p_num}"
        _check(
            1 <= len(pattern.rows) <= constants.MAX_PATTROWS,
            f"{what}: need 1-{constants.MAX_PATTROWS} rows",
        )
        for r_num, row in enumerate(pattern.rows):
            _validate_row(row, song, f"{what} row {r_num}")


def build_sng(song: Song) -> bytes:
    """Serialize ``song`` to .SNG bytes (validating first)."""
    validate_song(song)
    out = bytearray()
    out += constants.SNG_MAGIC
    out += _encode_str(song.name, constants.MAX_STR, "song name")
    out += _encode_str(song.author, constants.MAX_STR, "author name")
    out += _encode_str(song.copyright, constants.MAX_STR, "copyright")

    out.append(len(song.subtunes))
    for subtune in song.subtunes:
        for orderlist in subtune.channels:
            out.append(len(orderlist.entries) + 1)
            out += bytes(entry.to_byte() for entry in orderlist.entries)
            out.append(constants.LOOPSONG)
            out.append(orderlist.restart)

    out.append(len(song.instruments))
    for instrument in song.instruments:
        out += bytes(
            (
                instrument.attack_decay,
                instrument.sustain_release,
                instrument.wave_ptr,
                instrument.pulse_ptr,
                instrument.filter_ptr,
                instrument.vibrato_param,
                instrument.vibrato_delay,
                instrument.gateoff_timer,
                instrument.first_wave,
            )
        )
        out += _encode_str(
            instrument.name, constants.MAX_INSTRNAMELEN, "instrument name"
        )

    for table in song.tables():
        out.append(len(table.left))
        out += bytes(table.left)
        out += bytes(table.right)

    out.append(len(song.patterns))
    for pattern in song.patterns:
        out.append(len(pattern.rows) + 1)
        for row in pattern.rows:
            out += bytes((row.note, row.instrument, row.command, row.data))
        out += bytes((constants.ENDPATT, 0, 0, 0))

    return bytes(out)


def write_sng(song: Song, dst) -> None:
    """Write ``song`` to a path or binary file-like object."""
    data = build_sng(song)
    if isinstance(dst, (str, Path)):
        Path(dst).write_bytes(data)
        return
    if isinstance(dst, io.IOBase) or hasattr(dst, "write"):
        dst.write(data)
        return
    raise TypeError(f"cannot write a song to {type(dst).__name__}")
