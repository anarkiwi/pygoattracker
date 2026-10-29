"""A tune's own play routine around the player: the image's code runs in the 6502
emulator and each entry into the player's play routine is one
:class:`~pygoattracker.player.Player` tick, returned from as by ``rts``."""

from collections import Counter
from dataclasses import dataclass

from pysidtracker import MemPlayer
from pysidtracker.emu import observe_data_access

from pygoattracker import constants as C
from pygoattracker.probe import _CYCLES, boot

_STACK = 0x0100
_CHIP = 0x20


@dataclass(frozen=True)
class Wrap:
    """The play routine at ``play`` around the player's ``entries``, of the image
    booted for header ``song``; ``sink`` is the ghost a tick's registers go to for
    the wrapper to flush (None: the SID)."""

    play: int
    entries: frozenset
    song: int = 0
    sink: int | None = None


def _push_call(mpu, bus, pc):
    """Enter ``pc`` as a ``jsr`` from outside would: the stack depth it returns to."""
    depth = mpu.sp
    for byte in (0x00, 0x01):
        bus[_STACK + mpu.sp] = byte
        mpu.sp = (mpu.sp - 1) & 0xFF
    mpu.a, mpu.pc = 0, pc
    return depth


def _rts(mpu, bus):
    lo, hi = (bus[_STACK + ((mpu.sp + k) & 0xFF)] for k in (1, 2))
    mpu.sp = (mpu.sp + 2) & 0xFF
    mpu.pc = ((hi << 8 | lo) + 1) & 0xFFFF


def _flush_base(stores) -> int | None:
    """The block most ``(register, address read)`` stores copy, register for register."""
    bases = Counter(addr - reg for reg, addr in stores if addr is not None)
    return bases.most_common(1)[0][0] if bases else None


def wrapper(image, subtune, play, entries) -> Wrap | None:
    """The :class:`Wrap` of a header ``play`` routine that is not one of the player's
    ``entries`` (None: it is, or no entry is known). A play call whose SID writes
    all fall outside the player's own run flushes a ghost: the one most of them
    read from, register for register."""
    if play is None or not entries or play in entries:
        return None
    _, mpu, bus = boot(image, subtune)[:3]
    seen = {"player": False, "read": None}
    stores = []

    def write(addr, _value):
        stores.append((addr, seen["read"], seen["player"]))

    observe_data_access(
        mpu, on_read=lambda addr: seen.__setitem__("read", addr), on_write=write
    )
    for _ in range(2):
        stores.clear()
        depth = _push_call(mpu, bus, play)
        start, entered = mpu.processorCycles, None
        while mpu.sp < depth and mpu.processorCycles - start <= _CYCLES:
            if entered is None and mpu.pc in entries:
                entered = mpu.sp
            elif entered is not None and mpu.sp > entered:
                entered = None
            seen["player"] = entered is not None
            mpu.step()
    sid = range(C.SID_BASE, C.SID_BASE + C.SID_REGISTERS)
    outside = [(a - C.SID_BASE, r) for a, r, inner in stores if a in sid and not inner]
    if any(a in sid and inner for a, _, inner in stores):
        return Wrap(play, frozenset(entries), subtune)
    return Wrap(play, frozenset(entries), subtune, _flush_base(outside))


class Wrapped(MemPlayer):
    """``player`` inside the image's own play routine ``wrap``: each play call runs
    the routine, and at each entry into the player one ``player`` tick writes its
    registers to the SID, or its ghost (a ghost-register build's) to the one the
    routine flushes."""

    MASK_PW_HI = True

    def __init__(self, player, image, subtune: int, wrap: Wrap):
        self._player, self._image, self._wrap = player, image, wrap
        super().__init__(b"", 0, subtune)

    def _init(self, _subtune: int) -> None:
        self._mem, self._mpu, self._bus = boot(self._image, self._wrap.song)[:3]

    def _tick(self) -> None:
        # pylint: disable=protected-access
        player, sink, bus = self._player, self._wrap.sink, self._bus
        player._frame()
        if sink is not None:
            for k, value in enumerate(player.regs):
                bus[sink + k] = value & 0xFF
            return
        for chip in (0, *player._banks):
            base = C.SID_BASE + chip * _CHIP
            for k in range(C.SID_REGISTERS):
                bus[base + k] = player._mem[base + k]

    def _frame(self) -> None:
        mpu, bus, entries = self._mpu, self._bus, self._wrap.entries
        depth = _push_call(mpu, bus, self._wrap.play)
        start = mpu.processorCycles
        while mpu.sp < depth and mpu.processorCycles - start <= _CYCLES:
            if mpu.pc in entries:
                self._tick()
                _rts(mpu, bus)
                continue
            mpu.step()
