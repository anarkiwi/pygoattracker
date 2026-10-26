"""The packed player's build, read from what it does when run.

A :class:`Bench` plants chosen song data at the decoded layout, boots the image's own
6502 player on jennings and reads back its SID writes; each define of
:class:`~pygoattracker.packed.PlayerBuild` is read from how ``player.s`` behaves.
"""

from dataclasses import dataclass, field

import numpy as np
from pysidtracker import registers as reg
from pysidtracker.emu import observe_data_access, run_to_rts, run_to_rts_until
from pysidtracker.image import SidImage
from pysidtracker.oracle import boot_flat
from vagrant_moses import memmap as mm

from pygoattracker import constants as C
from pygoattracker.packed import NONE, PlayerBuild, PlayerState, columns, pattern_bytes
from pygoattracker.typed import (
    DoNothing,
    Note,
    PortamentoDown,
    PortamentoUp,
    Rest,
    Row,
    SetTempo,
    TonePortamento,
    Vibrato,
)

_SID = range(mm.SID_BASE, mm.SID_BASE + C.SID_REGISTERS)
_IO = range(mm.SID_BASE, mm.SID_BASE + 0x400)
_AD, _SR, _CTRL, _PLO, _PHI = 5, 6, 4, 2, 3
_VOICE_AD = [_AD + v * C.VOICE_REG_SIZE for v in range(C.MAX_CHN)]
_VOICE_CTRL = [_CTRL + v * C.VOICE_REG_SIZE for v in range(C.MAX_CHN)]
_CUTOFF, _MODEVOL = 0x16, 0x18
_FILTCTRL = 0xF1
_RESFILT = 0x17
# A note's envelope and first frame; the hard restart's ADSR shows against them.
_BASE = dict(ad=0x5A, sr=0xC3, wave=1, pulse=0, filt=0, vib=0, vibdelay=0)
_BASE.update(gate=1, first=0x09)
_WAVE = ((0x51, 0x80), (C.TABLEJUMP, 0))  # waveform $41 after mt_wavexec's sbc #$10
_SETPULSE = ((0x8A, 0x5C), (C.TABLEJUMP, 0))  # set $A5C, stop
_MODPULSE = ((0x7F, 0x01), (C.TABLEJUMP, 1))  # +1 a frame, looping
_TOGGLEPULSE = ((0x88, 0x11), (0x89, 0x22), (C.TABLEJUMP, 1))  # two sets, looping
_FILTER = ((0x90, _FILTCTRL), (C.TABLEJUMP, 0))  # lowpass, resonance and voice 1
_CALCSPEED = (0x81, 0x01)  # half the note's semitone step (or 1) a call
_SLOWVIB = (0x40, 0x01)  # a vibrato turning only after 32 calls
_VIBDELAYS = (0x30, 0x28)
_REST = Row(Rest(), None, None, 24)
_EFFECTS = (PortamentoUp, PortamentoDown, Vibrato)
_CAP = 256
_CALLS = (0x20, 0x4C)  # jsr, jmp abs
_ROUTE = 32
_HELD = 96
_WAVES = (0xCD, 0x02, 0x30)  # gate-on waveforms setting and clearing every bit
_LONG = 4096
_STRIDE = 16
_CYCLES = 200_000
_BOOT_CYCLES = 8_000_000


class ProbeMiss(Exception):
    """The planted data does not fit the image's layout, or does not play."""


def _call(mpu, mem, play):
    start = mpu.processorCycles
    run_to_rts(mpu, mem, play, 0, _CYCLES)
    if mpu.processorCycles - start > _CYCLES:
        raise ProbeMiss("play call did not return")


def boot(image, subtune=0, buf=None, init_run=None):
    """``(subject, mpu, mem, play)``: ``image`` after its init, booted by
    :func:`~pysidtracker.oracle.boot_flat`, and ``buf``'s ``{address: byte}`` planted
    in RAM, where an init that copies song data put it. ``init_run(subject, mpu, mem,
    address, subtune)`` runs the init in place of
    :func:`~pysidtracker.emu.run_to_rts`."""
    box = []

    def setup(_mpu, subject):
        box.append(subject)

    run = None
    if init_run is not None:

        def run(mpu, mem, address, song, _max_cycles):
            init_run(box[0], mpu, mem, address, song)

    subject, mpu, mem, play = boot_flat(
        image, subtune, _BOOT_CYCLES, setup=setup, init_run=run
    )
    for addr, value in (buf or {}).items():
        subject[addr] = value
    return subject, mpu, mem, play


