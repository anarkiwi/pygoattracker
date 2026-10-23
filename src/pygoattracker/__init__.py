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
from pygoattracker.gt1 import (
    DecompiledGt1,
    Gt1Instrument,
    Gt1SidParser,
    Gt1Song,
    decompile_gt1,
)
from pygoattracker.gt1player import Gt1Player
from pygoattracker.gt1sng import Gt1Sng, parse_gts1, relocate, write_gts1
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
    GoatTrackerSidParser,
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
from pygoattracker.packed import PackedLayout, PlayerBuild, PlayerState
from pygoattracker.typed import TypedSong

__version__ = "0.7.3"

__all__ = [
    "ConversionError",
    "DecompiledGt1",
    "DecompiledSid",
    "GoatTrackerError",
    "GoatTrackerSidParser",
    "Gt1Instrument",
    "Gt1Player",
    "Gt1SidParser",
    "Gt1Sng",
    "Gt1Song",
    "Instrument",
    "NinjaCommand",
    "NinjaParseError",
    "NinjaPattern",
    "NinjaRow",
    "NinjaSong",
    "NinjaValidationError",
    "Orderlist",
    "PackedInfo",
    "PackedLayout",
    "Pattern",
    "PlayPattern",
    "Player",
    "PlayerBuild",
    "PlayerState",
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
    "TypedSong",
    "__version__",
    "build_nt2",
    "build_sng",
    "decompile_gt1",
    "decompile_sid",
    "entry_from_byte",
    "gt_to_nt2",
    "iter_frames",
    "iter_register_writes",
    "parse_gts1",
    "parse_nt2",
    "parse_sid_header",
    "parse_sng",
    "read_nt2",
    "read_reglog",
    "read_sid",
    "read_sng",
    "relocate",
    "render_samples",
    "render_wav",
    "validate_nt2",
    "validate_song",
    "write_nt2",
    "write_reglog",
    "write_gts1",
    "write_sng",
    "write_wav",
]
