"""The canonical text of what a GoatTracker 2 subtune plays (``pysidtracker equiv``).

Reachable data only, table rows merged as automata and everything renumbered by
first use (:mod:`pysidtracker.automaton`), printed in the song grammar; see
``docs/grammar.md``.
"""

from dataclasses import replace

from pysidtracker.automaton import minimise, number_chains

from pygoattracker import constants as C
from pygoattracker import text
from pygoattracker.errors import GoatTrackerError
from pygoattracker.model import Orderlist, Pattern, PlayPattern, Subtune, Transpose
from pygoattracker.typed import (
    CutoffSet,
    DoNothing,
    FilterMod,
    FilterSet,
    FunkTempo,
    Instrument,
    Jump,
    Note,
    PulseMod,
    PulseSet,
    Row,
    SetFilterPtr,
    SetPulsePtr,
    SetWavePtr,
    SimplePulseMod,
    SimplePulseSet,
    SPEED_COMMANDS,
    Stop,
    TypedSong,
    WaveCommand,
    WaveStep,
    rows_of,
    speed_row,
    wave_row,
)

TABLES = ("wave", "pulse", "filter")
_POINTERS = {SetWavePtr: "wave", SetPulsePtr: "pulse", SetFilterPtr: "filter"}
_SONG_STOPS = (DoNothing, SetWavePtr, FunkTempo)


def _signed(byte: int) -> int:
    return byte - 0x100 if byte & 0x80 else byte


def _command(cmd, ref):
    """``cmd`` with its row and speed references mapped by ``ref``; command 0's
    data, which no player reads, dropped."""
    if isinstance(cmd, DoNothing):
        return DoNothing()
    if type(cmd) in _POINTERS:
        return type(cmd)(ref(_POINTERS[type(cmd)], cmd.row))
    if isinstance(cmd, SPEED_COMMANDS):
        return replace(cmd, speed=ref("speed", cmd.speed))
    return cmd


def _instrument(ins: Instrument, ref) -> Instrument:
    """``ins`` nameless, references mapped; the vibrato speed counts only with a delay."""
    return replace(
        ins,
        wave_ptr=ref("wave", ins.wave_ptr),
        pulse_ptr=ref("pulse", ins.pulse_ptr),
        filter_ptr=ref("filter", ins.filter_ptr),
        vibrato_speed=ref("speed", ins.vibrato_speed if ins.vibrato_delay else 0),
        name="",
    )


def _row(row: Row, ref) -> Row:
    """An editor row with its instrument and command references mapped."""
    ins = ref("instrument", row.instrument) if row.instrument else None
    return Row(row.note, ins, _command(row.command, ref))


class _Table:
    """A wave, pulse or filter table as the player's executor walks it.

    A node is a row run as the executor finds it, or ``("S", ptr)``: a pointer set
    onto a jump row, which stops the song.
    """

    def __init__(self, kind: str, table, simplepulse: bool = False):
        self.kind, self.simple = kind, simplepulse
        pad = [0] * (0x100 - len(table.left))
        self.left, self.right = list(table.left) + pad, list(table.right) + pad

    def at(self, ptr: int) -> tuple:
        """The ``(left, right)`` bytes the executor reads at ``ptr``."""
        return self.left[(ptr - 1) & 0xFF], self.right[(ptr - 1) & 0xFF]

    def land(self, ptr: int):
        """``ptr`` as a row the executor runs next (None: halted); a jump row it
        would run as an ordinary row has no typed form."""
        if ptr and self.at(ptr)[0] == C.TABLEJUMP:
            raise GoatTrackerError(
                f"{self.kind}table: a jump onto the jump row {ptr} plays as a row"
            )
        return ptr or None

    def resolve(self, ptr: int):
        """The row run after advancing to ``ptr`` (None: halted)."""
        left, right = self.at(ptr)
        return self.land(right if left == C.TABLEJUMP else ptr)

    def entry(self, ptr: int):
        """The node a pointer set to ``ptr`` starts (None: no program)."""
        if not ptr:
            return None
        return ("S", ptr) if self.at(ptr)[0] == C.TABLEJUMP else ptr

    def step(self, node, ref) -> tuple:
        """``(rows, succ)``: the typed rows ``node`` runs and the node after it."""
        if isinstance(node, tuple):
            target = self.land(self.at(node[1])[1])
            if self.kind == "wave":
                return (_jump(target or 0),), self.step(node[1], ref)[1]
            return (Stop(),), target
        left, right = self.at(node)
        nxt = (node + 1) & 0xFF
        if self.kind == "wave":
            row = wave_row(0 if left == C.TABLEJUMP else left, right)
            if isinstance(row, WaveCommand):
                stops = isinstance(row.command, _SONG_STOPS)
                row = WaveCommand(DoNothing() if stops else _command(row.command, ref))
            return (row,), self.resolve(nxt)
        if self.kind == "pulse":
            if self.simple:
                row = (
                    SimplePulseSet(right)
                    if left & 0x80
                    else SimplePulseMod(left, right)
                )
            elif left & 0x80:
                row = PulseSet((left & 0x0F) << 8 | right)
            else:
                row = PulseMod(left, _signed(right))
            return (row,), self.resolve(nxt)
        if not left & 0x80:
            row = FilterMod(left, _signed(right)) if left else CutoffSet(right)
            return (row,), self.resolve(nxt)
        rows = (FilterSet(left & 0x70, right >> 4, right & 0x0F),)
        if self.at(nxt)[0] == 0:
            rows += (CutoffSet(self.at(nxt)[1]),)
            nxt = (nxt + 1) & 0xFF
        return rows, self.resolve(nxt)

    def graph(self, entries, ref) -> tuple:
        """``(rows, succ)`` of every node ``entries`` reach."""
        rows, succ, stack = {}, {}, [e for e in entries if e is not None]
        while stack:
            node = stack.pop()
            if node not in rows:
                rows[node], nxt = self.step(node, ref)
                if nxt is not None:
                    succ[node] = nxt
                    stack.append(nxt)
        return rows, succ


