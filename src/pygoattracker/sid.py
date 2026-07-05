"""Recover GoatTracker 2 songs from packed ``.sid`` files.

A ``.sid`` file (PSID/RSID) wraps a 6502 program: the GoatTracker
*packed* playroutine (produced by ``gt2reloc``) followed by the song
data. This module locates that data in the loaded image and decompiles
it back into a :class:`~pygoattracker.model.Song`.

The packed data layout and byte transforms follow GoatTracker 2.76's
``greloc.c`` (packer) and ``player.s`` (relocatable playroutine):

* frequency table, song(order)-table and pattern-table pointer arrays,
  then per-field instrument arrays, then the four wave/pulse/filter/speed
  tables, then the orderlists and packed patterns;
* patterns are stored in the packed player grammar (instrument/FX/note/
  packed-rest bytes), not the .SNG per-row form;
* wave/pulse/filter columns are transformed for the packed player and are
  reversed here to the editor form the :class:`~pygoattracker.model.Song`
  (and :class:`~pygoattracker.player.Player`) expect.

Images that are crunched or relocated on init are unpacked first by
running the init routine in a 6502 emulator (:mod:`py65`, optional).
"""

import io
from dataclasses import dataclass
from pathlib import Path

from pygoattracker import constants
from pygoattracker.errors import SidParseError
from pygoattracker.model import (
    Instrument,
    Orderlist,
    Pattern,
    PlayPattern,
    Repeat,
    Row,
    Song,
    Subtune,
    Table,
    Transpose,
)

# Packed pattern grammar (player.s / gcommon.h).
_FX = 0x40
_FXONLY = 0x50
_PNOTE = 0x60
_FIRSTPACKEDREST = 0xC0
_PENDMARK = 0x00

# Packed orderlist grammar (shares .SNG encoding except repeats trail
# their pattern instead of prefixing it).
_REPEAT = 0xD0
_TRANSDOWN = 0xE0
_TRANSUP = 0xF0
_LOOPSONG = 0xFF


@dataclass
class SidHeader:
    """Parsed PSID/RSID header fields (see the HVSC SID format spec)."""

    magic: bytes
    version: int
    data_offset: int
    load_address: int
    init_address: int
    play_address: int
    songs: int
    start_song: int
    name: str
    author: str
    released: str
    flags: int


@dataclass
class PackedInfo:
    """Playroutine options recovered from the packed image.

    These let a caller build a :class:`~pygoattracker.player.Player` that
    reproduces the packed player exactly::

        song, info = decompile_sid(data).song, decompile_sid(data).info
        player = Player(song, freq_table=info.freq_table,
                        simplepulse=info.simplepulse,
                        live_vibrato=info.live_vibrato)
    """

    firstnote: int = 0
    lastnote: int = constants.MAX_NOTES - 1
    freq_table: tuple = ()
    simplepulse: bool = False
    live_vibrato: bool = False
    nowavedelay: bool = False


@dataclass
class DecompiledSid:
    """Result of decompiling a ``.sid``: the song and how to play it back."""

    song: Song
    info: PackedInfo
    header: SidHeader
    subtune: int = 0


def _u16be(data: bytes, off: int) -> int:
    return (data[off] << 8) | data[off + 1]


