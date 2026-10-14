"""greloc's packed song data, read as a :class:`~pygoattracker.typed.TypedSong` and written back.

``decode`` states the packed bytes as what the packed player does with them;
``pack`` re-emits every byte at its address from the typed song and its layout;
``read_build`` reads the greloc defines from the instructions ``player.s`` assembles.
"""

from dataclasses import dataclass, replace

from pysidtracker import find_code_all

from pygoattracker import constants as C
from pygoattracker.model import PlayPattern, Repeat
from pygoattracker.typed import (
    FilterSet,
    GateOff,
    GateOn,
    Instrument,
    Rest,
    Row,
    SetTempo,
    SetWave,
    TypedSong,
    command_bytes,
    command_of,
    filter_bytes,
    filter_row,
    note_byte,
    note_of,
    pulse_bytes,
    pulse_row,
    speed_bytes,
    speed_row,
    wave_bytes,
    wave_row,
)

_FX, _FXONLY, _NOTE, _PACKEDREST = 0x40, 0x50, 0x60, 0xC0
NONE = 0x100  # an instrument class boundary past the last instrument


@dataclass(frozen=True)
class PackedLayout:
    """Where greloc put each part of the song data, and which instrument columns exist."""

    freq_addr: int
    firstnote: int
    notes: int
    songtbl: int
    songs: int
    order_addrs: tuple
    patt_lo: int
    patt_addrs: tuple
    ins_start: int
    num_instr: int
    pulse: bool
    filt: bool
    vib: bool
    gate: bool
    tstart: int
    wlen: int
    plen: int
    flen: int
    slen: int
    zeros: int
    gate_flags: bool = False  # the gate column keeps the hard-restart/legato bits


@dataclass(frozen=True)
class PlayerBuild:
    """The greloc defines in the player's code that change what the song data does.

    ``ghost``: the full-buffering ghost registers (None: the player writes the chip);
    ``alt``: altplayer.s, whose flush ascends. Below ``firstnohr`` hard restart, below
    ``firstlegato`` gate-off, then legato. None: a define the code does not state.
    """

    ghost: int | None = None
    alt: bool | None = None
    adparam: int | None = None
    srparam: int | None = None
    firstnohr: int | None = None
    firstlegato: int | None = None
    deftempo: int | None = None
    insvib: bool | None = None
    gatecol: bool | None = None
    fixed: tuple | None = None
    pulseopt: bool | None = None
    realtime: bool | None = None


@dataclass(frozen=True)
class PlayerState:
    """What the first play call starts from: the ghost image and the globals init keeps."""

    registers: tuple
    cutoff: int
    filttype: int
    volume: int
    funk: tuple


class _Ambiguous(Exception):
    pass


def _one(image, spec, **want):
    at = [m for m in find_code_all(image, spec) if want.items() <= m.captures.items()]
    if len(at) > 1:
        raise _Ambiguous(spec)
    return at[0] if at else None


def _branch(m, off):
    d = m.u8(off + 1)
    return m.addr + off + 2 + d - (d & 0x80) * 2


def _has(image, spec):
    return bool(find_code_all(image, spec))


def read_build(image) -> PlayerBuild:
    """Every define of :class:`PlayerBuild` the image's player code states."""
    try:
        return _read_build(image)
    except _Ambiguous:
        return PlayerBuild()


