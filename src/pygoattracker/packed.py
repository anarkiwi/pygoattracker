"""greloc's packed song data, read as a :class:`~pygoattracker.typed.TypedSong` and written back.

``decode`` states the packed bytes as what the packed player does with them;
``pack`` re-emits every byte at its address from the typed song and its layout;
:class:`PlayerBuild` holds the greloc defines :mod:`pygoattracker.probe` measures,
and :class:`Build` the playback options they resolve to.
"""

import re
from dataclasses import dataclass, replace
from typing import NamedTuple

from lark import Transformer, v_args
from pysidtracker import REACH, ByteGrammar, Parsed, find_code_all
from pysidtracker.codescan import jump_table, play_entries
from pysidtracker.mos6502 import OP_JMP, OP_JSR, RETURN_OPS, disasm_len
from pysidtracker.facets import Entry, Variant, inferred

from pygoattracker import constants as C
from pygoattracker.legacy import (
    MST_FINEVIB,
    MST_FUNKTEMPO,
    MST_PORTAMENTO,
    make_speed_entry,
)
from pygoattracker.errors import SidParseError
from pygoattracker.model import Orderlist, PlayPattern, Repeat, Table, entry_from_byte
from pygoattracker.variant import tristate_entry
from pygoattracker.typed import (
    FilterSet,
    FunkTempo,
    GateOff,
    GateOn,
    Instrument,
    PortamentoDown,
    PortamentoUp,
    Rest,
    Row,
    SetTempo,
    SetWave,
    TonePortamento,
    TypedSong,
    Vibrato,
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

_FX, _FXONLY = 0x40, 0x50
_ROW_MAX = 4  # instrument, command, its data and the note
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
    inline: bool = False  # no speed table: effect parameters are the data itself
    songtbl_hi: int | None = None  # None: right after the low column
    patt_hi: int | None = None
    cols: tuple | None = None  # column names in memory order; None: greloc's
    channels: int = C.MAX_CHN  # orderlist pointers a song

    @property
    def binary(self) -> bool:
        """Whether the binary relocator of GoatTracker 2.0-2.28 laid the data out:
        its song and pattern tables follow the instruments and tables."""
        return self.songtbl > self.ins_start


def default_voices(channels: int | None) -> tuple | None:
    """Channel ``c`` on chip ``c // 3``, voice ``c % 3`` (None: the song's own)."""
    if channels is None:
        return None
    return tuple(
        (c // C.MAX_CHN) * 0x20 + (c % C.MAX_CHN) * C.VOICE_REG_SIZE
        for c in range(channels)
    )


@dataclass(frozen=True)
class PlayerBuild:
    """The greloc defines of the player that change what the song data does.

    ``ghost``: the full-buffering ghost registers (None: the player writes the chip);
    ``alt``: altplayer.s, whose flush ascends. Below ``firstnohr`` hard restart, below
    ``firstlegato`` gate-off, then legato. None: a define the player does not show.
    ``pulsestep``: the pulse change a +1 modulation speed makes;
    ``funk``: the funk tempo table a ghost build's first play call starts from;
    ``channels``: the channels the player inits; ``idle``: those its play never
    runs; ``voices``: each one's register offset from ``$D400`` (``$20`` a chip),
    None where its registers reach no chip; ``overlay``: each one's ``(and, or)``
    on the control byte it writes;
    ``wavedelay``: waveforms are shifted up $10 behind a delay test of $01-$0F (False:
    stored as they are); ``lowdelay``: a player storing waveforms as they are still
    delays $01-$07, before v2.18; ``vibinit``: a note's init loads the instrument's
    vibrato parameter (False: only an effect 0 on its row does);
    ``nohrwave``: a note's init of an instrument without hard restart runs the
    wavetable on its own call (a player keeping the flag in the gate column);
    ``noeffects``: instrument vibrato reads the current instrument live (greloc
    NOEFFECTS), not the parameter latched at note init.
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
    pulsestep: int | None = None
    realtime: bool | None = None
    wavedelay: bool | None = None
    lowdelay: bool | None = None
    vibinit: bool | None = None
    nohrwave: bool | None = None
    simplepulse: bool | None = None
    noeffects: bool | None = None
    funk: tuple | None = None
    channels: int | None = None
    idle: tuple = ()
    voices: tuple | None = None
    overlay: tuple | None = None

    def variant(self) -> Variant:
        """The defines as facets: those the player shows are ``inferred`` from the
        probe, the rest ``default`` to the editor's."""
        adparam = (
            None if self.adparam is None else self.adparam << 8 | (self.srparam or 0)
        )
        entries = {
            "adparam": tristate_entry(adparam, C.DEFAULT_ADPARAM),
            "optimize_pulse": tristate_entry(self.pulseopt, True),
            "optimize_realtime": tristate_entry(self.realtime, True),
            "pulse_step": tristate_entry(self.pulsestep or None, 1),
            "default_tempo": tristate_entry(self.deftempo, None),
            "voices": tristate_entry(
                self.voices or default_voices(self.channels), None
            ),
            "idle": Entry(self.idle, inferred("probe")),
            "overlay": tristate_entry(self.overlay, None),
            "vibinit": tristate_entry(self.vibinit, True),
            "nohr_wave": tristate_entry(self.nohrwave, False),
        }
        return Variant(entries)

    def resolve(self, **decoded) -> "Build":
        """The :class:`Build` these defines play with, the editor's where not shown;
        ``decoded`` the options read from the song data rather than the player."""
        variant = self.variant()
        return Build(**{name: variant[name] for name in variant}, **decoded)


@dataclass(frozen=True)
class Build:
    """The playback options a :class:`~pygoattracker.player.Player` runs with.

    ``adparam``: the hard-restart ADSR (AD high byte, SR low byte);
    ``optimize_pulse``/``optimize_realtime``: the pulse/realtime command skips;
    ``pulse_step``: the pulse change a +1 modulation speed makes;
    ``simplepulse``: the packed SIMPLEPULSE pulse table and code path;
    ``live_vibrato``: instrument vibrato reads the current instrument's param every
    tick (greloc NOEFFECTS) instead of the one latched at note init;
    ``default_tempo``: greloc DEFAULTTEMPO, every channel's starting countdown (None:
    the editor's instrument-63 rule over tempo 5);
    ``init_regs``: ``(reg, value)`` SID writes of the tune's init routine;
    ``voices``: each channel's register offset from ``$D400`` (``$20`` a chip, None:
    silent; None: channel ``c`` on chip ``c // 3``, voice ``c % 3``);
    ``calls``: player ticks a frame runs; ``idle``: channels the play never runs;
    ``overlay``: each channel's ``(and, or)`` on its control byte (None: as written);
    ``vibstop``: a vibrato delay of zero stops the instrument vibrato (the binary
    relocator's players, which count the delay as stored down to one);
    ``vibinit``: a note's init loads the instrument's vibrato parameter;
    ``nohr_wave``: a note's init of an instrument without hard restart runs the
    wavetable on its own tick.
    """

    adparam: int = C.DEFAULT_ADPARAM
    optimize_pulse: bool = True
    optimize_realtime: bool = True
    pulse_step: int = 1
    simplepulse: bool = False
    live_vibrato: bool = False
    default_tempo: int | None = None
    init_regs: tuple = ()
    voices: tuple | None = None
    calls: int = 1
    idle: tuple = ()
    overlay: tuple | None = None
    vibstop: bool = False
    vibinit: bool = True
    nohr_wave: bool = False


@dataclass(frozen=True)
class PlayerState:
    """What the first play call starts from: the ghost image and the globals init keeps.

    ``ghost``: ``registers`` is the full-buffering ghost; else they are the SID the
    first tick writes from the channel and filter variables init leaves as loaded.
    ``init``: the ``(register, value)`` SID writes of a chip-writing build's
    song-init call, which loads the channel registers from those variables.
    ``channels``: ``(field, values)`` of each channel variable init leaves as loaded
    (``mt_chnlastnote``, ``mt_chnvibtime``, ``mt_chnvibdelay``), one value per channel
    in the :class:`~pygoattracker.player.Player`'s terms; absent where the build
    shows none.
    """

    registers: tuple
    cutoff: int
    filttype: int
    volume: int
    funk: tuple
    ghost: bool = True
    init: tuple = ()
    channels: tuple = ()


class PlayerCode(NamedTuple):
    """The ``[start, end)`` span of ``image`` one player's code is in (None: open)."""

    image: object
    start: int | None = None
    end: int | None = None


_TEMPO = "A9 {t} 9D ?? ?? A9 01 9D {c:w} 9D"
_JUMPS = "4C {init:w} 4C ?? ??"
_TABLE = 6  # jmp mt_init, jmp mt_play


def player_span(image, data):
    """The :class:`PlayerCode` of the player whose song data starts at ``data``.

    greloc emits the jump table (``jmp mt_init``, ``jmp mt_play``), the player
    code and then the data, so an image holding several players keeps each one's
    code between its jump table and its data. The instance is the last
    ``mt_initchn`` below ``data`` and the last jump table before it whose
    ``mt_init`` lies between the two (a table's first entry, where a third jump
    follows; a play jump may lead out of the player, into a wrapper), else the
    last whose ``mt_play`` does (:func:`~pysidtracker.codescan.jump_table`); the
    whole image without such a jump table.
    """
    tempo = find_code_all(image, _TEMPO, end=data)
    if not tempo:
        return PlayerCode(image)
    t = tempo[-1].addr
    tables = {
        m.addr
        for m in find_code_all(image, _JUMPS, end=t)
        if m.addr < m.captures["init"] <= t
    }
    if not tables:
        base = jump_table(image, t)
        return PlayerCode(image) if base is None else PlayerCode(image, base, data)
    base = max(tables)
    while base - 3 in tables:
        base -= 3
    return PlayerCode(image, base, data)


def play_entry(image, code: PlayerCode) -> frozenset:
    """The entries of ``code``'s ``mt_play``: the jump table's play entries
    (:func:`~pysidtracker.codescan.play_entries`), or, where its play jump leads
    out of the player's code into a wrapper, the first call that wrapper makes
    back into it."""
    entries = play_entries(image, code.start)
    out = [e for e in entries if not code.start <= e < code.end]
    if not out:
        return entries
    mem, pc = image.mem, out[0]
    while pc < len(mem) - 2 and mem[pc] not in (OP_JSR, OP_JMP, *RETURN_OPS):
        pc += disasm_len(mem[pc])
    target = mem[pc + 1] | mem[pc + 2] << 8
    if mem[pc] in (OP_JSR, OP_JMP) and code.start + _TABLE <= target < code.end:
        return frozenset({target})
    return entries


# mt_init's clear of two channel-variable blocks: ``txa; ldx #n; sta base,x; dex; bpl``.
_CLEAR = re.compile(rb"\x8a\xa2(.)\x9d..\xca\x10\xfa", re.S)
_CLEARED = 2 * C.VOICE_REG_SIZE


def channel_count(mem, pcs) -> int:
    """The channels a player sequences: its init, run at ``pcs``, clears two
    variable blocks of them (the editor's three where no one clear runs)."""
    sizes = {m[1][0] + 1 for pc in pcs if (m := _CLEAR.match(bytes(mem[pc : pc + 9])))}
    if len(sizes) != 1 or (size := sizes.pop()) % _CLEARED:
        return C.MAX_CHN
    return size // _CLEARED


def stride(channels: int) -> int:
    """Orderlist pointers a song: greloc lays three, which a player sequencing
    fewer leaves unread, a stereo packer one a channel."""
    return max(channels, C.MAX_CHN)


# ---- packed bytes <-> meaning ------------------------------------------------


def _tempo(cmd, packing):
    """greloc writes tempo ``t >= 3`` as ``t - 1``, the packed player's countdown."""
    if not isinstance(cmd, SetTempo) or cmd.tempo < (3 if packing else 2):
        return cmd
    return replace(cmd, tempo=cmd.tempo + (-1 if packing else 1))


def _command(fx, data):
    num = ord(fx) & 0x0F
    return _tempo(command_of(num, ord(data[0]) if data else 0), False)


@v_args(inline=True)
class _Stream(Transformer):
    """``streams.lark``'s streams as typed rows and orderlist entries."""

    # pylint: disable=missing-function-docstring

    def rest_run(self, byte):
        return Rest(), None, 0x100 - ord(byte)

    def note_only(self, byte):
        return note_of(ord(byte)), None, 1

    note_only_i = note_only

    def fx_note(self, fx, *rest):
        *data, note = rest
        return note_of(ord(note)), _command(fx, data), 1

    fx_note_i = fx_note

    def fx_only(self, fx, *data):
        return Rest(), _command(fx, data), 1

    def first_row(self, *kids):
        note, cmd, length = kids[-1]
        return Row(note, ord(kids[0]) if len(kids) == 2 else None, cmd, length)

    row = first_row

    def pattern(self, *kids):
        if isinstance(kids[-1], Row):
            return Parsed(kids, None)
        return Parsed(tuple(kids[:-1]), kids[-1].end_pos)

    def orderlist(self, *tokens):
        body, restart = tokens, None
        if len(tokens) > 1 and tokens[-2].type == "LOOPSONG":
            *body, _, restart = tokens
        entries, k = [], 0
        while k < len(body):
            nxt = body[k + 1] if k + 1 < len(body) else None
            if body[k].type == "PATTERN" and nxt is not None and nxt.type == "REPEAT":
                entries += [entry_from_byte(ord(nxt)), entry_from_byte(ord(body[k]))]
                k += 2
                continue
            entries.append(entry_from_byte(ord(body[k])))
            k += 1
        if restart is None:
            return Parsed(Orderlist(entries, 0), None)
        return Parsed(Orderlist(entries, ord(restart)), restart.end_pos)


STREAMS = ByteGrammar(
    __package__, "streams.lark", ("pattern", "orderlist"), _Stream(), SidParseError
)


def stream_trees(mem, lay) -> list:
    """``(name, rule, bytes)`` of every packed stream ``lay`` points at in ``mem``,
    each its stored bytes (:func:`packed_orderlist`, :func:`pattern_rows`)."""
    out = [(f"orderlist {k}", "orderlist", a) for k, a in enumerate(lay.order_addrs)]
    out += [(f"pattern {k}", "pattern", a) for k, a in enumerate(lay.patt_addrs)]
    read = {"orderlist": packed_orderlist, "pattern": pattern_rows}
    return [(n, rule, bytes(mem[a : read[rule](mem, a)[1]])) for n, rule, a in out]


def _reach(mem, pos, rule, cut=0):
    window = bytes(mem[pos : pos + REACH - cut])
    model, size = STREAMS.parse_prefix(window, rule)
    return model, pos + (len(window) if size is None else size)


def pattern_rows(mem, pos):
    """One packed pattern at ``pos``: typed rows, and the address past its end mark
    (or the cursor's reach).

    A last row the cursor's reach cuts short wraps the cursor into the pattern's
    own start, which no pattern of rows states: the rows end before it.
    """
    for cut in range(_ROW_MAX - 1):
        try:
            return _reach(mem, pos, "pattern", cut)
        except SidParseError:
            pass
    return _reach(mem, pos, "pattern", _ROW_MAX - 1)


def packed_orderlist(mem, pos):
    """One packed orderlist at ``pos`` (a repeat trails the pattern it repeats), and
    the address past its restart byte (or the cursor's reach)."""
    return _reach(mem, pos, "orderlist")


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
    return bytes(out) if len(out) >= REACH else bytes(out + b"\0")


# How a player reads a wavetable's left bytes: ``WAVE_NEW`` delays $01-$0F and
# shifts waveforms up $10, ``WAVE_NONE`` has no delays, ``WAVE_OLD`` (before v2.18)
# delays $01-$07 and takes the rest as waveforms.
WAVE_NEW, WAVE_NONE, WAVE_OLD = 0, 1, 2
_OLD_DELAYS = 0x07


def _wave_editor(left, right, wave):
    """A packed wavetable row in editor form: waveforms $10 up, silent ones by nibble."""
    if left == C.TABLEJUMP or left >= C.WAVECMD:
        return left, right
    if wave == WAVE_OLD:
        silent = _OLD_DELAYS < left <= C.WAVELASTDELAY
        return (C.WAVESILENT | left if silent else left), right ^ 0x80
    if wave:
        return (
            C.WAVESILENT | left if C.WAVEDELAY <= left <= C.WAVELASTDELAY else left
        ), right ^ 0x80
    if left <= C.WAVELASTDELAY:
        return left, right ^ 0x80
    w = left - 0x10
    return (C.WAVESILENT | w if w <= C.WAVELASTDELAY else w), right ^ 0x80


def _wave_packed(left, right, wave):
    if left == C.TABLEJUMP or left >= C.WAVECMD:
        return left, right
    w = left & 0x0F if left >= C.WAVESILENT else left
    return (
        w + 0x10 if left > C.WAVELASTDELAY and wave == WAVE_NEW else w
    ), right ^ 0x80


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


def _instrument(i, col, build, fixed, lay):
    """Instrument ``i`` from its packed columns: greloc's ``vibdelay - 1`` undone (the
    binary relocator's players count the delay as stored), FIXEDPARAMS' immediates,
    the hard restart and legato classes from the gate column's own bits where it keeps
    them, else from the build's class bounds."""
    gate, first = (col["gate"], col["first"]) if "gate" in col else fixed
    flags = lay.gate_flags
    return Instrument(
        col["ad"],
        col["sr"],
        col["wave"],
        col.get("pulse", 0),
        col.get("filt", 0),
        col.get("vib", 0),
        (col.get("vibdelay", 0xFF) + (not lay.binary)) & 0xFF,
        gate & 0x3F,
        not gate & 0x80 if flags else i < (build.firstnohr or NONE),
        bool(gate & 0x40) if flags else i >= (build.firstlegato or NONE),
        {0: None, 0xFE: GateOff(), 0xFF: GateOn()}.get(first, SetWave(first)),
        f"INSTR{i:02X}",
    )


def _swapped(byte):
    return (byte >> 4 | byte << 4) & 0xFF


def _speeds(instruments, patterns):
    """``(instruments, patterns, speedtable)`` of a player without a speed table,
    its vibrato, portamento and funktempo parameters made the speedtable rows
    GoatTracker 2.76 makes of them on loading such a song (``legacy.parse_gts2``),
    undoing the nibble swap and funktempo bias greloc applied."""
    table = Table()

    def command(cmd):
        match cmd:
            case PortamentoUp(x) | PortamentoDown(x) | TonePortamento(x):
                return replace(cmd, speed=make_speed_entry(table, x, MST_PORTAMENTO))
            case Vibrato(x):
                return Vibrato(make_speed_entry(table, _swapped(x), MST_FINEVIB))
            case FunkTempo(x):
                return FunkTempo(
                    make_speed_entry(table, (x + 0x11) & 0xFF, MST_FUNKTEMPO)
                )
        return cmd

    instruments = tuple(
        replace(
            i,
            vibrato_speed=make_speed_entry(
                table, _swapped(i.vibrato_speed), MST_FINEVIB
            ),
        )
        for i in instruments
    )
    patterns = tuple(
        tuple(replace(r, command=command(r.command)) if r.command else r for r in p)
        for p in patterns
    )
    return (
        instruments,
        patterns,
        tuple(speed_row(*r) for r in zip(table.left, table.right)),
    )


def decode(mem, lay, build, header, orderlists, wave, simplepulse) -> TypedSong:
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
            lay,
        )
        for i in range(n)
    )

    def table(start, length, f):
        return tuple(f(mem[start + k], mem[start + length + k]) for k in range(length))

    ppos = lay.tstart + 2 * lay.wlen
    fpos = ppos + 2 * lay.plen
    spos = fpos + 2 * lay.flen + lay.zeros
    limit = C.MAX_STR - 1
    patterns = tuple(pattern_rows(mem, a)[0] for a in lay.patt_addrs)
    speeds = tuple(
        speed_row(mem[spos + k], mem[spos + lay.slen + lay.zeros + k])
        for k in range(lay.slen)
    )
    if lay.inline:
        instruments, patterns, speeds = _speeds(instruments, patterns)
    return TypedSong(
        name=header.name[:limit],
        author=header.author[:limit],
        copyright=header.released[:limit],
        subtunes=tuple(orderlists),
        patterns=patterns,
        instruments=instruments,
        wavetable=table(
            lay.tstart,
            lay.wlen,
            lambda a, b: wave_row(*_wave_editor(a, b, wave)),
        ),
        pulsetable=table(ppos, lay.plen, lambda a, b: pulse_row(a, b, simplepulse)),
        filtertable=table(fpos, lay.flen, _filter_typed),
        speedtable=speeds,
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
    if len(out) >= REACH:
        return bytes(out)
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
        "vibdelay": (raw.vibrato_delay - (not lay.binary)) & 0xFF,
        "gate": raw.gateoff_timer & (0x3F | flags),
        "first": raw.first_wave,
    }
    return [col[k] for k in columns(lay)]


def pack(song: TypedSong, info) -> list[tuple[int, bytes]]:
    """Every byte of the song data, as ``(address, bytes)`` pieces of the image.

    ``info`` is the :class:`~pygoattracker.sid.PackedInfo` decompiling the image left.
    """
    lay, wave = info.layout, info.wave
    if lay.inline:
        raise ValueError("a player without a speed table cannot be packed")

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
            pairs(song.wavetable, lambda r: _wave_packed(*wave_bytes(r), wave)),
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
