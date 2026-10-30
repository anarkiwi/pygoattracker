"""Where the packed player keeps its song data, read off by running it.

:class:`Trace` steps the image's own player, following each value to the data byte
it was loaded from through registers, RAM and self-modified operands, so an
indexed read states which data its index came from.
"""

from typing import NamedTuple

from jennings.opcodes import OPCODES
from pysidtracker.emu import observe_data_access
from vagrant_moses import memmap as mm

from pygoattracker.errors import SidParseError
from pygoattracker.packed import channel_count, pattern_bytes, stride
from pygoattracker.probe import ProbeMiss, Spin
from pygoattracker.typed import PortamentoUp, Rest, Row, Vibrato

A, X, Y = 0, 1, 2
_LOAD = {"LDA": (A,), "LDX": (X,), "LDY": (Y,), "LAX": (A, X)}
_MOVE = {"TAX": (A, X), "TAY": (A, Y), "TXA": (X, A), "TYA": (Y, A)}
_STORE = {"STA": A, "STX": X, "STY": Y}
_ALU = {"ADC", "SBC", "AND", "ORA", "EOR", "ASL", "LSR", "ROL", "ROR"}
_RMW = {"INC", "DEC", "ASL", "LSR", "ROL", "ROR"}
_LOST = {"PLA": (A,), "TSX": (X,), "LXA": (A, X), "LAS": (A, X), "ANE": (A,)}
_LOST |= {"SBX": (X,), "ALR": (A,), "ANC": (A,), "ARR": (A,)}
_LOST |= {k: (A,) for k in ("DCP", "ISC", "SLO", "SRE", "RLA", "RRA", "SAX")}
_INDEX = {"absx": X, "zpx": X, "indx": X, "absy": Y, "zpy": Y, "indy": Y}
_ZP = {"zp", "zpx", "zpy", "indx", "indy", "imm", "rel", "impl", "acc"}
_IO = range(mm.SID_BASE, mm.SID_BASE + 0x400)
_UNKNOWN = (None, False, None, None)
_CYCLES = 200_000


class Read(NamedTuple):
    """An indexed read of ``addr`` through operand ``base``: the data byte its index
    was loaded from, with what value and whether arithmetic changed it since; for
    ``(zp),Y`` the ``(byte, operand)`` each half of the pointer was loaded from;
    the data byte ``addr`` holds a copy of (``addr`` itself where none)."""

    pc: int
    base: int
    addr: int
    origin: int | None
    value: int
    exact: bool
    pointer: tuple | None = None
    source: int | None = None


class Store(NamedTuple):
    """A value loaded from ``origin`` stored to ``addr``, ``indexed`` by X or Y."""

    addr: int
    origin: int
    indexed: bool