def _jump(target: int):
    return Jump(target) if target else Stop()


class _Program:
    """One table's reachable nodes merged into classes, laid out as rows."""

    def __init__(self, table: _Table, entries, ref):
        self.table = table
        rows, succ = table.graph(entries, ref)
        self.cls = minimise(rows, succ)
        self.rep = {}
        for node, c in self.cls.items():
            self.rep.setdefault(c, node)
        self.rows = {c: rows[n] for c, n in self.rep.items()}
        self.succ = {self.cls[n]: self.cls[s] for n, s in succ.items()}
        self.order, self.pos = [], {}

    def of(self, ptr: int):
        """The class a pointer set to ``ptr`` starts, None for none."""
        node = self.table.entry(ptr)
        return None if node is None else self.cls[node]

    def _is_jump(self, c) -> bool:
        """A pulse or filter pointer onto a jump row: laid out as that jump."""
        return self.table.kind != "wave" and isinstance(self.rows[c][0], Stop)

    def _trailer(self, k: int) -> bool:
        """Whether the ``k``-th class is followed by a jump or stop row: unless its
        successor comes next, and a filter set does not take it as its cutoff."""
        c = self.order[k]
        nxt = self.succ.get(c)
        if self._is_jump(c):
            return False
        if nxt is None or self.order[k + 1 : k + 2] != [nxt]:
            return True
        return isinstance(self.rows[c][-1], FilterSet) and isinstance(
            self.rows[nxt][0], CutoffSet
        )

    def number(self, entries) -> None:
        """Lay out the classes ``entries`` (first-use order) reach."""
        num = number_chains(entries, self.succ)
        self.order = sorted(num, key=num.get)
        at = 1
        for k, c in enumerate(self.order):
            self.pos[c] = at
            at += len(self.rows[c]) + self._trailer(k)

    def render(self, ref) -> tuple:
        """The laid-out typed rows, references mapped by ``ref``."""
        out = []
        for k, c in enumerate(self.order):
            nxt = self.succ.get(c)
            target = Jump(self.pos[nxt]) if nxt is not None else Stop()
            if not self._is_jump(c):
                out += self.table.step(self.rep[c], ref)[0]
            if self._is_jump(c) or self._trailer(k):
                out.append(target)
        return tuple(out)


def _first(keys: dict, raw: dict) -> dict:
    """``{key: value}`` of the first raw item each key is the key of."""
    out = {}
    for n, key in keys.items():
        out.setdefault(key, raw[n])
    return out


def _notes(sub, rows, wave_rows, lastnotes) -> set:
    """The frequency-table entries the player can read: every pattern note under
    every transpose, wavetable notes on those, and each last note and the next."""
    trans = {0} | {
        e.semitones for o in sub.channels for e in o.entries if isinstance(e, Transpose)
    }
    base = {0} | {
        r.note.pitch + t
        for p in rows
        for r in p
        if isinstance(r.note, Note)
        for t in trans
    }
    steps = [
        (
            wave_row(0, r.row if isinstance(r, Jump) else 0)
            if isinstance(r, (Jump, Stop))
            else r
        )
        for r in wave_rows
    ]
    notes = [r.note for r in steps if isinstance(r, WaveStep) and r.note is not None]
    last = (
        base
        | set(lastnotes)
        | {n.value + b if n.relative else n.value for n in notes for b in base}
    )
    return {n & 0x7F for n in last} | {(n + 1) & 0x7F for n in last}


