"""A song packed as greloc packs it, for the spec player to play without a tune.

:func:`song_image` lays a song out as GoatTracker 2.76's relocator does (instruments
by class, packed patterns and orderlists, the four tables) behind a stub player, with
the defines a :class:`~pygoattracker.packed.Build` and a ``PlayerState`` state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from pygoattracker import constants as C
from pygoattracker.errors import GoatTrackerError
from pygoattracker.gt2spec import Match, layout
from pygoattracker.model import Pattern
from pygoattracker.packed import (
    WAVE_NEW,
    Build,
    PlayerState,
    _filter_packed,
    _wave_packed,
    orderlist_bytes,
    pattern_bytes,
)
from pygoattracker.typed import (
    TypedSong,
    WaveCommand,
    packed_rows,
    pulse_bytes,
    raw_rows,
    rows_of,
    speed_bytes,
    wave_bytes,
)

BASE = 0x1000
_CHIP = 0x20
_CHIPS = 3
_SLOT = 0x100
_NOTES = 0x80
ZP = 0xFC
_JMP, _JSR, _RTS, _STA, _LDA, _ASL, _ADC = 0x4C, 0x20, 0x60, 0x8D, 0xA9, 0x0A, 0x6D
_TICK0 = ("0", "12", "12", "34", "34", "5", "6", "7", "8", "9")
_TICK0 += ("a", "b", "c", "d", "e", "f")
_EFFECTS = ("0", "12", "12", "3", "4")
_BLOCKS = (
    ("songptr", "trans", "repeat", "pattptr", "packedrest", "newfx", "newparam"),
    ("fx", "param", "newnote", "waveptr", "wave", "pulseptr", "pulsetime"),
    ("songnum", "pattnum", "tempo", "counter", "note", "instr", "gate"),
    ("vibtime", "vibdelay", "wavetime", "freqlo", "freqhi", "pulselo", "pulsehi"),
    ("ad", "sr", "sfx", "sfxlo", "sfxhi", "gatetimer", "lastnote"),
)
_FILTER = ("filtstep", "filttime", "filtcutoff", "filtctrl", "filttype")
_OPERANDS = (
    "mt_initsongnum",
    "mt_masterfader",
    "mt_tick0jump1",
    "mt_tick0jump2",
    "mt_effectjump",
    "mt_effectnum",
    "mt_effect_4_speedcmp",
    "mt_cscount",
    "mt_csresty",
    "mt_execwavetick0jump",
)
_INSTRUMENT = (
    "mt_insad",
    "mt_inssr",
    "mt_inswaveptr",
    "mt_inspulseptr",
    "mt_insfiltptr",
    "mt_insvibparam",
    "mt_insvibdelay",
    "mt_insgatetimer",
    "mt_insfirstwave",
)
_GHOST = ("freqlo", "freqhi", "pulselo", "pulsehi", "ctrl", "ad", "sr")
_TABLES = (
    ("mt_wavetbl", "mt_notetbl"),
    ("mt_pulsetimetbl", "mt_pulsespdtbl"),
    ("mt_filttimetbl", "mt_filtspdtbl"),
    ("mt_speedlefttbl", "mt_speedrighttbl"),
)
# The full-featured build: every effect, table and command, the editor's skips.
DEFINES = {
    "NOEFFECTS": 0,
    "NOINSTRVIB": 0,
    "NOVIB": 0,
    "NOTONEPORTA": 0,
    "NOPORTAMENTO": 0,
    "NOCALCULATEDSPEED": 0,
    "NONORMALSPEED": 0,
    "NOZEROSPEED": 0,
    "NOWAVEDELAY": 0,
    "NOWAVECMD": 0,
    "NOPULSE": 0,
    "NOPULSEMOD": 0,
    "NOFILTER": 0,
    "NOFILTERMOD": 0,
    "NOFUNKTEMPO": 0,
    "NOCHANNELTEMPO": 0,
    "NOGLOBALTEMPO": 0,
    "NOTRANS": 0,
    "NOREPEAT": 0,
    "NOGATE": 0,
    "FIXEDPARAMS": 0,
    "NOFIRSTWAVECMD": 0,
    "NOAUTHORINFO": 0,
    "BUFFEREDWRITES": 0,
    "GHOSTREGS": 0,
    "ZPGHOSTREGS": 0,
    "SIMPLEPULSE": 0,
    "PULSEOPTIMIZATION": 1,
    "REALTIMEOPTIMIZATION": 1,
    "NODIRECTWAVE": 1,
    "NUMSONGS": 2,
    "FIRSTNOTE": 0,
    "SIDBASE": C.SID_BASE,
}


@dataclass(frozen=True)
class SongImage:
    """A packed song loading at :data:`BASE`: ``init`` takes the subtune, ``play``
    runs a frame's calls of ``entry`` (``mt_play``) until ``direct`` frames have
    played, then ``entry`` is the play; ``layout`` binds the spec, ``marks`` is
    ``{address: channel}`` of each orderlist's loop mark and ``ghost`` the
    first chip's ghost registers of a full-ghosting build."""

    data: bytes
    init: int
    play: int
    entry: int
    layout: dict
    marks: dict
    direct: int | None = None
    ghost: int | None = None