def _decode_str(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin-1")


def parse_sid_header(raw: bytes) -> SidHeader:
    """Parse a PSID/RSID header from ``raw`` file bytes."""
    magic = bytes(raw[:4])
    if magic not in (b"PSID", b"RSID"):
        raise SidParseError(f"not a PSID/RSID file (identifier {magic!r})")
    version = _u16be(raw, 4)
    data_offset = _u16be(raw, 6)
    return SidHeader(
        magic=magic,
        version=version,
        data_offset=data_offset,
        load_address=_u16be(raw, 8),
        init_address=_u16be(raw, 10),
        play_address=_u16be(raw, 12),
        songs=_u16be(raw, 14),
        start_song=_u16be(raw, 16),
        name=_decode_str(raw[0x16:0x36]),
        author=_decode_str(raw[0x36:0x56]),
        released=_decode_str(raw[0x56:0x76]),
        flags=_u16be(raw, 0x76) if version >= 2 else 0,
    )


def load_sid(raw: bytes):
    """Load a ``.sid`` into a 64 KiB image.

    Returns ``(mem, header, load_address, end_address)`` where ``mem`` is a
    ``bytearray`` of length ``0x10000`` with the C64 data placed at its load
    address.
    """
    header = parse_sid_header(raw)
    data = raw[header.data_offset :]
    load = header.load_address
    if load == 0:
        if len(data) < 2:
            raise SidParseError("truncated: no in-file load address")
        load = data[0] | (data[1] << 8)
        data = data[2:]
    end = load + len(data)
    if end > 0x10000:
        raise SidParseError(
            f"data overruns memory (load {load:#06x}, {len(data)} bytes)"
        )
    mem = bytearray(0x10000)
    mem[load:end] = data
    return mem, header, load, end


def _find_freq_anchor(mem, lo, hi):
    """Locate ``mt_freqtbllo`` in ``mem``.

    greloc emits ``freqtbllo[firstnote..lastnote]`` immediately followed by
    ``freqtblhi[firstnote..lastnote]`` (a contiguous slice of the known GT
    tables). Returns ``(addr, firstnote, length)`` for the longest match, or
    ``None``.
    """
    best = None
    n = len(lo)
    for addr in range(0, 0x10000 - 8):
        first = mem[addr]
        for fn in range(n):
            if lo[fn] != first:
                continue
            length = 0
            while (
                fn + length < n
                and addr + length < 0x10000
                and mem[addr + length] == lo[fn + length]
            ):
                length += 1
            if length < 8:
                continue
            if addr + 2 * length > 0x10000:
                continue
            if all(mem[addr + length + i] == hi[fn + i] for i in range(length)):
                if best is None or length > best[2]:
                    best = (addr, fn, length)
    return best


def decode_packed_pattern(mem, addr):
    """Decode one packed pattern at ``addr`` into a list of :class:`Row`.

    Mirrors the packpattern grammar in ``greloc.c``: an optional instrument
    byte (< ``FX``), then an FX/FXONLY byte (updating the running command),
    then a note byte or a packed-rest run; ``0x00`` ends the pattern. The
    active command persists across rows until changed, so it is filled onto
    every reconstructed row.
    """
    pos = addr
    command = 0
    data = 0
    rows = []
    while True:
        byte = mem[pos]
        if byte == _PENDMARK:
            pos += 1
            break
        instrument = 0
        if byte < _FX:
            instrument = byte
            pos += 1
            byte = mem[pos]
        if byte >= _FIRSTPACKEDREST:
            run = 0x100 - byte
            pos += 1
            for _ in range(run):
                rows.append(
                    Row(note=constants.REST, instrument=0, command=command, data=data)
                )
            continue
        if byte < _PNOTE:
            command = byte & 0x0F
            is_rest = byte >= _FXONLY
            pos += 1
            if command:
                data = mem[pos]
                pos += 1
            if is_rest:
                rows.append(
                    Row(
                        note=constants.REST,
                        instrument=instrument,
                        command=command,
                        data=data,
                    )
                )
                continue
            note = mem[pos]
            pos += 1
        else:
            note = byte
            pos += 1
        rows.append(Row(note=note, instrument=instrument, command=command, data=data))
    return rows, pos


def decode_packed_orderlist(mem, addr):
    """Decode a packed orderlist at ``addr`` into an :class:`Orderlist`.

    Packed orderlists use the .SNG entry encoding except that a repeat
    (``0xD0``-``0xDF``) trails the pattern it repeats instead of prefixing
    it. Ends at ``0xFF`` followed by a restart-position byte.
    """
    entries = []
    pos = addr
    while mem[pos] != _LOOPSONG:
        byte = mem[pos]
        if byte < _REPEAT:
            nxt = mem[pos + 1]
            if _REPEAT <= nxt < _TRANSDOWN:
                entries.append(Repeat(nxt - _REPEAT))
                entries.append(PlayPattern(byte))
                pos += 2
            else:
                entries.append(PlayPattern(byte))
                pos += 1
        elif byte < _TRANSDOWN:
            # Bare repeat with no preceding pattern: keep for fidelity.
            entries.append(Repeat(byte - _REPEAT))
            pos += 1
        else:
            entries.append(Transpose(byte - _TRANSUP))
            pos += 1
    restart = mem[pos + 1]
    return Orderlist(entries=entries, restart=restart), pos + 2


# ---------------------------------------------------------------------------
# Instrument/table segmentation and reverse transforms.
#
# The packed instrument arrays and the four tables sit contiguously between
# the pattern-pointer table and the first orderlist, with no length markers.
# The layout is a deterministic function of greloc's build flags, which are
# baked into the player *code* (as assembler defines), not the song data. We
# recover the layout by trying every flag combination and keeping the one
# whose instrument arrays + tables tile the region exactly, with every
# instrument table pointer in range and the (directly indexed) speed table
# length equal to the highest speed pointer used.
# ---------------------------------------------------------------------------

# Pattern command numbers that index a table (its data byte is an entry point).
_CMD_SETWAVEPTR = 0x08
_CMD_SETPULSEPTR = 0x09
_CMD_SETFILTERPTR = 0x0A
_SPEED_CMDS = (0x01, 0x02, 0x03, 0x04, 0x0E)  # porta/toneporta/vibrato/funktempo


def _pattern_features(patterns):
    """Collect max instrument and per-table entry points from patterns."""
    maxinstr = 1
    wave, pulse, filt, speed = set(), set(), set(), set()
    for rows in patterns:
        for row in rows:
            if row.instrument > maxinstr:
                maxinstr = row.instrument
            cmd, data = row.command, row.data
            if not data:
                continue
            if cmd == _CMD_SETWAVEPTR:
                wave.add(data)
            elif cmd == _CMD_SETPULSEPTR:
                pulse.add(data)
            elif cmd == _CMD_SETFILTERPTR:
                filt.add(data)
            elif cmd in _SPEED_CMDS:
                speed.add(data)
    return maxinstr, wave, pulse, filt, speed


def _executed_length(mem, start, entry_points, limit=constants.MAX_TABLELEN):
    """Highest 1-based row reached by executing a jump-terminated table.

    wave/pulse/filter programs advance row-by-row until a ``0xFF`` jump row;
    the physical table is the union of all program spans. Returns
    ``(length, ok)`` where ``ok`` is False if execution ran off the rails
    (signals a wrong segmentation hypothesis).
    """
    entry_points = {e for e in entry_points if e}  # 0 = unused pointer
    if not entry_points:
        return 0, True
    reached = set()
    for entry in entry_points:
        ptr = entry
        for _ in range(limit + 1):
            if ptr < 1 or ptr > limit:
                return 0, False
            reached.add(ptr)
            if mem[start + ptr - 1] == 0xFF:  # jump row ends the program
                break
            ptr += 1
        else:
            return 0, False
    return max(reached), True


def _solve_layout(mem, region_start, region_end, num_instr, eps):
    """Return the unique valid (flags, table lengths) tiling of the region."""
    region_len = region_end - region_start
    solutions = []
    for pulse in (0, 1):
        for filt in (0, 1):
            for vib in (0, 1):
                for gate in (0, 1):
                    sol = _try_layout(
                        mem,
                        region_start,
                        region_end,
                        region_len,
                        num_instr,
                        pulse,
                        filt,
                        vib,
                        gate,
                        eps,
                    )
                    if sol is not None:
                        solutions.append(sol)
    if not solutions:
        raise SidParseError("could not segment packed instruments/tables")
    # vib-vs-gate can be genuinely ambiguous (same K, same tiling); greloc
    # would suppress an all-identical, normal gatetimer/firstwave pair via the
    # FIXEDPARAMS optimization, so such a pair must be the vibrato pair.
    solutions.sort(key=lambda s: s["gate"])
    return solutions[0]


def _try_layout(
    mem, region_start, region_end, region_len, num_instr, pulse, filt, vib, gate, eps
):
    wave_eps, pulse_eps, filt_eps, speed_eps = eps
    off = region_start
    arrays = {}
    for name in ("ad", "sr", "waveptr"):
        arrays[name] = mem[off : off + num_instr]
        off += num_instr
    if pulse:
        arrays["pulseptr"] = mem[off : off + num_instr]
        off += num_instr
    if filt:
        arrays["filtptr"] = mem[off : off + num_instr]
        off += num_instr
    if vib:
        arrays["vibparam"] = mem[off : off + num_instr]
        off += num_instr
        arrays["vibdelay"] = mem[off : off + num_instr]
        off += num_instr
    if gate:
        arrays["gatetimer"] = mem[off : off + num_instr]
        off += num_instr
        arrays["firstwave"] = mem[off : off + num_instr]
        off += num_instr
    tstart = off
    if tstart >= region_end:
        return None
    wlen, ok = _executed_length(mem, tstart, set(arrays["waveptr"]) | wave_eps)
    if not ok or wlen == 0 or max(arrays["waveptr"], default=0) > wlen:
        return None
    ppos = tstart + 2 * wlen
    plen = 0
    if pulse:
        plen, ok = _executed_length(mem, ppos, set(arrays["pulseptr"]) | pulse_eps)
        if not ok or max(arrays["pulseptr"], default=0) > plen:
            return None
    fpos = ppos + 2 * plen
    flen = 0
    if filt:
        flen, ok = _executed_length(mem, fpos, set(arrays["filtptr"]) | filt_eps)
        if not ok or max(arrays["filtptr"], default=0) > flen:
            return None
    speed_refs = set(speed_eps)
    if vib:
        speed_refs |= set(arrays["vibparam"])
    speed_refs.discard(0)
    slen = max(speed_refs, default=0)
    # Speed table has a leading zero-pad byte per column when it is executed.
    for zeros in (0, 1):
        total = (
            (3 + pulse + filt + 2 * vib + 2 * gate) * num_instr
            + 2 * wlen
            + 2 * plen
            + 2 * flen
            + 2 * slen
            + 2 * zeros
        )
        if total == region_len:
            return {
                "arrays": arrays,
                "pulse": pulse,
                "filt": filt,
                "vib": vib,
                "gate": gate,
                "tstart": tstart,
                "wlen": wlen,
                "plen": plen,
                "flen": flen,
                "slen": slen,
                "zeros": zeros,
            }
    return None


# ---------------------------------------------------------------------------
# Reverse the packed table byte transforms to the editor form the model and
# the (editor) Player expect.
# ---------------------------------------------------------------------------


def _reverse_wave(left, right, nowavedelay):
    """Reverse greloc's packed wavetable to editor left/right columns."""
    out_left, out_right = [], []
    for lbyte, rbyte in zip(left, right):
        if lbyte == 0xFF or constants.WAVECMD <= lbyte <= constants.WAVELASTCMD:
            out_left.append(lbyte)
            out_right.append(rbyte)
            continue
        if nowavedelay:
            out_left.append((constants.WAVESILENT | lbyte) if lbyte <= 0x0F else lbyte)
        elif lbyte <= 0x0F:
            out_left.append(lbyte)
        elif lbyte <= 0x1F:
            out_left.append(constants.WAVESILENT | (lbyte & 0x0F))
        else:
            out_left.append(lbyte - 0x10)
        out_right.append(rbyte ^ 0x80)
    return out_left, out_right


def _reverse_filter(left, right):
    """Reverse greloc's filter passband-bit transform (left column)."""
    out_left = [
        (0x80 | ((b & 0x38) << 1)) if (b != 0xFF and b > 0x80) else b for b in left
    ]
    return out_left, list(right)


def _detect_nowavedelay(wave_left):
    """greloc clears NOWAVEDELAY when a wavetable delay row (0x01-0x0F) exists."""
    return not any(0x01 <= b <= 0x0F for b in wave_left)


def _detect_simplepulse(pulse_left):
    """SIMPLEPULSE forces set-pulse rows to a 0x80 marker; the un-optimized
    player keeps the original 0x81-0xFE left byte."""
    return not any(0x81 <= b <= 0xFE for b in pulse_left)


def _freq_table(mem, addr, firstnote, length):
    """128-entry note-frequency table read from the image, reproducing the
    packed player's out-of-range overruns into adjacent memory."""
    table = []
    for note in range(128):
        off = note - firstnote
        lo = mem[(addr + off) & 0xFFFF]
        hi = mem[(addr + length + off) & 0xFFFF]
        table.append((hi << 8) | lo)
    return tuple(table)


def _read_table(mem, start, length):
    return list(mem[start : start + length]), list(
        mem[start + length : start + 2 * length]
    )


def _build_song(
    mem, header, layout, addr, firstnote, length, orderlists, patterns, num_instr
):
    sol = layout
    arrays = sol["arrays"]
    tstart = sol["tstart"]
    wlen, plen, flen, slen, zeros = (
        sol["wlen"],
        sol["plen"],
        sol["flen"],
        sol["slen"],
        sol["zeros"],
    )
    wleft, wright = _read_table(mem, tstart, wlen)
    ppos = tstart + 2 * wlen
    pleft, pright = _read_table(mem, ppos, plen) if sol["pulse"] else ([], [])
    fpos = ppos + 2 * plen
    fleft, fright = _read_table(mem, fpos, flen) if sol["filt"] else ([], [])
    spos = fpos + 2 * flen + zeros
    sleft = list(mem[spos : spos + slen])
    sright = list(mem[spos + slen + zeros : spos + slen + zeros + slen])

    nowavedelay = _detect_nowavedelay(wleft)
    simplepulse = _detect_simplepulse(pleft)
    ewl, ewr = _reverse_wave(wleft, wright, nowavedelay)
    efl, efr = _reverse_filter(fleft, fright)

    limit = constants.MAX_STR - 1
    song = Song(
        name=header.name[:limit],
        author=header.author[:limit],
        copyright=header.released[:limit],
    )
    song.wavetable = Table(left=ewl, right=ewr)
    # SIMPLEPULSE pulse tables stay in packed form (the Player replays them via
    # its simplepulse path); un-optimized pulse tables are already editor form.
    song.pulsetable = Table(left=list(pleft), right=list(pright))
    song.filtertable = Table(left=efl, right=efr)
    song.speedtable = Table(left=sleft, right=sright)

    instruments = []
    for i in range(num_instr):
        instruments.append(
            Instrument(
                attack_decay=arrays["ad"][i],
                sustain_release=arrays["sr"][i],
                wave_ptr=arrays["waveptr"][i],
                pulse_ptr=arrays["pulseptr"][i] if sol["pulse"] else 0,
                filter_ptr=arrays["filtptr"][i] if sol["filt"] else 0,
                vibrato_param=arrays["vibparam"][i] if sol["vib"] else 0,
                vibrato_delay=arrays["vibdelay"][i] if sol["vib"] else 0,
                gateoff_timer=arrays["gatetimer"][i] if sol["gate"] else 2,
                first_wave=arrays["firstwave"][i] if sol["gate"] else 0x09,
                name=f"INSTR{i + 1:02X}",
            )
        )
    song.instruments = instruments

    songs = header.songs
    song.subtunes = [
        Subtune(channels=orderlists[s * 3 : s * 3 + 3]) for s in range(songs)
    ]
    song.patterns = [Pattern(rows=rows) for rows in patterns]

    info = PackedInfo(
        firstnote=firstnote,
        lastnote=firstnote + length - 1,
        freq_table=_freq_table(mem, addr, firstnote, length),
        simplepulse=simplepulse,
        nowavedelay=nowavedelay,
    )
    return song, info


def _run_init(mem, header, subtune, max_cycles=8_000_000):
    """Run a crunched/relocated image's init routine so data lands in place."""
    try:
        from py65.devices.mpu6502 import MPU
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise SidParseError(
            "py65 is required to unpack this .sid (crunched/relocated): "
            "pip install pygoattracker[sid]"
        ) from exc
    mpu = MPU(memory=mem)
    start_sp = mpu.sp
    mem[0x0100 + mpu.sp] = 0x00
    mpu.sp = (mpu.sp - 1) & 0xFF
    mem[0x0100 + mpu.sp] = 0x01
    mpu.sp = (mpu.sp - 1) & 0xFF
    mpu.a = subtune & 0xFF
    mpu.pc = header.init_address
    start_cycles = mpu.processorCycles
    while mpu.sp < start_sp:
        mpu.step()
        if mpu.processorCycles - start_cycles > max_cycles:
            break


def decompile_sid(src, subtune=0) -> DecompiledSid:
    """Decompile a packed GoatTracker ``.sid`` into a :class:`DecompiledSid`.

    ``src`` may be a path, ``bytes``, or a binary file object. Direct-load
    images are inverted statically; crunched or relocated images have their
    init routine run in a 6502 emulator first (requires :mod:`py65`).
    """
    raw = _read_bytes(src)
    mem, header, _, dataend = load_sid(raw)
    anchor = _find_freq_anchor(mem, constants.FREQ_LO, constants.FREQ_HI)
    if anchor is None:
        _run_init(mem, header, subtune)
        dataend = 0x10000
        anchor = _find_freq_anchor(mem, constants.FREQ_LO, constants.FREQ_HI)
    if anchor is None:
        raise SidParseError("no GoatTracker frequency table found (not a GT sid?)")
    try:
        return _decompile(mem, header, dataend, anchor, subtune)
    except (IndexError, ValueError) as exc:
        raise SidParseError(f"malformed packed GoatTracker data: {exc}") from exc


def _decompile(mem, header, dataend, anchor, subtune) -> DecompiledSid:
    addr, firstnote, length = anchor
    songs = header.songs
    if not 1 <= songs <= constants.MAX_SONGS:
        raise SidParseError(f"implausible subtune count {songs}")
    songtbl = addr + 2 * length
    order_addrs = [
        mem[songtbl + i] | (mem[songtbl + 3 * songs + i] << 8) for i in range(3 * songs)
    ]
    if any(not 0 < a < dataend for a in order_addrs):
        raise SidParseError("orderlist pointer out of range")
    patt_lo = songtbl + 6 * songs
    orderlists = []
    order_end = 0
    for base in order_addrs:
        olist, nxt = decode_packed_orderlist(mem, base)
        orderlists.append(olist)
        order_end = max(order_end, nxt)
    patterns = []
    pos = order_end
    while pos < dataend - 1:
        rows, nxt = decode_packed_pattern(mem, pos)
        patterns.append(rows)
        pos = nxt
    region_start = patt_lo + 2 * len(patterns)
    region_end = min(order_addrs)
    if not region_start < region_end:
        raise SidParseError("bad packed layout (instrument/table region empty)")
    num_instr = max((r.instrument for rows in patterns for r in rows), default=1)
    eps = _pattern_features(patterns)[1:]
    layout = _solve_layout(mem, region_start, region_end, num_instr, eps)
    song, info = _build_song(
        mem, header, layout, addr, firstnote, length, orderlists, patterns, num_instr
    )
    return DecompiledSid(song=song, info=info, header=header, subtune=subtune)


def _read_bytes(src) -> bytes:
    if isinstance(src, bytes):
        return src
    if isinstance(src, (str, Path)):
        return Path(src).read_bytes()
    if isinstance(src, io.IOBase) or hasattr(src, "read"):
        return src.read()
    raise TypeError(f"cannot read a sid from {type(src).__name__}")


def read_sid(src, subtune=0) -> Song:
    """Read a packed GoatTracker ``.sid`` and return its :class:`Song`."""
    return decompile_sid(src, subtune=subtune).song