def canonical_song(song: TypedSong, subtune: int, freq=(), lastnotes=()) -> TypedSong:
    """What ``song``'s 0-based ``subtune`` plays, as a one-subtune :class:`TypedSong`.

    The definition is ``docs/grammar.md``'s *Canonical text*; ``freq`` is the
    per-note tuning played, ``lastnotes`` each channel's initial last note.
    """
    raw = song.to_song()
    sub = raw.subtunes[subtune]
    played = [
        e.num for o in sub.channels for e in o.entries if isinstance(e, PlayPattern)
    ]
    rows = {
        n: rows_of(raw.patterns[n] if n < len(raw.patterns) else Pattern())
        for n in played
    }
    used = [1] + [r.instrument for n in rows for r in rows[n] if r.instrument]
    instruments = {n: song.instrument(n) for n in used}
    tables = {
        "wave": _Table("wave", raw.wavetable),
        "pulse": _Table("pulse", raw.pulsetable, song.simplepulse),
        "filter": _Table("filter", raw.filtertable),
    }
    speeds = _Table("speed", raw.speedtable)
    found = {kind: [] for kind in TABLES}

    def collect(kind, value):
        if kind in found:
            found[kind].append(tables[kind].entry(value))
        return value

    for ins in instruments.values():
        _instrument(ins, collect)
    for n in rows:
        for r in rows[n]:
            _row(r, collect)
    tables["wave"].graph(found["wave"], collect)
    progs = {k: _Program(tables[k], found[k], collect) for k in ("pulse", "filter")}
    ins_key: dict = {}

    def by_class(kind, value):
        if kind == "speed":
            return speeds.at(value) if value else None
        if kind == "instrument":
            return ins_key[value]
        return progs[kind].of(value)

    progs["wave"] = _Program(tables["wave"], found["wave"], by_class)
    ins_key.update({n: _instrument(i, by_class) for n, i in instruments.items()})
    patt_key = {n: tuple(_row(r, by_class) for r in rows[n]) for n in rows}
    patt_rep = _first({n: patt_key[n] for n in played}, rows)
    first_ins = [ins_key[1]] + [
        r.instrument for p in patt_rep for r in p if r.instrument
    ]
    ins_num = {k: i for i, k in enumerate(dict.fromkeys(first_ins), 1)}
    ins_rep = _first(ins_key, instruments)

    starts = {kind: [] for kind in TABLES}

    def first_use(kind, value):
        if kind in starts and value:
            starts[kind].append(progs[kind].of(value))
        return value

    for key in ins_num:
        _instrument(ins_rep[key], first_use)
    for key in patt_rep:
        for r in patt_rep[key]:
            _row(r, first_use)
    progs["wave"].number(starts["wave"])
    for c in progs["wave"].order:
        tables["wave"].step(progs["wave"].rep[c], first_use)
    for kind in ("pulse", "filter"):
        progs[kind].number(starts[kind])
    speed_num: dict = {}

    def final(kind, value):
        if kind == "speed":
            return (
                speed_num.setdefault(speeds.at(value), len(speed_num) + 1)
                if value
                else 0
            )
        if kind == "instrument":
            return ins_num[ins_key[value]]
        c = progs[kind].of(value)
        return 0 if c is None else progs[kind].pos[c]

    patt_num = {k: i for i, k in enumerate(patt_rep)}
    wave_rows = [r for rs in progs["wave"].rows.values() for r in rs]
    read = _notes(sub, patt_rep.values(), wave_rows, lastnotes)
    instruments_out = tuple(_instrument(ins_rep[k], final) for k in ins_num)
    wavetable = progs["wave"].render(final)
    patterns = tuple(tuple(_row(r, final) for r in patt_rep[k]) for k in patt_rep)
    channels = [
        Orderlist(
            [
                (
                    PlayPattern(patt_num[patt_key[e.num]])
                    if isinstance(e, PlayPattern)
                    else e
                )
                for e in o.entries
            ],
            o.restart,
        )
        for o in sub.channels
    ]
    return TypedSong(
        subtunes=(Subtune(channels),),
        patterns=patterns,
        instruments=instruments_out,
        wavetable=wavetable,
        pulsetable=progs["pulse"].render(final),
        filtertable=progs["filter"].render(final),
        speedtable=tuple(speed_row(*v) for v in speed_num),
        simplepulse=song.simplepulse,
        freq=tuple(f if k in read else 0 for k, f in enumerate(freq)),
    )


def canonical(model) -> str:
    """A :class:`~pygoattracker.sid.DecompiledSid` subtune's :func:`canonical_song`
    text, after its playback build (with the starting tempo the player derives),
    initial state and play wrapper as ``#`` lines."""
    info, song = model.info, model.typed
    playback = info.playback
    if playback.default_tempo is None:
        last = song.instrument(C.MAX_INSTR - 1)
        ad = last.attack_decay
        playback = replace(
            playback, default_tempo=ad - 1 if ad >= 2 and not last.wave_ptr else 5
        )
    head = f"# playback {playback!r}\n# state {info.state!r}\n# wrap {info.wrap!r}\n"
    lastnotes = dict(info.state.channels).get("lastnote", ()) if info.state else ()
    out = canonical_song(song, model.subtune, info.note_read, lastnotes)
    return head + text.dumps(out)
