"""Read, write, play, and render GoatTracker 2 (.SNG) songs."""

from pygoattracker.audio import render_samples, render_wav, write_wav
from pygoattracker.errors import (
    GoatTrackerError,
    NinjaParseError,
    NinjaValidationError,
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
from pygoattracker.reglog import (
    RegWrite,
    iter_register_writes,
    read_reglog,
    write_reglog,
)
from pygoattracker.writer import build_sng, validate_song, write_sng

__version__ = "0.1.0"

__all__ = [
    "GoatTrackerError",
    "Instrument",
    "NinjaCommand",
    "NinjaParseError",
    "NinjaPattern",
    "NinjaRow",
    "NinjaSong",
    "NinjaValidationError",
    "Orderlist",
    "Pattern",
    "PlayPattern",
    "Player",
    "RegWrite",
    "Repeat",
    "Row",
    "SngParseError",
    "SngValidationError",
    "Song",
    "Subtune",
    "Table",
    "Transpose",
    "__version__",
    "build_nt2",
    "build_sng",
    "entry_from_byte",
    "iter_frames",
    "iter_register_writes",
    "parse_nt2",
    "parse_sng",
    "read_nt2",
    "read_reglog",
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