def _read_build(image) -> PlayerBuild:
    asc = _one(image, "A2 00 BD {g:w} 9D ?? ?? E8 E0 19 D0")
    flush = asc or _one(image, "A2 18 BD {g:w} 9D ?? ?? CA 10")
    hr = _one(image, "A9 {x} 9D ?? ?? A9 {y} 9D ?? ?? A9 FE 9D")
    ad = sr = None
    if hr is None:
        cmp = _one(image, "BD ?? ?? C9 {n} B0 ?? A9 FE 9D")
        skiphr = cmp.addr + 7 if cmp else None
    else:
        cmp = _one(
            image, "BD ?? ?? C9 {n} B0 ?? A9 ?? 9D ?? ?? A9 ?? 9D ?? ?? A9 FE 9D"
        )
        x, y = hr.captures["x"], hr.captures["y"]
        ad, sr = (x, y) if asc else (y, x)
        skiphr = hr.addr + 10
    legato = [
        m.captures["l"]
        for m in find_code_all(image, "C9 {l} 90 ?? B0")
        if _branch(m, 2) == skiphr
    ]
    tempo = _one(image, "A9 {t} 9D ?? ?? A9 01 9D {c:w} 9D")
    if tempo is None:  # no mt_initchn: not a player.s this reader knows
        return PlayerBuild()
    tick034 = _one(image, "9D ?? ?? BD ?? ?? 9D ?? ?? 60")
    insvib = any(
        m.captures["t"] == getattr(tick034, "addr", None)
        for m in find_code_all(image, "B9 ?? ?? 4C {t:w}")
    ) or _has(image, "BC ?? ?? B9 ?? ?? A8")
    c = tempo.captures["c"] if tempo else None
    gatecol = (
        None if c is None else _one(image, "BD {c:w} DD ?? ?? F0", c=c) is not None
    )
    fixed = None
    if gatecol is False:
        gate = _one(image, "BD {c:w} C9 {g} F0", c=c)
        waves = {m.captures["w"] for m in find_code_all(image, "BD {w:w} 3D ?? ?? 9D")}
        first = (
            _one(image, "A9 {f} 9D {w:w}", w=waves.pop()) if len(waves) == 1 else None
        )
        if gate and first:
            fixed = gate.captures["g"], first.captures["f"]
    return PlayerBuild(
        ghost=flush.captures["g"] if flush else None,
        alt=None if flush is None else asc is not None,
        adparam=ad,
        srparam=sr,
        firstnohr=cmp.captures["n"] if cmp else (NONE if hr else 1),
        firstlegato=legato[0] if len(legato) == 1 else NONE,
        deftempo=tempo.captures["t"],
        insvib=insvib,
        gatecol=gatecol,
        fixed=fixed,
        pulseopt=_has(image, "BC ?? ?? F0 ?? 1D ?? ?? F0"),
        realtime=(
            None if c is None else _one(image, "BD {c:w} F0 ?? BC", c=c) is not None
        ),
    )


def read_state(image, build: PlayerBuild) -> PlayerState | None:
    """The ghost image the first play call flushes, and the immediates init keeps.

    ``mt_filtdone`` loads cutoff, mode and volume and stores them to the ghost; a
    build without filter code stores no cutoff, which the image then holds.
    """
    g, mem = build.ghost, image.mem
    if g is None:
        return None
    try:
        filt = _one(image, "A9 {t} 09 {f} 8D {r:w}", r=g + 0x18)
        cut = _one(image, "A9 {c} 8D {r:w}", r=g + 0x16)
        funk = _one(image, "C9 02 B0 ?? A8 49 01 9D ?? ?? B9 {k:w} E9 00")
    except _Ambiguous:
        return None
    if filt is None:
        return None
    k = funk.captures["k"] if funk else None
    return PlayerState(
        registers=tuple(mem[g : g + C.SID_REGISTERS]),
        cutoff=cut.captures["c"] if cut else mem[g + 0x16],
        filttype=filt.captures["t"],
        volume=filt.captures["f"],
        funk=(mem[k], mem[k + 1]) if funk else (8, 5),
    )


# ---- packed bytes <-> meaning ------------------------------------------------


def _tempo(cmd, packing):
    """greloc writes tempo ``t >= 3`` as ``t - 1``, the packed player's countdown."""
    if not isinstance(cmd, SetTempo) or cmd.tempo < (3 if packing else 2):
        return cmd
    return replace(cmd, tempo=cmd.tempo + (-1 if packing else 1))


def pattern_rows(mem, pos):
    """One packed pattern at ``pos``: typed rows, and the address past its end mark."""
    rows = []
    while mem[pos]:
        ins = cmd = None
        b = mem[pos]
        if b < _FX:
            ins, pos = b, pos + 1
            b = mem[pos]
        if b >= _PACKEDREST:
            rows.append(Row(Rest(), ins, None, 0x100 - b))
            pos += 1
            continue
        if b < _NOTE:
            num, pos = b & 0x0F, pos + 1
            data = 0
            if num:
                data, pos = mem[pos], pos + 1
            cmd = _tempo(command_of(num, data), False)
            if b >= _FXONLY:
                rows.append(Row(Rest(), ins, cmd))
                continue
            b = mem[pos]
        rows.append(Row(note_of(b), ins, cmd))
        pos += 1
    return tuple(rows), pos + 1