class _Memory:
    """Bytes laid out one piece after another from ``BASE``."""

    def __init__(self):
        self.data = bytearray()

    def put(self, data) -> int:
        """Append ``data``; its address."""
        at = BASE + len(self.data)
        self.data += bytes(data)
        return at

    def poke(self, addr: int, data) -> None:
        """Overwrite the bytes at ``addr``."""
        data = bytes(data)
        self.data[addr - BASE : addr - BASE + len(data)] = data

    def code(self, *ops) -> int:
        """Append 6502 ``(opcode, operand)`` pairs (an immediate ``lda`` a byte,
        else an address or None); their address."""
        out = bytearray()
        for op, arg in ops:
            out.append(op)
            if arg is not None:
                out += arg.to_bytes(1 if op == _LDA else 2, "little")
        return self.put(out)


def _rank(ins) -> int:
    """greloc's instrument class: 0 hard restart, 1 none, 2 legato."""
    return 2 if ins.legato else 0 if ins.hard_restart else 1


def _ordered(song: TypedSong):
    """``(song, counts)``: ``song`` with its instruments in greloc's class order,
    and how many of each class."""
    order = sorted(
        range(len(song.instruments)), key=lambda k: _rank(song.instruments[k])
    )
    new = {old + 1: n + 1 for n, old in enumerate(order)}
    patterns = tuple(
        tuple(
            (
                replace(r, instrument=new.get(r.instrument, r.instrument))
                if r.instrument
                else r
            )
            for r in p
        )
        for p in song.patterns
    )
    ranks = [_rank(i) for i in song.instruments]
    song = replace(
        song,
        instruments=tuple(song.instruments[k] for k in order),
        patterns=patterns,
    )
    return song, [ranks.count(k) for k in range(3)]


def _columns(song: TypedSong, build: Build) -> list:
    """The nine instrument columns as greloc stores them: no vibrato delay, no
    vibrato; else the delay less one (as is where a zero delay stops it); the gate
    timer's no-hard-restart flag kept where it runs the wavetable at note init."""
    rows = []
    gate = 0xBF if build.nohr_wave else 0x3F
    for ins in song.instruments:
        raw = ins.raw()
        vib = raw.vibrato_delay != 0
        delay = raw.vibrato_delay if build.vibstop else max(raw.vibrato_delay - 1, 0)
        rows.append(
            (
                raw.attack_decay,
                raw.sustain_release,
                raw.wave_ptr,
                raw.pulse_ptr,
                raw.filter_ptr,
                raw.vibrato_param if vib else 0,
                delay,
                raw.gateoff_timer & gate,
                raw.first_wave,
            )
        )
    return [bytes(r[j] for r in rows) for j in range(len(_INSTRUMENT))]


def _pairs(rows, f) -> tuple:
    pairs = [f(r) for r in rows]
    return bytes(a for a, _ in pairs), bytes(b for _, b in pairs)