class Trace:
    """A booted player stepped with each register's and RAM byte's data origin: the
    byte it was first loaded from and that load's operand, whether arithmetic
    changed it, and the operand of the load that last put it in a register.

    ``reads`` holds every indexed read, ``stores`` every store of a traced value,
    ``sid`` every ``(address, origin, exact, operand, last operand)`` written in
    ``$D400-$D7FF``,
    ``counted``
    the origins of the bytes incremented or decremented in place, ``pcs`` every
    instruction address run, in the order first run.
    """

    def __init__(self, subject, mpu, mem):
        self.subject, self.mpu, self.mem = subject, mpu, mem
        self.regs = [_UNKNOWN] * 3
        self.value = [0, 0, 0]
        self.cells = {}
        self.reads, self.stores, self.sid = [], [], []
        self.counted = set()
        self.pcs = {}
        self._rd, self._wr = [], []
        observe_data_access(mpu, self._rd.append, lambda a, v: self._wr.append(a))

    def _cell(self, addr, base):
        return self.cells.get(addr, (addr, True, base, base))

    def _step(self):
        mpu, sub, rd, wr, regs = self.mpu, self.subject, self._rd, self._wr, self.regs
        pc = mpu.pc
        self.pcs.setdefault(pc)
        name, mode = OPCODES[sub[pc]]
        rd.clear()
        wr.clear()
        mpu.step()
        index = _INDEX.get(mode)
        base = sub[pc + 1] | (0 if mode in _ZP else sub[pc + 2] << 8)
        if index is not None and rd:
            pointer = None
            if mode == "indy":
                lo, hi = self._cell(base, base), self._cell((base + 1) & 0xFF, base)
                pointer = ((lo[0], lo[3]), (hi[0], hi[3]))
            org, exact, _, _ = regs[index]
            val = self.value[index]
            source = self._cell(rd[-1], base)[0]
            self.reads.append(Read(pc, base, rd[-1], org, val, exact, pointer, source))
        if name in _LOAD:
            src = self.cells.get(pc + 1, _UNKNOWN) if mode == "imm" else None
            src = src or self._cell(rd[-1], base)[:3] + (base,)
            out = (mpu.a, mpu.x, mpu.y)
            for r in _LOAD[name]:
                regs[r], self.value[r] = src, out[r]
        elif name in _MOVE:
            src, dst = _MOVE[name]
            regs[dst], self.value[dst] = regs[src], self.value[src]
        elif name in _STORE:
            src = regs[_STORE[name]]
            for addr in wr:
                self.cells[addr] = src
                if src[0] is not None:
                    self.stores.append(Store(addr, src[0], index is not None))
                if addr in _IO:
                    self.sid.append((addr, *src))
        elif name in _RMW and mode != "acc":
            for addr in wr:
                org, _, at, via = self._cell(addr, base)
                self.cells[addr] = (org, False, at, via)
                self.counted.add(org)
        elif name in _ALU:
            regs[A] = regs[A][:1] + (False,) + regs[A][2:]
        elif name in _LOST:
            for r in _LOST[name]:
                regs[r] = _UNKNOWN
            for addr in wr:
                self.cells[addr] = _UNKNOWN

    def call(self, pc, a=0, cycles=_CYCLES, what="play", idle=False):
        """Run the subroutine at ``pc`` with ``A = a`` to its final RTS, or, with
        ``idle``, to the idle loop (:class:`~pygoattracker.probe.Spin`) or ``cycles`` budget an IRQ-driven init ends
        in instead; ``what`` names the call in the error when it does not return."""
        mpu, mem = self.mpu, self.mem
        spun = Spin()
        start_sp = mpu.sp
        for byte in (0x00, 0x01):
            mem[0x0100 + mpu.sp] = byte
            mpu.sp = (mpu.sp - 1) & 0xFF
        mpu.a, mpu.pc = a & 0xFF, pc
        self.regs[A] = _UNKNOWN
        start = mpu.processorCycles
        while mpu.sp < start_sp:
            if mpu.processorCycles - start > cycles:
                if idle:
                    return
                raise SidParseError(f"{what} call did not return")
            self._step()
            if idle and spun(mpu.pc):
                return


_HORIZON = 512
_SETTLE = 2
_END = Row(Rest(), None, None, 32)
_SPEEDS = (PortamentoUp, Vibrato)
_LEFT = {"wave": 0x41, "pulse": 0x88, "filt": 0x90}
_CTRL = (4, 11, 18)
_FREQ = (0, 1, 7, 8, 14, 15)


class SongTables(NamedTuple):
    """The song(order)-table and pattern-table column bases the sequencer indexes,
    their lengths, and the player's own song the tune selects."""

    song_lo: int
    song_hi: int
    patt_lo: int
    patt_hi: int
    songs: int
    patterns: int
    song: int
    channels: int = 3
    stride: int = 3
    idle: tuple = ()


def _run(boot, until, settle=_SETTLE, trace=None):
    """A :class:`Trace` (``trace``, else a new one) of ``boot``'s player, called
    until ``until(trace)`` and then ``settle`` times more; None if ``until`` stays
    false."""
    subject, mpu, mem, play = boot
    trace = trace or Trace(subject, mpu, mem)
    for _ in range(_HORIZON):
        trace.call(play)
        if until(trace):
            for _ in range(settle):
                trace.call(play)
            return trace
    return None


def _pointer_tables(reads):
    """Each ``(song pointer, pattern pointer)``, each the ``(byte, operand)`` of its
    two halves: of the pointers ``(zp),Y`` reads go through whose halves are table
    entries, the pattern one is indexed by a byte read through the other."""
    through = {}
    for r in reads:
        if r.pointer and None not in (r.pointer[0][0], r.pointer[1][0]):
            through.setdefault(r.pointer, set()).add(r.source)
    index = {(r.source, r.base): r.origin for r in reads if r.pointer is None}
    for patt in through:
        org = index.get(patt[0])
        for song, seen in through.items():
            if song != patt and org in seen and song[0] in index and song[1] in index:
                yield song, patt


def _lengths(columns):
    """Each named column's length, the four tiling one block in base order, else
    None."""
    order = sorted(columns.items(), key=lambda kv: kv[1])
    gaps = {}
    for (name, base), (_, nxt) in zip(order, order[1:]):
        gaps.setdefault(name[0], set()).add(nxt - base)
    if any(len(g) != 1 for g in gaps.values()) or len(gaps) != 2:
        return None
    return gaps["s"].pop(), gaps["p"].pop()


