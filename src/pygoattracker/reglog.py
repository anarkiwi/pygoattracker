"""SID register write logs.

A register log is the player's output flattened to timed chip writes:
one :class:`RegWrite` per SID register write, with an absolute clock in
C64 CPU cycles. Logs serialize to plain text, one ``clock reg val``
triple per line (decimal, space separated, ``#`` comments allowed), so
they load directly into pandas or any line-based tooling.
"""

import io
from pathlib import Path
from typing import IO, Iterable, Iterator, NamedTuple

from pygoattracker import constants
from pygoattracker.errors import GoatTrackerError
from pygoattracker.model import Song
from pygoattracker.player import iter_frames

# Cycles between consecutive writes within one frame, approximating the
# store instructions of the 6502 playroutine.
DEFAULT_WRITE_SPACING = 16

REGLOG_HEADER = "# pygoattracker register log: clock reg val"


class RegWrite(NamedTuple):
    """One SID register write at an absolute CPU clock (in cycles)."""

    clock: int
    reg: int
    val: int


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
    the frame boundary; frames are ``cycles_per_frame`` apart.
    """
    if write_spacing * constants.SID_REGISTERS >= cycles_per_frame:
        raise GoatTrackerError("write_spacing too large for one frame")
    for frame, writes in enumerate(
        iter_frames(
            song,
            subtune=subtune,
            max_frames=max_frames,
            until_loop=until_loop,
            **player_options,
        )
    ):
        clock = frame * cycles_per_frame
        for offset, (reg, val) in enumerate(writes):
            yield RegWrite(clock + offset * write_spacing, reg, val)


def write_reglog(writes: Iterable[RegWrite], dst, header: bool = True) -> None:
    """Write a register log to a path or text file-like object."""

    def _dump(out: IO[str]) -> None:
        if header:
            print(REGLOG_HEADER, file=out)
        for write in writes:
            print(f"{write.clock} {write.reg} {write.val}", file=out)

    if isinstance(dst, (str, Path)):
        with open(dst, "w", encoding="utf-8") as out:
            _dump(out)
        return
    _dump(dst)


def read_reglog(src) -> list[RegWrite]:
    """Read a register log from a path or text file-like object."""
    if isinstance(src, (str, Path)):
        text = Path(src).read_text(encoding="utf-8")
    elif isinstance(src, io.IOBase) or hasattr(src, "read"):
        text = src.read()
    else:
        raise TypeError(f"cannot read a register log from {type(src).__name__}")
    writes = []
    for num, line in enumerate(text.splitlines(), start=1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) != 3:
            raise GoatTrackerError(f"bad register log line {num}: {line!r}")
        try:
            writes.append(RegWrite(*(int(field) for field in fields)))
        except ValueError as exc:
            raise GoatTrackerError(f"bad register log line {num}: {line!r}") from exc
    return writes