def _deftempo(song: TypedSong) -> int:
    """greloc's DEFAULTTEMPO: instrument 63's attack/decay less one where it
    starts no wavetable, else 5."""
    if len(song.instruments) >= C.MAX_INSTR - 1:
        last = song.instruments[C.MAX_INSTR - 2]
        if last.attack_decay >= 2 and not last.wave_ptr:
            return last.attack_decay - 1
    return 5


def _writes(regs) -> tuple:
    """6502 ops storing each ``(register, value)`` to the chip."""
    return tuple(
        op
        for reg, value in regs
        for op in ((_LDA, value & 0xFF), (_STA, C.SID_BASE + reg))
    )


def _patch(call: int, target: int) -> tuple:
    """6502 ops pointing the ``jmp`` at ``call`` to ``target``."""
    return (
        (_LDA, target & 0xFF),
        (_STA, call + 1),
        (_LDA, target >> 8),
        (_STA, call + 2),
    )


def _held(state, build: Build, voices) -> tuple:
    """The ``(register, value)`` writes a chip-writing build's first tick outputs
    before its channels run: each envelope ``state`` holds, and the frequency and
    pulse each played channel writes from its variables (zero with no ``state``)."""
    if state is not None and state.ghost:
        return ()
    regs = state.registers if state else (0,) * C.SID_REGISTERS
    touched = {reg for reg, _ in build.init_regs}
    out = []
    for c, off in enumerate(voices):
        if off is None or off >= _CHIP:
            continue
        envelope = (C.AD_REG, C.SR_REG) if state else ()
        sound = () if c in build.idle else range(C.CONTROL_REG)
        out += [
            (off + reg, regs[off + reg])
            for reg in (*envelope, *sound)
            if state or off + reg in touched
        ]
    return tuple(out)


def _operands(mem: _Memory, sym: dict) -> None:
    """The operand cells (each chip's filter, then the rest) and ``mt_play``."""
    for chip in range(_CHIPS):
        for name in _FILTER:
            at = mem.put(bytes(2)) - 1
            if chip < 2:
                sym["mt_" + name + ("_sid2" if chip else "")] = at
    for name in _OPERANDS:
        sym[name] = mem.put(bytes(2)) - 1
    mem.poke(sym["mt_masterfader"] + 1, b"\x0f")
    sym["mt_play"] = mem.put(bytes((_RTS,)))


def _stub(mem, sym, channels, build, state, held) -> tuple:
    """An init storing the song's first orderlist index (song * 3, * 6 for two
    chips) and making the tune's init writes, and a play running a frame's calls,
    the first followed by ``state``'s song-init writes and the second preceded by
    the ``held`` writes; ``(init, play)``."""
    entry = sym["mt_play"]
    tmp = mem.put(b"\0")
    double = ((_ASL, None),) if channels > C.MAX_CHN else ()
    init = mem.code(
        (_STA, tmp),
        (_ASL, None),
        (_ADC, tmp),
        *double,
        (_STA, sym["mt_initsongnum"] + 1),
        *_writes(build.init_regs),
        (_RTS, None),
    )
    after = state.init if state is not None else ()
    if build.calls == 1 and not after and not held:
        return init, entry
    call = mem.code((_JMP, entry))
    play = mem.code(*((_JSR, call),) * build.calls, (_RTS, None))
    second = mem.code(*_writes(held), *_patch(call, entry), (_JMP, entry))
    first = mem.code(
        (_JSR, entry), *_writes(after), *_patch(call, second), (_RTS, None)
    )
    mem.poke(call + 1, first.to_bytes(2, "little"))
    return init, play


def _variables(mem: _Memory, sym: dict, channels: int) -> None:
    """The channel variables, as player.s's normal layout initialises them, and
    the ghost registers a full-ghosting build's first chip writes."""
    for k, names in enumerate(_BLOCKS):
        start = mem.put(bytes(7 * channels))
        sym.update(("mt_chn" + name, start + j) for j, name in enumerate(names))
        if k == 2:
            for c in range(channels):
                mem.poke(start + 7 * c, (c, 0, 0, 0, 0, 1, 0xFE))
    start = mem.put(bytes(C.SID_REGISTERS))
    sym.update(("ghost" + name, start + j) for j, name in enumerate(_GHOST))