def _tables(song, patt, stride):
    """The column bases of a song and pattern pointer and the song and pattern
    counts they tile to, else None: the song table holds ``stride`` orderlist
    pointers a song."""
    bases = {"slo": song[0][1], "shi": song[1][1], "plo": patt[0][1], "phi": patt[1][1]}
    lengths = _lengths(bases)
    if lengths is None or lengths[0] % stride or not lengths[1]:
        return None
    return bases, lengths


def song_tables(boot, trace=None) -> SongTables:
    """The :class:`SongTables` the booted player's sequencer reads, continuing
    ``trace`` (of its init), the first pointers it reads that tile as GoatTracker
    2's tables (another player in the image may run alongside), ``channels`` those
    its init clears and ``idle`` those whose orderlist it never reads; its song is
    the one whose first orderlist the lowest song-table index read is."""
    found, seen = [], []

    def fetched(trace):
        channels = channel_count(trace.subject, trace.pcs)
        for pair in _pointer_tables(trace.reads):
            seen.append(pair)
            tables = _tables(*pair, stride(channels))
            if tables is not None:
                found.append((*tables, channels))
                return True
        return False

    trace = _run(boot, fetched, 0, trace)
    if trace is None:
        if seen:
            raise SidParseError("song and pattern tables do not tile one block")
        raise SidParseError(
            "no GoatTracker song table read: the player reads no song and "
            "pattern tables"
        )
    bases, (songs, patterns), channels = found[0]
    n = stride(channels)
    read = {r.addr - r.base for r in trace.reads if r.base == bases["slo"]}
    index = min(read)
    first = index - index % n
    return SongTables(
        bases["slo"],
        bases["shi"],
        bases["plo"],
        bases["phi"],
        songs // n,
        patterns,
        index // n,
        channels,
        n,
        tuple(c for c in range(channels) if first + c not in read),
    )


def _voice(bench):
    """Instrument 1's note, held three rows to a rest byte nothing else planted holds."""
    return (Row(bench.pitches(1)[0], 1), Row(Rest(), None, None, 2), _END)


def _effects(bench):
    """Voice 0's note, and on the others rests with speed commands of speed row 1."""
    rest = Row(Rest(), None, None, _END.length - 1)
    return [_voice(bench)] + [(Row(Rest(), None, c(1)), rest) for c in _SPEEDS]


def _row1(reads, origin, tables=0):
    """The row-1 address of the table, past the instrument columns at ``tables``,
    the reads indexed by ``origin`` are in: each reads the indexing row or the one
    after it."""
    hits = [r.addr - r.value + 1 for r in reads if r.origin == origin and r.exact]
    return min((h for h in hits if h >= tables), default=None)


def _right(reads, origin, left):
    """The right column of the table whose left column starts at ``left``, from the
    reads of its row 1 through ``origin``: that row's left and right bytes and the
    next row's left byte, which is the right column's first where the table has one
    row."""
    rows = sorted(
        {r.addr for r in reads if r.origin == origin and r.exact and r.value == 1}
    )
    if rows[:2] != [left, left + 1] or len(rows) > 3:
        raise SidParseError("a table's row 1 does not read as two columns")
    return rows[-1] if len(rows) == 3 else left + 1


def _speed(reads, origins, end):
    """Speed row 1's left column, from a row read through ``origins`` with the same
    row of its right column where a speed table led by greloc's zero rows ends at
    ``end``, else None."""
    rows = {
        (r.addr - r.value + 1, r.value)
        for r in reads
        if r.origin in origins and r.exact
    }
    lefts = (a for a, v in rows if ((end + a + 1) / 2, v) in rows)
    return min(lefts, default=None)


