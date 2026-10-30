"""GoatTracker V1.x song data streams (``gt1streams.lark``) as raw rows and entries.

A row is ``("cmd", note, command byte, data)``, ``("note", note)`` or
``("rest", rows)``; an orderlist is its entry bytes and its end. What a command or
an entry byte means is the playroutine family's (:mod:`pygoattracker.gt1typed`).
"""

from lark import Transformer, v_args
from pysidtracker import REACH, ByteGrammar, Parsed

from pygoattracker.errors import SngParseError

ORDER_REACH = REACH + 1  # 255 entries, the end mark and the restart byte


@v_args(inline=True)
class _Raw(Transformer):
    """Rows and orderlists as tuples of byte values; a pattern's size is where its
    end mark (or the cut) starts, an orderlist's where it ends."""

    # pylint: disable=missing-function-docstring

    def command_row(self, note, command, data):
        return ("cmd", ord(note), ord(command), ord(data))

    editor_row = command_row

    def note_row(self, byte):
        return ("note", ord(byte) - 0x60)

    def rest_run(self, byte):
        return ("rest", 0x100 - ord(byte))

    def first_row(self, row):
        return row

    row = first_row

    def part(self, *tokens):
        return tokens[0].start_pos

    def pattern(self, *kids):
        return Parsed(kids[:-1], kids[-1].start_pos)

    def pattern_cut(self, *kids):
        if kids and isinstance(kids[-1], int):
            return Parsed(kids[:-1], kids[-1])
        return Parsed(kids, None)

    def editor_pattern(self, *kids):
        return Parsed(kids[:-3], kids[-3].start_pos)

    def _order(self, *tokens):
        if len(tokens) > 1 and tokens[-2].type == "LOOPSONG":
            body, end = tokens[:-2], ("loop", ord(tokens[-1]))
        else:
            body, end = tokens[:-1], ("end", ord(tokens[-1]))
        return Parsed((tuple(ord(t) for t in body), end), tokens[-1].end_pos)

    std_loop = std_loop_old = std_end = _order
    m11_loop = m11_loop_old = m11_end = _order


STREAMS = ByteGrammar(
    __package__,
    "gt1streams.lark",
    (
        "pattern",
        "pattern_cut",
        "editor_pattern",
        "std_loop",
        "std_loop_old",
        "std_end",
        "m11_loop",
        "m11_loop_old",
        "m11_end",
    ),
    _Raw(),
    SngParseError,
)


def order_rule(standard: bool, editor: bool, no_restart: bool) -> str:
    """The orderlist rule of a family (``standard``) and form."""
    end = "_end" if no_restart else "_loop_old" if editor else "_loop"
    return ("std" if standard else "m11") + end


def stream_trees(song) -> list:
    """``(name, rule, bytes)`` of every orderlist and pattern of a
    :class:`~pygoattracker.gt1.Gt1Song`."""
    from pygoattracker.gt1typed import (
        _Reading,
    )  # pylint: disable=import-outside-toplevel

    read = _Reading.of(song.player, None if song.sng is None else song.sng.layout)
    order = order_rule(read.standard, read.editor, read.no_restart)
    out = [
        (f"subtune {s} orderlist {c}", order, bytes(blob))
        for s, chans in enumerate(song.orderlists)
        for c, blob in enumerate(chans)
    ]
    return out + [
        (f"pattern {k}", pattern_rule(b, read.editor), bytes(b))
        for k, b in enumerate(song.patterns)
    ]


def pattern_rule(blob: bytes, editor: bool) -> str:
    """The rule a pattern's bytes read as: a packed pattern the end of the data
    cuts short is a ``pattern_cut``."""
    if editor:
        return "editor_pattern"
    try:
        STREAMS.parse_prefix(bytes(blob), "pattern")
    except SngParseError:
        return "pattern_cut"
    return "pattern"


def pattern_rows(blob: bytes, editor: bool) -> tuple:
    """``(rows, end)``: a pattern's raw rows and the bytes from its end mark on (a
    cut pattern's partial row, or nothing)."""
    blob = bytes(blob)
    if editor:
        rows, at = STREAMS.parse_prefix(blob, "editor_pattern")
        return rows, blob[at:]
    try:
        rows, at = STREAMS.parse_prefix(blob, "pattern")
    except SngParseError:
        rows, at = STREAMS.parse_prefix(blob, "pattern_cut")
    return rows, blob[len(blob) if at is None else at :]