def _dispatch(mem: _Memory, sym: dict) -> None:
    """Handler labels a byte apart and the jump tables naming them."""
    for k in dict.fromkeys(_TICK0):
        sym[f"mt_tick0_{k}"] = mem.put(b"\0")
    for k in ("0", "4", "3", "12"):
        sym[f"mt_effect_{k}"] = mem.put(b"\0")
    sym["mt_effect_0_delay"] = sym["mt_effect_0"]
    sym["mt_tick0jumptbl"] = mem.put(sym[f"mt_tick0_{k}"] & 0xFF for k in _TICK0)
    sym["mt_effectjumptbl"] = mem.put(sym[f"mt_effect_{k}"] & 0xFF for k in _EFFECTS)
    sym["mt_funktempotbl"] = mem.put(b"\x08\x05")
    sym["mt_author"] = mem.put(bytes(32))


def _padded(data, size: int) -> bytes:
    """``data`` zero-filled to ``size`` bytes."""
    data = bytes(data)
    if len(data) > size:
        raise GoatTrackerError(f"{len(data)} bytes do not fit {size}")
    return data + bytes(size - len(data))


def _data(mem: _Memory, sym: dict, song: TypedSong, build: Build, freq) -> dict:
    """Instruments, tables and the frequency table in fixed slots, the song and
    pattern tables, then the orderlists and patterns; the address of each
    orderlist's loop mark, and its channel."""
    for name, col in zip(_INSTRUMENT, _columns(song, build)):
        sym[name] = mem.put(_padded(col, C.MAX_INSTR))
    tables = (
        _pairs(song.wavetable, lambda r: _wave_packed(*wave_bytes(r), WAVE_NEW)),
        _pairs(song.pulsetable, pulse_bytes),
        _pairs(song.filtertable, _filter_packed),
        _pairs(song.speedtable, speed_bytes),
    )
    for (left_name, right_name), (left, right) in zip(_TABLES, tables):
        sym[left_name] = mem.put(_padded(b"\0" + left, _SLOT)) + 1
        sym[right_name] = mem.put(_padded(b"\0" + right, _SLOT)) + 1
    sym["mt_freqtbllo"] = mem.put(_padded((f & 0xFF for f in freq), _NOTES))
    sym["mt_freqtblhi"] = mem.put(_padded((f >> 8 & 0xFF for f in freq), _NOTES))
    for name in ("mt_songtbllo", "mt_songtblhi", "mt_patttbllo", "mt_patttblhi"):
        sym[name] = mem.put(bytes(_SLOT))
    orders = [o for s in song.subtunes for o in s.channels]
    order_addrs = [mem.put(orderlist_bytes(o)) for o in orders]
    patt_addrs = []
    for p in song.patterns:
        rows = packed_rows(rows_of(Pattern(raw_rows(p))))
        if build.live_vibrato:
            rows = tuple(replace(r, command=None) for r in rows)
        patt_addrs.append(mem.put(pattern_bytes(rows)))
    for name, addrs in (("mt_songtbl", order_addrs), ("mt_patttbl", patt_addrs)):
        mem.poke(sym[name + "lo"], _padded((a & 0xFF for a in addrs), _SLOT))
        mem.poke(sym[name + "hi"], _padded((a >> 8 for a in addrs), _SLOT))
    return {
        a + len(orderlist_bytes(o)) - 2: k % len(song.subtunes[0].channels)
        for k, (a, o) in enumerate(zip(order_addrs, orders))
    }


