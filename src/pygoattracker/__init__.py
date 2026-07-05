"""Read, write, play, and render GoatTracker 2 (.SNG) songs."""

from pygoattracker.audio import render_samples, render_wav, write_wav
from pygoattracker.convert import gt_to_nt2
from pygoattracker.errors import (
    ConversionError,
    GoatTrackerError,
    NinjaParseError,
    NinjaValidationError,
    SidParseError,
    SngParseError,
    SngValidationError,
)
from pygoattracker.ninja import (
    NinjaCommand,
    NinjaPattern,
    NinjaRow,
    NinjaSong,
    build_nt2,
    parse_nt2,
    read_nt2,
    validate_nt2,
    write_nt2,
)
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
    entry_from_byte,
)
from pygoattracker.player import Player, iter_frames
from pygoattracker.reader import parse_sng, read_sng
from pygoattracker.sid import (
    DecompiledSid,
    PackedInfo,
    SidHeader,
    decompile_sid,
    parse_sid_header,
    read_sid,
)
from pygoattracker.reglog import (
    RegWrite,
    iter_register_writes,
    read_reglog,
    write_reglog,
)
from pygoattracker.writer import build_sng, validate_song, write_sng

__version__ = "0.1.0"

__all__ = [
    "ConversionError",
    "DecompiledSid",
    "GoatTrackerError",
    "Instrument",
    "NinjaCommand",
    "NinjaParseError",
    "NinjaPattern",
    "NinjaRow",
    "NinjaSong",
    "NinjaValidationError",
    "Orderlist",
    "PackedInfo",
    "Pattern",
    "PlayPattern",
    "Player",
    "RegWrite",
    "Repeat",
    "Row",
    "SidHeader",
    "SidParseError",
    "SngParseError",
    "SngValidationError",
    "Song",
    "Subtune",
    "Table",
    "Transpose",
    "__version__",
    "build_nt2",
    "build_sng",
    "decompile_sid",
    "entry_from_byte",
    "gt_to_nt2",
    "iter_frames",
    "iter_register_writes",
    "parse_nt2",
    "parse_sid_header",
    "parse_sng",
    "read_nt2",
    "read_reglog",
    "read_sid",
    "read_sng",
    "render_samples",
    "render_wav",
    "validate_nt2",
    "validate_song",
    "write_nt2",
    "write_reglog",
    "write_sng",
    "write_wav",
]