def pattern_bytes(rows) -> bytes:
    """A pattern's packed bytes, end mark included."""
    out = bytearray()
    for r in rows:
        if r.instrument is not None:
            out.append(r.instrument)
        if r.length > 1:
            out.append(0x100 - r.length)
            continue
        if r.command is not None:
            num, data = command_bytes(_tempo(r.command, True))
            only = isinstance(r.note, Rest)
            out.append((_FXONLY if only else _FX) | num)
            if num:
                out.append(data)
            if only:
                continue
        out.append(note_byte(r.note))
    return bytes(out + b"\0")


def _wave_editor(left, right, nwd):
    """A packed wavetable row in editor form: waveforms $10 up, silent ones by nibble."""
    if left == C.TABLEJUMP or left >= C.WAVECMD:
        return left, right
    if nwd:
        return (
            C.WAVESILENT | left if 0 < left <= C.WAVELASTDELAY else left
        ), right ^ 0x80
    if left <= C.WAVELASTDELAY:
        return left, right ^ 0x80
    w = left - 0x10
    return (C.WAVESILENT | w if w <= C.WAVELASTDELAY else w), right ^ 0x80


def _wave_packed(left, right, nwd):
    if left == C.TABLEJUMP or left >= C.WAVECMD:
        return left, right
    w = left & 0x0F if left >= C.WAVESILENT else left
    return (w + 0x10 if left > C.WAVELASTDELAY and not nwd else w), right ^ 0x80


def _filter_packed(row):
    if isinstance(row, FilterSet):
        return 0x80 | row.passband >> 1, row.resonance << 4 | row.channels
    return filter_bytes(row)


def _filter_typed(left, right):
    if left != C.TABLEJUMP and left & 0x80:
        return FilterSet((left & 0x7F) << 1, right >> 4, right & 0x0F)
    return filter_row(left, right)


def _columns(lay):
    names = ["ad", "sr", "wave"] + ["filt"] * lay.filt + ["pulse"] * lay.pulse
    return names + ["vib", "vibdelay"] * lay.vib + ["gate", "first"] * lay.gate


def _instrument(i, col, build, fixed, flags):
    """Instrument ``i`` from its packed columns: greloc's ``vibdelay - 1`` undone,
    FIXEDPARAMS' immediates, the hard-restart and legato classes from the gate
    column's own bits where it keeps them, else from the build's class bounds."""
    gate, first = (col["gate"], col["first"]) if "gate" in col else fixed
    return Instrument(
        col["ad"],
        col["sr"],
        col["wave"],
        col.get("pulse", 0),
        col.get("filt", 0),
        col.get("vib", 0),
        (col.get("vibdelay", 0xFF) + 1) & 0xFF,
        gate & 0x3F,
        not gate & 0x80 if flags else i < (build.firstnohr or NONE),
        bool(gate & 0x40) if flags else i >= (build.firstlegato or NONE),
        {0: None, 0xFE: GateOff(), 0xFF: GateOn()}.get(first, SetWave(first)),
        f"INSTR{i:02X}",
    )


def decode(mem, lay, build, header, orderlists, nowavedelay, simplepulse) -> TypedSong:
    """The packed song data at ``lay`` as a :class:`TypedSong`."""
    n, cols = lay.num_instr, _columns(lay)
    base = lay.ins_start
    arrays = {k: mem[base + j * n : base + (j + 1) * n] for j, k in enumerate(cols)}
    instruments = tuple(
        _instrument(
            i + 1,
            {k: v[i] for k, v in arrays.items()},
            build,
            build.fixed or (2, 9),
            lay.gate_flags,
        )
        for i in range(n)
    )

    def table(start, length, f):
        return tuple(f(mem[start + k], mem[start + length + k]) for k in range(length))

    fpos = lay.tstart + 2 * lay.wlen
    ppos = fpos + 2 * lay.flen
    spos = ppos + 2 * lay.plen + lay.zeros
    limit = C.MAX_STR - 1
    return TypedSong(
        name=header.name[:limit],
        author=header.author[:limit],
        copyright=header.released[:limit],
        subtunes=tuple(orderlists),
        patterns=tuple(pattern_rows(mem, a)[0] for a in lay.patt_addrs),
        instruments=instruments,
        wavetable=table(
            lay.tstart,
            lay.wlen,
            lambda a, b: wave_row(*_wave_editor(a, b, nowavedelay)),
        ),
        filtertable=table(fpos, lay.flen, _filter_typed),
        pulsetable=table(ppos, lay.plen, lambda a, b: pulse_row(a, b, simplepulse)),
        speedtable=tuple(
            speed_row(mem[spos + k], mem[spos + lay.slen + lay.zeros + k])
            for k in range(lay.slen)
        ),
        simplepulse=simplepulse,
        speed0=speed_row(mem[spos - 1], mem[spos + lay.slen]) if lay.zeros else None,
        firstnote=lay.firstnote,
        freq=tuple(
            mem[lay.freq_addr + k] | mem[lay.freq_addr + lay.notes + k] << 8
            for k in range(lay.notes)
        ),
    )