def _tiling(lefts, last, speed, end):
    """``(lengths, speed rows, zero rows)`` of the abutting two-column tables whose
    left columns start at ``lefts``, the last ending where its row 1 ``speed`` is
    seen, else at ``last``, else (a table no row of which is read) at ``end``.
    greloc leads each speed column with a zero row when the build has a speed
    effect, which instrument vibrato and every packed speed row are for."""
    lens = [(b - a) / 2 for a, b in zip(lefts, lefts[1:])]
    if speed is not None:
        lens.append((speed - 1 - lefts[-1]) / 2)
    else:
        lens.append((end - lefts[-1]) / 2 if last is None else last - lefts[-1])
    rest = end - (lefts[-1] + 2 * lens[-1])
    if any(n < 0 or n % 1 for n in lens) or rest < 0 or rest % 2:
        raise SidParseError("tables do not tile the instrument/table region")
    if speed is not None and rest < 2:
        raise SidParseError("the speed table does not fill the region")
    return [int(n) for n in lens], max(0, int(rest) // 2 - 1), int(rest > 0)


def _roles(trace, cols, wave, tables, binary=False):
    """``{column: address}`` of the instrument columns besides the ``wave`` pointer,
    the tables starting at ``tables``, each told by what the player does with instrument 1's byte: the SID register it
    reaches or the table it indexes, the speed table last and the filter's pointer
    kept globally (read or not: a player can store it where nothing reads it), the
    pulse's per voice. Of the counters, the gate timer pairs
    with the first wave and the vibrato delay is the one whose copy counts down.
    A ``binary`` relocator player without a speed table keeps the vibrato parameter
    in its column, after the vibrato delay's.
    """
    sid = {}
    for addr, org, *_ in trace.sid:
        sid.setdefault(org, set()).add(addr & 0x1F)
    roles = {"wave": wave}
    for name, regs in (("ad", {5, 12, 19}), ("sr", {6, 13, 20}), ("first", _CTRL)):
        hit = [c for c in cols if sid.get(c, set()) & set(regs)]
        if len(hit) > 1:
            raise SidParseError(f"two instrument columns reach the {name} register")
        roles.update({name: hit[0]} if hit else {})
    starts = {c: _row1(trace.reads, c, tables) for c in cols if c not in roles.values()}
    pointers = [c for _, c in sorted((s, c) for c, s in starts.items() if s)]
    inert = [c for c in cols if c in starts and not starts[c]]
    for c in inert[:]:
        if {s.indexed for s in trace.stores if s.origin == c} == {False}:
            roles["filt"] = c
            inert.remove(c)
    gate = "first" in roles
    if binary and len(inert) - gate == 2:
        timer = roles["first"] - (cols[1] - cols[0]) if gate else None
        roles["vibdelay"], roles["vib"] = (c for c in inert if c != timer)
        roles.update({"gate": timer} if gate else {})
        inert, gate = [], 0
    if len(inert) - gate not in (0, 1) or len(inert) - gate > len(pointers):
        raise SidParseError("instrument columns are not greloc's")
    if len(inert) > gate:
        roles["vib"] = pointers.pop()
    if len(inert) == 2:
        paired = roles["first"] - (cols[1] - cols[0])
        timer = [c for c in inert if c == paired] or [
            c for c in inert if c not in trace.counted
        ]
        inert.remove(timer[0])
        roles["gate"], roles["vibdelay"] = timer[0], inert[0]
    elif inert:
        roles["gate" if gate else "vibdelay"] = inert[0]
    for c in pointers:
        name = (
            "pulse"
            if True in {s.indexed for s in trace.stores if s.origin == c}
            else "filt"
        )
        if name in roles:
            raise SidParseError("instrument columns are not greloc's")
        roles[name] = c
    if len(roles) != len(cols):
        raise SidParseError("instrument columns are not greloc's")
    return roles


def _columns(trace, planted):
    """The instrument columns the planted instrument byte 1 indexes, and their stride."""
    cols = sorted(
        {
            r.addr
            for r in trace.reads
            if r.origin in planted and r.value == 1 and r.exact
        }
    )
    num = cols[1] - cols[0] if len(cols) > 2 else 0
    if not num or any(b - a != num for a, b in zip(cols, cols[1:])):
        raise SidParseError("instrument columns are not evenly spaced")
    return cols, num


def _anchor(trace, anchors, ours):
    """The frequency table of ``anchors`` whose bytes reach a frequency register,
    loaded by an index from the data ``ours`` holds."""
    loads = {(r.addr, r.base) for r in trace.reads if r.origin in ours}
    return next(
        (
            a
            for addr, org, _, base, _ in trace.sid
            if addr & 0x1F in _FREQ and (org, base) in loads
            for a in anchors
            if a[0] <= org < a[0] + 2 * a[2]
        ),
        None,
    )


def _note_bases(trace):
    """``(low, high)``: the operands of the loads, by one index loaded from data, of
    the two frequency bytes a voice's registers are written from: the note table
    the player indexes; else None."""
    loads = {(r.addr, r.base): r for r in trace.reads if r.origin is not None}
    voices = {}
    for addr, org, _, base, _ in trace.sid:
        reg, read = addr & 0x1F, loads.get((org, base))
        if reg in _FREQ and read is not None:
            key = (reg // 7, read.origin, read.value)
            voices.setdefault(key, {})[reg % 7] = read
    bases = {(v[0].base, v[1].base) for v in voices.values() if len(v) == 2}
    return bases.pop() if len(bases) == 1 else None


def instrument_layout(bench, anchors, tables) -> dict:
    """The instrument columns, tables and frequency table the player reads, as
    :class:`~pygoattracker.packed.PackedLayout` fields; the tables end where the
    orderlists start, past the song and pattern ``tables``.

    A planted note shows the columns its instrument byte indexes and the wavetable
    pointer (instrument 1's, else the tune's first nonzero bytes or 1s); planted with instrument 1's other
    columns 1, no wavetable and speed commands on the other voices (or as few of
    these as the player plays through), what each column is and where the speed
    table is; where no vibrato or command shows it, planted with every pointer at
    a row 1 of two set rows, where the last table's right column starts.
    """
    lay = bench.lay
    rooms = {a + k for a, n in zip(lay.patt_addrs, bench.patt_sizes) for k in range(n)}
    buf = bench.plant([_voice(bench)])
    planted = rooms & set(buf)
    end_byte = {a for a in planted if buf[a] == pattern_bytes((_END,))[0]}

    def held(trace):
        return any(r.addr in end_byte for r in trace.reads)

    def run(cols, until):
        trace = _run(bench.boot({**buf, **cols}), until)
        if trace is None:
            raise SidParseError("the planted note does not play")
        return trace

    trace = run({}, held)
    cols, num = _columns(trace, planted)
    binary = max(tables) > cols[0]
    tstart = cols[0] + num * len(cols)
    end = min(tables) if binary else None
    if end is None:
        end = min((a for a in lay.order_addrs if a > tstart), default=None)
    if end is None:
        raise SidParseError("no orderlist follows the tables")
    ours = planted | set(range(tstart, end))
    anchor, bases = _anchor(trace, anchors, ours), _note_bases(trace)
    wave = [c for c in cols if _row1(trace.reads, c) == tstart]
    mem = bench.image.mem
    used = {c: next((v for v in mem[c : c + num] if v), 0) for c in cols}
    for cells in ([used, dict.fromkeys(cols, 1)] if not wave else []):
        trace = _run(bench.boot({**buf, **cells}), held)
        if trace is not None:
            bases = bases or _note_bases(trace)
            wave = [c for c in cols if _row1(trace.reads, c) == tstart]
            break
    if len(wave) != 1:
        raise SidParseError("no column indexes the wavetable after the instruments")
    ones = {c: int(c != wave[0]) for c in cols}
    try:
        effects = bench.plant(_effects(bench))
    except ProbeMiss:
        effects = buf
    for plant, cells in ((effects, ones), (buf, ones), (buf, dict.fromkeys(cols, 1))):
        try:
            trace = _run(bench.boot({**plant, **cells}), held)
        except SidParseError:
            trace = None
        if trace is not None:
            break
    else:
        raise SidParseError("the planted note does not play")
    buf = plant
    params = {a for a in rooms & set(buf) if buf[a] == 1} - planted
    roles = _roles(trace, cols, wave[0], tstart, binary)
    bases = bases or _note_bases(trace)
    lefts = {"wave": tstart}
    lefts.update(
        {
            k: _row1(trace.reads, roles[k], tstart)
            for k in ("pulse", "filt")
            if k in roles
        }
    )
    lefts = {k: v for k, v in lefts.items() if v is None or v < end}
    speed = _speed(trace.reads, params | {roles.get("vib")}, end)
    dead = lefts.get("filt", 0) is None
    if dead:
        del lefts["filt"]
    right = None
    if speed is None or dead:
        last, left = list(lefts.items())[-1]
        rows = {a + k: _LEFT[t] for t, a in lefts.items() for k in (0, 1)}
        trace = run({**dict.fromkeys(cols, 1), **rows}, held)
        bases = bases or _note_bases(trace)
        right = _right(trace.reads, roles[last], left)
    if dead:
        lefts["filt"], right = 2 * right - left, None
    lens, slen, zeros = _tiling(list(lefts.values()), right, speed, end)
    lens = dict(zip(lefts, lens))
    return dict(
        anchor=anchor,
        bases=bases,
        ins_start=cols[0],
        num_instr=num,
        pulse="pulse" in roles,
        filt="filt" in roles,
        vib="vib" in roles,
        gate="gate" in roles,
        cols=tuple(sorted(roles, key=roles.get)),
        tstart=tstart,
        wlen=lens["wave"],
        plen=lens.get("pulse", 0),
        flen=lens.get("filt", 0),
        slen=slen,
        zeros=zeros,
        inline=binary and not zeros,
    )