@dataclass
class Bench:
    """The image's player over a :class:`~pygoattracker.packed.PackedLayout`.

    ``patt_sizes`` and ``order_sizes`` are the packed patterns' and orderlists'
    byte lengths, the room planted rows have; ``play`` overrides the header's play
    address;
    ``span`` is the ``[start, end)`` of the player's own code; ``song`` the player's
    own song, whose first channel a planted voice must replace; ``copies`` the RAM
    bytes the init copied each song data byte to, which a plant writes too.
    """

    image: SidImage
    lay: object
    patt_sizes: tuple
    order_sizes: tuple
    subtune: int = 0
    play: int | None = None
    span: tuple = (None, None)
    song: int | None = None
    copies: dict = field(default_factory=dict)
    _slots: list = field(init=False)

    def __post_init__(self):
        self._slots = sorted(
            range(len(self.patt_sizes)), key=lambda p: -self.patt_sizes[p]
        )

    def _tables(self):
        lay = self.lay
        wave = lay.tstart
        pulse = wave + 2 * lay.wlen
        filt = pulse + 2 * lay.plen
        speed = filt + 2 * lay.flen
        return {
            "wave": (wave, lay.wlen, lay.wlen),
            "pulse": (pulse, lay.plen, lay.plen),
            "filt": (filt, lay.flen, lay.flen),
            # parameter p reads mt_speedlefttbl-1+p and mt_speedrighttbl-1+p
            "speed": (speed + lay.zeros - 1, lay.slen + 1, lay.slen + lay.zeros),
        }

    def pitches(self, count):
        """``count`` notes spread over the tune's frequency table."""
        lay = self.lay
        top = min(lay.firstnote + lay.notes - 1, C.LASTNOTE - C.FIRSTNOTE)
        step = max(1, (top - lay.firstnote) // max(1, count - 1))
        return [Note(min(lay.firstnote + i * step, top)) for i in range(count)]

    def _chunks(self, rows, free):
        """``(slot, packed bytes)`` of ``rows`` laid over the next free pattern
        slots, as many rows to each as its room holds."""
        rows = list(rows)
        while rows:
            slot = next(free, None)
            if slot is None:
                raise ProbeMiss("patterns too short")
            n = len(rows)
            while n and len(pattern_bytes(rows[:n])) > self.patt_sizes[slot]:
                n -= 1
            if not n:
                raise ProbeMiss("pattern too short")
            yield slot, pattern_bytes(rows[:n])
            rows = rows[n:]

    def plant(self, voices, instruments=None, tables=None, every=False) -> dict:
        """``{address: byte}`` planting ``voices`` (row sequences), ``instruments``
        (``{number: {column: byte}}`` over :data:`_BASE`) and ``tables`` (``{name:
        ((left, right), ...)}``); each channel whose orderlist holds one plays a voice,
        which the played song's first must (``every``: only the played song's
        channels, each its own)."""
        lay, buf = self.lay, {}

        def put(addr, data):
            buf.update(zip(range(addr, addr + len(data)), data))

        free = iter(self._slots)
        orders = []
        for rows in voices:
            order = []
            for chunk in self._chunks(rows, free):
                order.append(chunk[0])
                put(lay.patt_addrs[chunk[0]], chunk[1])
            orders.append(bytes(order + [C.LOOPSONG, 0]))
        song = range(
            lay.channels * (self.song or 0),
            lay.channels * (self.song or 0) + lay.channels,
        )
        if every and len({lay.order_addrs[k] for k in song}) < lay.channels:
            raise ProbeMiss("the song's channels share an orderlist")
        for k, addr in enumerate(lay.order_addrs):
            if every and k not in song:
                continue
            order = orders[k % lay.channels % len(orders)]
            if len(order) <= self.order_sizes[k]:
                put(addr, order)
            elif k == song.start or every:
                raise ProbeMiss("orderlist too short")
        cols = columns(lay)
        for num, over in (instruments or {}).items():
            if not 1 <= num <= lay.num_instr:
                raise ProbeMiss(f"no instrument {num}")
            want = {**_BASE, **over}
            for j, name in enumerate(cols):
                addr = lay.ins_start + j * lay.num_instr + num - 1
                value = want[name]
                if name == "gate" and lay.gate_flags:
                    value |= self.image.mem[addr] & 0xC0
                put(addr, bytes((value,)))
        spans = self._tables()
        for name, rows in (tables or {}).items():
            base, room, right = spans[name]
            if len(rows) > room:
                raise ProbeMiss(f"{name} table too short")
            put(base, bytes(a for a, _ in rows))
            put(base + right, bytes(b for _, b in rows))
        for addr, value in list(buf.items()):
            buf.update(dict.fromkeys(self.copies.get(addr, ()), value))
        return buf

    def boot(self, buf=None):
        """``(subject, mpu, mem, play)``: :func:`boot` of the image and ``buf``."""
        subject, mpu, mem, play = boot(self.image, self.subtune, buf)
        return subject, mpu, mem, play if self.play is None else self.play

    def frames(self, buf=None, cap=_CAP, writes=None):
        """Each play call's registers of every SID the chip select decodes in
        ``$D400-$D7FF``: one 25-register row per ``$20`` block (pulse-width high to
        a nibble), so a player writing a mirror or a second chip is read there."""
        subject, mpu, mem, play = self.boot(buf)
        regs = np.zeros((len(_IO) // 0x20, C.SID_REGISTERS), np.int32)
        regs[:] = subject[_SID.start : _SID.stop]

        def sid(addr, value):
            if addr & 0x1F < C.SID_REGISTERS:
                regs[(addr - _IO.start) >> 5, addr & 0x1F] = value

        mem.subscribe_to_write(_IO, sid)
        if writes is not None:
            writes.mpu = mpu
            observe_data_access(mpu, writes.read, writes.write)
        hi = list(reg.PW_HI_REGS)
        for _ in range(cap):
            if writes is not None:
                writes.calls.append([])
            _call(mpu, mem, play)
            row = regs.copy()
            row[:, hi] &= 0x0F
            yield row

    def grid(self, voices, instruments=None, tables=None, frames=_CAP, until=None):
        """The register grid of up to ``frames`` play calls of the planted song, on
        the chip and voice its first planted envelope is written to, that voice's
        registers moved to voice 1's; ``until(grid)``, asked every :data:`_STRIDE`
        calls, ends it early."""
        buf = self.plant(voices, instruments, tables)
        rows, hit = [], None
        for row in self.frames(buf, frames):
            if hit is None:
                hit = _envelope(row, rows[-1] if rows else None)
            rows.append(row)
            if until and hit is not None and len(rows) % _STRIDE == 0:
                if until(_voice1(np.array(rows), hit)):
                    break
        return _voice1(np.array(rows), hit or (0, 0))


def _keyed(row):
    """Each ``(chip, voice)`` of ``row`` holding the planted envelope, gate on."""
    return (row[:, _VOICE_AD] == _BASE["ad"]) & (row[:, _VOICE_CTRL] & 1 == 1)


def _envelope(row, last):
    """The ``(chip, voice)`` ``row`` keys the planted envelope on, else None."""
    new = _keyed(row)
    if last is not None:
        new &= ~_keyed(last)
    found = np.argwhere(new)
    return tuple(int(v) for v in found[0]) if found.size else None


def _voice1(rows, hit):
    """The ``(chip, voice)`` ``hit`` of each row, its voice's registers swapped with
    voice 1's."""
    chip, voice = hit
    cols = list(range(C.SID_REGISTERS))
    lo = voice * C.VOICE_REG_SIZE
    cols[: C.VOICE_REG_SIZE], cols[lo : lo + C.VOICE_REG_SIZE] = (
        cols[lo : lo + C.VOICE_REG_SIZE],
        cols[: C.VOICE_REG_SIZE],
    )
    return rows[:, chip][:, cols]


class _Writes:
    """Each play call's SID writes, ``(register, address read last, pc, value)``."""

    def __init__(self):
        self.calls, self._last, self.mpu = [], None, None

    def read(self, addr):
        self._last = addr

    def write(self, addr, value):
        if addr in _IO and addr & 0x1F < C.SID_REGISTERS and self.calls:
            self.calls[-1].append((addr & 0x1F, self._last, self.mpu.pc, value))


def _notes(grid) -> list:
    """The play calls voice 0's notes sound from: its frequency changes after the
    first envelope load; each note's init is the call before."""
    load = np.flatnonzero(grid[:, _AD] == _BASE["ad"])
    if not load.size:
        return []
    f0 = int(load[0]) + 1
    return [f for f in range(f0, len(grid)) if (grid[f, :2] != grid[f - 1, :2]).any()]


@dataclass
class _Run:
    grid: np.ndarray
    notes: list


def _melody(
    bench, instrument=1, cap=_LONG, pulse=_SETPULSE, wave=_WAVE, filt=False, **over
):
    """Voice 0 plays ``instrument``: four single-row notes, then rest rows; ``filt``
    also plants a filter program."""
    first, *notes = bench.pitches(4)
    rows = (Row(first, instrument), *map(Row, notes), _REST)
    tables = {"wave": wave}
    if bench.lay.pulse and bench.lay.plen >= len(pulse):
        tables["pulse"] = pulse
        over.setdefault("pulse", 1)
    if filt and bench.lay.filt and bench.lay.flen >= len(_FILTER):
        tables["filt"] = _FILTER
        over.setdefault("filt", 1)
    grid = bench.grid([rows], {instrument: over}, tables, cap, _played)
    notes = _notes(grid)
    if not notes or grid[notes[0] - 1, _AD] != _BASE["ad"]:
        raise ProbeMiss("no note")
    return _Run(grid, notes)


def _played(grid) -> bool:
    """Four notes and two of the rest rows after them."""
    notes = _notes(grid)
    return len(notes) >= 4 and len(grid) - notes[3] > 2 * (notes[3] - notes[2]) + 2


def _class(run):
    """``(class, hard restart ADSR)`` before the second note: 0 hard restart,
    1 gate-off, 2 legato; None without a second note."""
    if len(run.notes) < 2:
        return None, None
    span = run.grid[run.notes[0] : run.notes[1] - 1]
    hr = [(a, s) for a, s in span[:, [_AD, _SR]] if (a, s) != (0x5A, 0xC3)]
    off = bool((span[:, _CTRL] & 1 == 0).any())
    return (0 if hr else 1 if off else 2), (hr[0] if hr else None)


def _classes(bench, first):
    """``(firstnohr, firstlegato)``: greloc numbers instruments hard restart,
    gate-off, legato, which ``mt_normalnote`` tells apart by comparing with
    FIRSTNOHRINSTR and FIRSTLEGATOINSTR, so each bound is a bisection over the
    instruments' monotone class, skipped where the last shares the first's."""
    known = {1: first}

    def rank(num):
        if num not in known:
            first, second = bench.pitches(2)
            rows = (Row(first, num), Row(second), _REST)
            grid = bench.grid([rows], {num: {}}, {"wave": _WAVE}, _LONG, _two)
            k, _ = _class(_Run(grid, _notes(grid)))
            if k is None:
                raise ProbeMiss("no second note")
            known[num] = k
        return known[num]

    def bound(lo, cls):
        hi = bench.lay.num_instr
        if rank(lo) >= cls:
            return lo
        if rank(hi) < cls:
            return NONE
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (lo, mid) if rank(mid) >= cls else (mid, hi)
        return hi

    nohr = bound(1, 1)
    return nohr, bound(min(nohr, bench.lay.num_instr), 2)


def _two(grid) -> bool:
    return len(_notes(grid)) >= 2


def _wavedelay(run, wave=_WAVE[0][0]):
    """``mt_wavexec`` subtracts $10 from waveforms only with its delay test in."""
    return {wave - 0x10: True, wave: False}.get(
        int(run.grid[run.notes[0], _CTRL]) & 0xF0 | 1
    )


def _simplepulse(run):
    """SIMPLEPULSE stores a set row's one byte to both pulse registers."""
    sets = {(0x5C, 0x0A): False, (0x5C, 0x0C): True}
    seen = [sets.get((int(lo), int(h))) for lo, h in run.grid[:, [_PLO, _PHI]]]
    return next((s for s in seen if s is not None), None)


def _pulseopt(bench):
    """The pulse optimization skips the step on each rest row's fetch call: two
    alternating set rows (every build sets) repeat there, else a modulation holds."""
    lay = bench.lay
    if not lay.pulse:
        return None
    program = _TOGGLEPULSE if lay.plen >= len(_TOGGLEPULSE) else _MODPULSE
    run = _melody(bench, pulse=program)
    tail = run.grid[run.notes[-1] :]
    steps = np.diff(tail[:, _PLO] | tail[:, _PHI] << 8)
    return bool((steps == 0).any()) if steps.any() else None


def _pulsestep(bench):
    """The pulse-low step a +1 modulation speed makes each call it runs (None: no
    one step shows)."""
    if not bench.lay.pulse:
        return None
    run = _melody(bench, pulse=_MODPULSE)
    steps = set(np.diff(run.grid[run.notes[0] :, _PLO]) & 0xFF) - {0}
    return int(steps.pop()) if len(steps) == 1 else None


def _pulsefetch(bench, row):
    """The pulse optimization also skips the step on the gate timer's fetch call:
    with the gate timer at the call after a note's init, a set row lands a call
    late. Needs the gate column, and only a set row."""
    if not bench.lay.gate or row < 2:
        return None
    run = _melody(bench, gate=row - 1)
    pw = run.grid[:, _PLO] | run.grid[:, _PHI] << 8
    n = run.notes[0]
    if pw[n] != pw[n - 1]:
        return False
    return True if pw[n + 1] != pw[n] else None


def _realtime(bench, row):
    """``REALTIMEOPTIMIZATION`` skips continuous effects on each row's tick 0: the
    first continuous effect the build keeps holds the frequency there, and only
    there. A calculated speed moves every call in every build that has one."""
    lay = bench.lay
    lo, hi = lay.firstnote, lay.firstnote + lay.notes - 1
    tables = {"wave": _WAVE, "speed": (_CALCSPEED,) * (1 + bool(lay.slen))}
    start = Row(Note(lo), 1)
    tries = [((Row(Note(lo), 1, cmd(0)), _REST), {}) for cmd in _EFFECTS]
    tries.append(
        (
            (
                start,
                Row(Note(min(hi, C.LASTNOTE - C.FIRSTNOTE)), None, TonePortamento(0)),
                _REST,
            ),
            {},
        )
    )
    if lay.vib and lay.slen:
        tries.insert(0, ((start, _REST), {"vib": 1}))
    for rows, over in tries:
        grid = bench.grid([rows], {1: over}, tables, 8 * row)
        moves = sorted(_notes(grid))
        if len(moves) > 3:
            held = {f for f in range(moves[1], moves[-1]) if f not in moves}
            ticks = set(range(moves[0] - 1 + row, moves[-1], row)) & set(
                range(moves[1], moves[-1])
            )
            shown = {frozenset(): False, frozenset(ticks): True}.get(frozenset(held))
            if shown is not None and ticks:
                return shown
    return None


def _tie(bench, run):
    """``REALTIMEOPTIMIZATION`` from a tone portamento at speed 0, a tie: it skips
    the second note's init, gate-off and hard restart, and jumps on that row's
    tick 0 (the plain note's init call) unless realtime effects wait a call."""
    k, _ = _class(run)
    if k not in (0, 1):
        return None
    first, second = bench.pitches(2)
    rows = (Row(first, 1), Row(second, None, TonePortamento(0)), _REST)
    zero = ((0, 0),) * (1 + bool(bench.lay.slen))
    tie = _Run(
        bench.grid(
            [rows], {1: {}}, {"wave": _WAVE, "speed": zero}, 4 * len(run.grid) // 3
        ),
        [],
    )
    tie.notes = _notes(tie.grid)
    if len(tie.notes) < 2 or _class(tie)[0] != 2:
        return None
    return {run.notes[1] - 1: False, run.notes[1]: True}.get(tie.notes[1])


def _funk(bench, row):
    """The funk tempo table: tempo 0 bounces rows between its two entries. A row
    keeps its voice's last command, so ``DoNothing`` ends each; a first tempo
    other than the default shows the tempo command is in the build at all."""
    tempo = row + 2
    notes = bench.pitches(7)
    rows = (
        Row(notes[0], 1, SetTempo(tempo)),
        Row(notes[1], None, DoNothing()),
        Row(notes[2], None, SetTempo(0)),
        Row(notes[3], None, DoNothing()),
        *map(Row, notes[4:]),
        _REST,
    )
    grid = bench.grid([rows], {1: {}}, {"wave": _WAVE}, 8 * (tempo + max(row, 9)))
    gaps = np.diff(_notes(grid))[:6]
    if len(gaps) < 6 or gaps[1] != tempo or len({*gaps[2::2]}) + len({*gaps[3::2]}) > 2:
        return None
    return int(gaps[2]), int(gaps[3])


def _ghost(bench):
    """``(ghost address, ascending, wrapper flush)`` of a buffering build: each play
    call writes all 25 registers in one order, each from the ghost byte read just
    before it. Flushed from outside the player, it is a play wrapper's, which on
    the song-init call (no filter code run yet) outputs the ghost's silent volume."""
    log = _Writes()
    for _ in bench.frames(cap=3, writes=log):
        pass
    call = log.calls[-1]
    regs = [w[0] for w in call]
    order = list(range(C.SID_REGISTERS))
    base = {w[1] - w[0] for w in call if w[1] is not None}
    if regs not in (order, order[::-1]) or len(base) != 1:
        return None, None, False
    start, end = bench.span
    if start is None or start <= call[0][2] < end:
        return base.pop(), regs == order, False
    first = [w[3] for w in log.calls[0] if w[0] == _MODEVOL]
    return None, None, bool(first) and not first[-1] & 0x0F


def _stores(bench, buf, match):
    """The bases (address less X) of the player's own stores over :data:`_CAP` play
    calls of ``buf`` whose written values and the byte left after them ``match``."""
    subject, mpu, mem, play = bench.boot(buf)
    start, end = bench.span
    written = {}

    def store(addr, value):
        if (start is None or start <= mpu.pc) and (end is None or mpu.pc < end):
            written.setdefault((addr, addr - mpu.x), []).append(value)

    observe_data_access(mpu, on_write=store)
    for _ in range(_CAP):
        _call(mpu, mem, play)
    return {base for (addr, base), seq in written.items() if match(seq, subject[addr])}


def _steps(start, step, count=4):
    """A match for stores writing ``count`` values from ``start`` ``step`` apart."""
    want = [(start + k * step) & 0xFF for k in range(count)]
    return lambda seq, _: any(
        seq[k : k + count] == want for k in range(len(seq) - count + 1)
    )


def _countdown(start):
    """A match for stores counting ``start`` down to zero, where they stay."""
    want = list(range(start, -1, -1))
    return lambda seq, _: any(
        seq[k : k + start + 1] == want and 0xFF not in seq[k + start + 1 :]
        for k in range(len(seq) - start)
    )


def _lastnote(bench):
    """``mt_chnlastnote``: the store a wavetable absolute note holds after the song
    plays, the same for two notes."""
    pattern, *notes = (n.pitch for n in bench.pitches(3))
    if notes[0] == notes[1] or not all(0 < n < 0x80 for n in notes):
        raise ProbeMiss("no two wavetable notes")
    rows = [(Row(Note(pattern), 1), _REST)]
    first, second = (
        _stores(
            bench,
            bench.plant(rows, {1: {}}, {"wave": wave}),
            lambda seq, held, n=n: held == n and n in seq,
        )
        for n in notes
        for wave in [((_WAVE[0][0], n), (C.TABLEJUMP, 0))]
    )
    return first & second


def _vibtime(bench):
    """``mt_chnvibtime``: the store a vibrato steps by two each call, from an
    instrument vibrato where the build has one, else the pattern command."""
    lay = bench.lay
    tables = {"wave": _WAVE, "speed": (_SLOWVIB,) * (1 + bool(lay.slen))}
    if lay.vib and lay.slen:
        voice, over = (Row(bench.pitches(1)[0], 1), _REST), {"vib": 1}
    else:
        voice, over = (Row(bench.pitches(1)[0], 1, Vibrato(1)), _REST), {}
    return _stores(bench, bench.plant([voice], {1: over}, tables), _steps(2, 2))


def _vibdelay(bench):
    """``mt_chnvibdelay``: the store two instrument vibrato delays each count down
    to zero and stop at, under a vibrato of speed two so no register slides alike."""
    lay = bench.lay
    if not (lay.vib and lay.slen):
        raise ProbeMiss("no instrument vibrato")
    tables = {"wave": _WAVE, "speed": ((_SLOWVIB[0], 2),) * 2}
    rows = [(Row(bench.pitches(1)[0], 1), _REST)]
    first, second = (
        _stores(
            bench,
            bench.plant(rows, {1: {"vib": 1, "vibdelay": delay}}, tables),
            _countdown(delay),
        )
        for delay in _VIBDELAYS
    )
    return first & second


# Channel variables init leaves as loaded: the Player field each seeds, the probe
# locating its store, and what the Player's count adds to the byte.
_CHANNEL_VARS = (
    ("lastnote", _lastnote, 0),
    ("vibtime", _vibtime, 0),
    ("vibdelay", _vibdelay, 1),
)


def _channels(bench):
    """``(field, values)`` of each channel variable the song-init call leaves as the
    image loaded it, read at each channel's offset from the one store its probe
    locates."""
    subject, mpu, mem, play = bench.boot()
    _call(mpu, mem, play)
    out = []
    for name, probe, bias in _CHANNEL_VARS:
        bases = _shown(probe, bench) or set()
        if len(bases) == 1:
            base = bases.pop()
            values = (subject[base + c * C.VOICE_REG_SIZE] for c in range(C.MAX_CHN))
            out.append((name, tuple(v + bias for v in values)))
    return tuple(out)


def _planted(bench, channel, wave=_WAVE, frames=_ROUTE):
    """The grids of every chip over ``frames`` calls of a note planted on
    ``channel`` alone (None: on none) with wavetable ``wave``."""
    note, rest = (Row(bench.pitches(1)[0], 1), _REST), (_REST,)
    voices = [note if k == channel else rest for k in range(bench.lay.channels)]
    buf = bench.plant(voices, {1: {}}, {"wave": wave}, every=True)
    return np.array(list(bench.frames(buf, frames)))


def _key(rows, quiet):
    """The first ``(frame, chip, voice)`` the planted envelope reaches, gate on or
    where the ``quiet`` grids of no note do not hold it, else None."""
    ad = _BASE["ad"]
    env = rows[..., _VOICE_AD] == ad
    calm = quiet[..., _VOICE_AD] != ad
    gate = rows[..., _VOICE_CTRL] & 1 == 1
    found = np.argwhere(
        env & (gate | calm) & ~(~calm & (quiet[..., _VOICE_CTRL] & 1 == 1))
    )
    return tuple(int(v) for v in found[0]) if found.size else None


def _voices(bench, channels):
    """Each channel's register offset from ``$D400`` (``$20`` a chip): where a note
    planted on it alone writes its envelope, None where it writes none."""
    quiet = _planted(bench, None)
    out = []
    for channel in range(channels):
        hit = _key(_planted(bench, channel), quiet)
        out.append(None if hit is None else hit[1] * 0x20 + hit[2] * C.VOICE_REG_SIZE)
    if not any(v is not None for v in out):
        raise ProbeMiss("no planted note sounds")
    return tuple(out)


def _mask(seen):
    """``(and, or)`` taking each waveform of ``seen`` to the control byte it shows."""
    keep = sets = 0
    for bit in (1 << b for b in range(8)):
        on = {out & bit for wave, out in seen.items() if wave & bit}
        off = {out & bit for wave, out in seen.items() if not wave & bit}
        if len(on) != 1 or len(off) != 1 or (on, off) == ({0}, {bit}):
            raise ProbeMiss("a control bit is not an and/or of the waveform")
        keep |= bit if on != off else 0
        sets |= bit if off == {bit} else 0
    return keep, sets


def _overlay(bench, voices):
    """Each channel's ``(and, or)`` on the control byte it writes: the first byte
    its voice shows past its note's init call for :data:`_WAVES`, each set by a
    held wavetable row, else the init call's; None where it is identity or no row
    shows."""
    quiet = _planted(bench, None, frames=_HELD)
    out = []
    for channel, voice in enumerate(voices):
        seen, moved = {}, False
        for wave in _WAVES if voice is not None else ():
            rows = _planted(
                bench, channel, ((wave + 0x10, 0x80), (C.TABLEJUMP, 0)), _HELD
            )
            hit = _key(rows, quiet)
            if hit is None:
                raise ProbeMiss("the planted note does not key")
            ctrl = rows[hit[0] :, hit[1], _VOICE_CTRL[hit[2]]]
            later = np.flatnonzero(ctrl != ctrl[0])
            moved |= bool(later.size)
            seen[wave] = int(ctrl[later[0]] if later.size else ctrl[0])
        if seen and not moved and set(seen.values()) == {_BASE["first"]}:
            seen = {}
        mask = _mask(seen) if seen else (0xFF, 0)
        out.append(None if mask == (0xFF, 0) else mask)
    return tuple(out) if any(out) else None


class _Entries:
    """The addresses a play call runs through, seen as :func:`run_to_rts_until`'s
    ``pcs``: learns the target of the first ``jsr``/``jmp`` and counts its visits."""

    def __init__(self, mem):
        self.mem = mem
        self.count = 0
        self.entry = None

    def __contains__(self, pc):
        mem = self.mem
        self.count += pc == self.entry
        if self.entry is None and mem[pc] in _CALLS:
            self.entry = mem[pc + 1] | mem[pc + 2] << 8
        return False


def calls(image, subtune, play, entries):
    """How many times one play call at ``play`` after the song-init one runs the
    target of its first ``jsr``/``jmp``, where that is one of the player's play
    ``entries`` (else 1)."""
    subject, mpu, mem = boot(image, subtune)[:3]
    _call(mpu, mem, play)
    seen = _Entries(subject)
    run_to_rts_until(mpu, mem, play, mpu.a, seen, _CYCLES)
    return max(seen.count, 1) if seen.entry in entries else 1


def _shown(probe, *args, default=None):
    """``probe(*args)``, or ``default`` where the planted song does not play."""
    try:
        return probe(*args)
    except ProbeMiss:
        return default


def _first_shown(*probes):
    """The first of ``probes`` that shows its define."""
    return next((v for v in (_shown(p) for p in probes) if v is not None), None)


def measure(bench: Bench, channels: int | None = None) -> PlayerBuild:
    """Every :class:`~pygoattracker.packed.PlayerBuild` define the player sequencing
    ``channels`` (None: the editor's) shows."""
    lay = bench.lay
    ghost, alt, wrapper = _shown(_ghost, bench, default=(None, None, False))
    voices = _shown(_voices, bench, channels or C.MAX_CHN)
    overlay = voices and _shown(_overlay, bench, voices)
    try:
        run = _melody(bench)
    except ProbeMiss:
        return PlayerBuild(
            ghost=ghost,
            alt=alt,
            wrapper=wrapper,
            channels=channels,
            voices=voices,
            overlay=overlay,
        )
    gaps = set(np.diff(run.notes[:4]))
    row = int(gaps.pop()) if len(run.notes) >= 4 and len(gaps) == 1 else None
    k, hr = _class(run)
    firstnohr = firstlegato = None
    if not lay.gate_flags and k is not None:
        firstnohr, firstlegato = _shown(_classes, bench, k, default=(None, None))
    fixed = None
    if not lay.gate and len(run.notes) > 1:
        span = run.grid[run.notes[0] : run.notes[1] - 1, _CTRL]
        off = np.flatnonzero(span & 1 == 0)
        if off.size:
            fixed = len(span) - int(off[0]), int(run.grid[run.notes[0] - 1, _CTRL])
    return PlayerBuild(
        ghost=ghost,
        alt=alt,
        adparam=None if hr is None else int(hr[0]),
        srparam=None if hr is None else int(hr[1]),
        firstnohr=firstnohr,
        firstlegato=firstlegato,
        deftempo=None if row is None else row - 1,
        fixed=fixed,
        pulseopt=_first_shown(
            lambda: _pulseopt(bench),
            lambda: None if row is None else _pulsefetch(bench, row),
        ),
        pulsestep=_shown(_pulsestep, bench),
        realtime=(
            None
            if row is None
            else _first_shown(lambda: _realtime(bench, row), lambda: _tie(bench, run))
        ),
        wavedelay=_wavedelay(run),
        simplepulse=_simplepulse(run),
        funk=None if row is None or ghost is None else _shown(_funk, bench, row),
        wrapper=wrapper,
        channels=channels,
        voices=voices,
        overlay=overlay,
    )


def state(bench: Bench, build: PlayerBuild) -> PlayerState | None:
    """What the first play call starts from. A full-ghosting build's is the ghost
    init leaves, and the cutoff, mode and volume its filter code then stores to it;
    a chip-writing build's is the SID its first tick writes from the variables init
    leaves as loaded, and the registers its song-init call writes (None where the
    planted song does not play); each with the channel variables init keeps."""
    g = build.ghost
    funk = build.funk or (8, 5)
    channels = _channels(bench)
    if g is None:
        log = _Writes()
        try:
            buf = bench.plant([(_REST,)], {1: {}}, {"wave": _WAVE})
            first, regs = (row[0] for row in bench.frames(buf, cap=2, writes=log))
        except ProbeMiss:
            return None
        init = sorted({w[0] for w in log.calls[0]})
        return PlayerState(
            registers=tuple(int(v) for v in regs),
            cutoff=int(regs[_CUTOFF]),
            filttype=int(regs[_MODEVOL]) & 0xF0,
            volume=int(regs[_MODEVOL]) & 0x0F,
            funk=funk,
            ghost=False,
            init=tuple((r, int(first[r])) for r in init),
            channels=channels,
        )
    buf = bench.plant([(Row(bench.pitches(1)[0], 1), _REST)], {1: {}}, {"wave": _WAVE})
    subject, mpu, mem, play = bench.boot(buf)
    regs = tuple(subject[g : g + C.SID_REGISTERS])
    for _ in range(2):
        _call(mpu, mem, play)
    mode = subject[g + _MODEVOL]
    return PlayerState(
        registers=regs,
        cutoff=subject[g + _CUTOFF],
        filttype=mode & 0xF0,
        volume=mode & 0x0F,
        funk=funk,
        channels=channels,
    )