def orderlist_bytes(orderlist) -> bytes:
    """A packed orderlist: a repeat trails the pattern it repeats."""
    out, entries, k = bytearray(), orderlist.entries, 0
    while k < len(entries):
        e = entries[k]
        nxt = entries[k + 1] if k + 1 < len(entries) else None
        if isinstance(e, Repeat) and isinstance(nxt, PlayPattern):
            out += bytes([nxt.to_byte(), e.to_byte()])
            k += 2
        else:
            out.append(e.to_byte())
            k += 1
    return bytes(out + bytes([C.LOOPSONG, orderlist.restart]))


def _instrument_bytes(ins, lay):
    raw = ins.raw()
    flags = 0xC0 if lay.gate_flags else 0
    col = {
        "ad": raw.attack_decay,
        "sr": raw.sustain_release,
        "wave": raw.wave_ptr,
        "pulse": raw.pulse_ptr,
        "filt": raw.filter_ptr,
        "vib": raw.vibrato_param,
        "vibdelay": (raw.vibrato_delay - 1) & 0xFF,
        "gate": raw.gateoff_timer & (0x3F | flags),
        "first": raw.first_wave,
    }
    return [col[k] for k in _columns(lay)]


def pack(song: TypedSong, info) -> list[tuple[int, bytes]]:
    """Every byte of the song data, as ``(address, bytes)`` pieces of the image.

    ``info`` is the :class:`~pygoattracker.sid.PackedInfo` decompiling the image left.
    """
    lay, nowavedelay = info.layout, info.nowavedelay

    def split(addrs):
        return bytes(a & 0xFF for a in addrs) + bytes(a >> 8 for a in addrs)

    def pairs(rows, f):
        p = [f(r) for r in rows]
        return bytes(a for a, _ in p) + bytes(b for _, b in p)

    ins = [_instrument_bytes(i, lay) for i in song.instruments]
    columns = bytes(row[j] for j in range(len(_columns(lay))) for row in ins)
    rows0 = [song.speed0] if song.speed0 else []
    st = [speed_bytes(r) for r in rows0 + list(song.speedtable)]
    out = [
        (
            lay.freq_addr,
            bytes(f & 0xFF for f in song.freq) + bytes(f >> 8 for f in song.freq),
        ),
        (lay.songtbl, split(lay.order_addrs)),
        (lay.patt_lo, split(lay.patt_addrs)),
        (lay.ins_start, columns),
        (
            lay.tstart,
            pairs(song.wavetable, lambda r: _wave_packed(*wave_bytes(r), nowavedelay)),
        ),
        (lay.tstart + 2 * lay.wlen, pairs(song.filtertable, _filter_packed)),
        (
            lay.tstart + 2 * (lay.wlen + lay.flen),
            pairs(song.pulsetable, pulse_bytes),
        ),
        (
            lay.tstart + 2 * (lay.wlen + lay.flen + lay.plen),
            bytes(a for a, _ in st) + bytes(b for _, b in st),
        ),
    ]
    channels = [o for s in song.subtunes for o in s.channels]
    out += [(a, orderlist_bytes(o)) for a, o in zip(lay.order_addrs, channels)]
    out += [(a, pattern_bytes(p)) for a, p in zip(lay.patt_addrs, song.patterns)]
    return out
