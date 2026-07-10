"""SID register write logs.

A register log is the player's output flattened to timed chip writes:
one :class:`RegWrite` per SID register write, with an absolute clock in
C64 CPU cycles. Logs serialize to plain text, one ``clock reg val``
triple per line (decimal, space separated, ``#`` comments allowed), so
they load directly into pandas or any line-based tooling.

:class:`RegWrite`, :func:`read_reglog`, :func:`write_reglog`, and the
framing loop are the shared :mod:`pysidtracker.reglog` primitives;
:data:`REGLOG_HEADER` is this package's own header line (passed to the
shared writer's ``header=`` argument), and :func:`iter_register_writes`
is a thin wrapper feeding the player's per-frame writes to
:func:`pysidtracker.reglog.frame_writes`.
"""

from typing import Iterator

from pysidtracker.errors import SidParseError
from pysidtracker.reglog import (  # re-exported for back-compat
    DEFAULT_WRITE_SPACING,
    RegWrite,
    frame_writes,
    read_reglog,
    write_reglog,
)

from pygoattracker import constants
from pygoattracker.errors import GoatTrackerError
from pygoattracker.model import Song
from pygoattracker.player import iter_frames

REGLOG_HEADER = "# pygoattracker register log: clock reg val"

__all__ = [
    "DEFAULT_WRITE_SPACING",
    "REGLOG_HEADER",
    "RegWrite",
    "iter_register_writes",
    "read_reglog",
    "write_reglog",
]


def iter_register_writes(
    song: Song,
    subtune: int = 0,
    max_frames: int | None = None,
    until_loop: bool = False,
    cycles_per_frame: int = constants.PAL_CYCLES_PER_FRAME,
    write_spacing: int = DEFAULT_WRITE_SPACING,
    **player_options,
) -> Iterator[RegWrite]:
    """Yield :class:`RegWrite` for ``song``, frame by frame.

    Writes within a frame are spaced ``write_spacing`` cycles apart from
    the frame boundary; frames are ``cycles_per_frame`` apart. The player
    already yields ``0..24`` register offsets, so framing runs with
    ``sid_reg_base=0``.
    """
    frames = iter_frames(
        song,
        subtune=subtune,
        max_frames=max_frames,
        until_loop=until_loop,
        **player_options,
    )
    try:
        yield from frame_writes(
            frames,
            cycles_per_frame=cycles_per_frame,
            write_spacing=write_spacing,
            sid_reg_base=0,
        )
    except SidParseError as exc:
        raise GoatTrackerError(str(exc)) from exc
