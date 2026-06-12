"""Read, write, play, and render GoatTracker 2 (.SNG) songs."""

from pygoattracker.audio import render_samples, render_wav, write_wav
from pygoattracker.errors import (
    GoatTrackerError,
    SngParseError,
    SngValidationError,
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
    "build_sng",
    "entry_from_byte",
    "iter_frames",
    "iter_register_writes",
    "parse_sng",
    "read_reglog",
    "read_sng",
    "render_samples",
    "render_wav",
    "validate_song",
    "write_reglog",
    "write_sng",
    "write_wav",
]
