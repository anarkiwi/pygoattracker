"""greloc's packed song data, read as a :class:`~pygoattracker.typed.TypedSong` and written back.

``decode`` states the packed bytes as what the packed player does with them;
``pack`` re-emits every byte at its address from the typed song and its layout;
:class:`PlayerBuild` holds the greloc defines :mod:`pygoattracker.probe` measures.
"""

from dataclasses import dataclass, replace
from typing import NamedTuple

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
    songtbl_hi: int | None = None  # None: right after the low column
    patt_hi: int | None = None
    cols: tuple | None = None  # column names in memory order; None: greloc's


@dataclass(frozen=True)
class PlayerBuild:
    """The greloc defines of the player that change what the song data does.

    ``ghost``: the full-buffering ghost registers (None: the player writes the chip);
    ``alt``: altplayer.s, whose flush ascends. Below ``firstnohr`` hard restart, below
    ``firstlegato`` gate-off, then legato. None: a define the player does not show.
    ``wrapper``: a play wrapper outside the player flushes its zero-page ghost;
    ``funk``: the funk tempo table a ghost build's first play call starts from.
    """

    ghost: int | None = None
    alt: bool | None = None
    adparam: int | None = None
    srparam: int | None = None
    firstnohr: int | None = None
    firstlegato: int | None = None
    deftempo: int | None = None
    fixed: tuple | None = None
    pulseopt: bool | None = None
    realtime: bool | None = None
    wavedelay: bool | None = None
    simplepulse: bool | None = None
    funk: tuple | None = None
    wrapper: bool = False


@dataclass(frozen=True)
class PlayerState:
    """What the first play call starts from: the ghost image and the globals init keeps."""

    registers: tuple
    cutoff: int
    filttype: int
    volume: int
    funk: tuple


class PlayerCode(NamedTuple):
    """The ``[start, end)`` span of ``image`` one player's code is in (None: open)."""

    image: object
    start: int | None = None
    end: int | None = None


_TEMPO = "A9 {t} 9D ?? ?? A9 01 9D {c:w} 9D"


def player_span(image, data):
    """The :class:`PlayerCode` of the player whose song data starts at ``data``.

    greloc emits the jump table (``jmp mt_init``, ``jmp mt_play``), the player
    code and then the data, so an image holding several players keeps each one's
    code between its jump table and its data. The instance is the last
    ``mt_initchn`` below ``data`` and the last jump table before it whose
    ``mt_play`` lies between the two; the whole image without such a jump table.
    """
    tempo = find_code_all(image, _TEMPO, end=data)
    if not tempo:
        return PlayerCode(image)
    t = tempo[-1].addr
    bases = [
        m.addr
        for m in find_code_all(image, "4C ?? ?? 4C {p:w}", end=t)
        if m.addr < m.captures["p"] <= t
    ]
    return PlayerCode(image, bases[-1], data) if bases else PlayerCode(image)


# ---- packed bytes <-> meaning ------------------------------------------------


def _tempo(cmd, packing):
    """greloc writes tempo ``t >= 3`` as ``t - 1``, the packed player's countdown."""
    if not isinstance(cmd, SetTempo) or cmd.tempo < (3 if packing else 2):
        return cmd
    return replace(cmd, tempo=cmd.tempo + (-1 if packing else 1))


def pattern_rows(mem, pos):
    """One packed pattern at ``pos``: typed rows, and the address past its end mark.

    ``mt_rest`` checks for the end mark only after a row, so a pattern has at
    least one.
    """
    rows = []
    while mem[pos] or not rows:
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


def columns(lay):
    """The instrument columns of ``lay`` in memory order, greloc's unless it names them."""
    if lay.cols is not None:
        return list(lay.cols)
    names = ["ad", "sr", "wave"] + ["pulse"] * lay.pulse + ["filt"] * lay.filt
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
    n, cols = lay.num_instr, columns(lay)
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

    ppos = lay.tstart + 2 * lay.wlen
    fpos = ppos + 2 * lay.plen
    spos = fpos + 2 * lay.flen + lay.zeros
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
        pulsetable=table(ppos, lay.plen, lambda a, b: pulse_row(a, b, simplepulse)),
        filtertable=table(fpos, lay.flen, _filter_typed),
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
    return [col[k] for k in columns(lay)]


def pack(song: TypedSong, info) -> list[tuple[int, bytes]]:
    """Every byte of the song data, as ``(address, bytes)`` pieces of the image.

    ``info`` is the :class:`~pygoattracker.sid.PackedInfo` decompiling the image left.
    """
    lay, nowavedelay = info.layout, info.nowavedelay

    def split(lo, hi, addrs):
        hi = lo + len(addrs) if hi is None else hi
        return [
            (lo, bytes(a & 0xFF for a in addrs)),
            (hi, bytes(a >> 8 for a in addrs)),
        ]

    def pairs(rows, f):
        p = [f(r) for r in rows]
        return bytes(a for a, _ in p) + bytes(b for _, b in p)

    ins = [_instrument_bytes(i, lay) for i in song.instruments]
    cells = bytes(row[j] for j in range(len(columns(lay))) for row in ins)
    rows0 = [song.speed0] if song.speed0 else []
    st = [speed_bytes(r) for r in rows0 + list(song.speedtable)]
    out = [
        (
            lay.freq_addr,
            bytes(f & 0xFF for f in song.freq) + bytes(f >> 8 for f in song.freq),
        ),
        *split(lay.songtbl, lay.songtbl_hi, lay.order_addrs),
        *split(lay.patt_lo, lay.patt_hi, lay.patt_addrs),
        (lay.ins_start, cells),
        (
            lay.tstart,
            pairs(song.wavetable, lambda r: _wave_packed(*wave_bytes(r), nowavedelay)),
        ),
        (lay.tstart + 2 * lay.wlen, pairs(song.pulsetable, pulse_bytes)),
        (
            lay.tstart + 2 * (lay.wlen + lay.plen),
            pairs(song.filtertable, _filter_packed),
        ),
        (
            lay.tstart + 2 * (lay.wlen + lay.plen + lay.flen),
            bytes(a for a, _ in st) + bytes(b for _, b in st),
        ),
    ]
    channels = [o for s in song.subtunes for o in s.channels]
    out += [(a, orderlist_bytes(o)) for a, o in zip(lay.order_addrs, channels)]
    out += [(a, pattern_bytes(p)) for a, p in zip(lay.patt_addrs, song.patterns)]
    return out