def _seed(mem: _Memory, sym: dict, state: PlayerState, voices, build: Build) -> None:
    """The variables ``state`` starts from, which the song's init keeps."""
    regs = state.registers
    home = [(c, off) for c, off in enumerate(voices) if off is not None and off < 0x20]
    mem.poke(sym["ghostfreqlo"], bytes(r & 0xFF for r in regs))
    for c, off in home:
        for k, name in enumerate(_GHOST[:4]):
            mem.poke(sym["mt_chn" + name] + 7 * c, (regs[off + k] & 0xFF,))
    mem.poke(sym["mt_funktempotbl"], bytes((v + 1) & 0xFF for v in state.funk))
    mem.poke(sym["mt_masterfader"] + 1, (state.volume & 0x0F,))
    chip = home[0][0] // C.MAX_CHN if home else 0
    for name, value in (("cutoff", state.cutoff), ("type", state.filttype)):
        at = sym["mt_filt" + name] + chip * 2 * len(_FILTER) + 1
        mem.poke(at, (value & 0xFF,))
    for name, values in state.channels:
        for c, value in enumerate(values):
            at = sym["mt_chnpulselo" if name == "pulse_init" else "mt_chn" + name]
            if name == "vibdelay" and not build.vibstop:
                value = max(value - 1, 0)
            if name == "pulse_init":
                mem.poke(sym["mt_chnpulsehi"] + 7 * c, (value >> 8 & 0xFF,))
            mem.poke(at + 7 * c, (value & 0xFF,))


def song_image(
    song,
    build: Build | None = None,
    state: PlayerState | None = None,
    freq_table=None,
) -> SongImage:
    """``song`` packed behind a stub player as ``build`` plays it, from ``state``,
    with the 128 note frequencies ``freq_table`` (None: the editor's)."""
    build = build or Build()
    typed = song if isinstance(song, TypedSong) else TypedSong.from_song(song)
    deftempo = _deftempo(typed)
    typed, counts = _ordered(typed)
    channels = len(typed.subtunes[0].channels) if typed.subtunes else C.MAX_CHN
    voices = build.voices or tuple(
        c // C.MAX_CHN * 0x20 + c % C.MAX_CHN * C.VOICE_REG_SIZE
        for c in range(channels)
    )
    ghost = state is not None and state.ghost
    mem = _Memory()
    sym = dict(DEFINES)
    _operands(mem, sym)
    _variables(mem, sym, channels)
    _dispatch(mem, sym)
    freq = C.FREQ_TABLE if freq_table is None else tuple(freq_table)[:_NOTES]
    marks = _data(mem, sym, typed, build, freq)
    entry = sym["mt_play"]
    init, play = _stub(mem, sym, channels, build, state, _held(state, build, voices))
    if state is not None:
        _seed(mem, sym, state, voices, build)
    adparam = build.adparam & 0xFFFF
    wavecmd = any(isinstance(r, WaveCommand) for r in typed.wavetable)
    sym.update(
        mt_temp1=ZP,
        NUMCHANNELS=channels,
        DEFAULTTEMPO=deftempo if build.default_tempo is None else build.default_tempo,
        ADPARAM=adparam >> 8,
        SRPARAM=adparam & 0xFF,
        NUMHRINSTR=counts[0],
        NUMNOHRINSTR=counts[1],
        NUMLEGATOINSTR=counts[2],
        FIRSTNOHRINSTR=counts[0] + 1,
        FIRSTLEGATOINSTR=counts[0] + counts[1] + 1,
        PULSEOPTIMIZATION=int(build.optimize_pulse),
        REALTIMEOPTIMIZATION=int(build.optimize_realtime),
        SIMPLEPULSE=int(build.simplepulse),
        NOEFFECTS=int(build.live_vibrato),
        NODIRECTWAVE=int(not build.nohr_wave),
        NOWAVECMD=int(not wavecmd),
    )
    if channels > C.MAX_CHN:
        sym["SID2BASE"] = C.SID_BASE + 0x20
    name = "gt2stereo_277/player" if channels > C.MAX_CHN else "gt2_276/player"
    passed = {"mt_effectnum"} if wavecmd else set()
    found = Match(name, BASE, BASE + len(mem.data), sym, {}, frozenset(passed))
    played = range(len(voices))
    lay = layout(found, [c for c in played if c not in set(build.idle)], True)
    lay.update(
        vibstop=build.vibstop,
        novibinit=not build.vibinit,
        pulse2x=build.pulse_step == 2,
    )
    direct = 2 if build.calls == 1 and play != entry else None
    ghost = sym["ghostfreqlo"] if ghost else None
    return SongImage(bytes(mem.data), init, play, entry, lay, marks, direct, ghost)
