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
running the init routine in a 6502 emulator (:mod:`jennings`).
"""

from dataclasses import dataclass, field

from pysidtracker import (
    BaseSidParser,
    CodePattern,
    SidError,
    SidHeader,
    SidImage,
    detect_playroutine,
    find_code_all,
    init_effects,
    read_bytes,
    trace_init,
)
from pysidtracker import parse_sid_header as _parse_sid_header

from pygoattracker import constants
from pygoattracker.errors import SidParseError
from pygoattracker.packed import (
    PackedLayout,
    PlayerBuild,
    PlayerState,
    decode,
    pack,
    player_span,
    read_build,
    read_state,
)
from pygoattracker.player import Player
from pygoattracker.typed import DoNothing, TypedSong, WaveCommand
from pygoattracker.model import (
    Orderlist,
    PlayPattern,
    Repeat,
    Row,
    Song,
    Subtune,
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

# ---------------------------------------------------------------------------
# Relocation-invariant player-code fingerprints.
#
# gt2reloc bakes every table's absolute base into the playroutine as
# ``LDA table,Y`` (opcode ``B9``) operands, and relocates the player as one
# block, so those operands move with the tune. We locate each table by its
# access idiom in ``player.s`` and read the captured base directly -- version
# tolerant, because the opcode skeleton is stable across gt2reloc/player
# revisions even as the code around it shifts. See docs/format.md.
#
# Sequencer (``mt_sequencer``) and new-note fetch (``mt_getnewnote``) share one
# skeleton -- ``lda tbllo,y; sta zp; lda tblhi,y; sta zp; ldy chn,x;
# lda (zp),y; cmp #k`` -- disambiguated by the terminal compare: ``#LOOPSONG``
# ($FF) for the song(order)-table, ``#FX`` ($40) for the pattern-pointer table.
_P_SONGTBL = CodePattern("B9 {lo:w} 85 ?? B9 {hi:w} 85 ?? BC ?? ?? B1 ?? C9 FF")
_P_PATTTBL = CodePattern("B9 {lo:w} 85 ?? B9 {hi:w} 85 ?? BC ?? ?? B1 ?? C9 40")
# ``lda ADSRtbl-1,y; sta ...`` newnote load pair -> instrument count = sr - ad.
_P_ADSR = CodePattern("B9 {sr:w} 9D 06 D4 B9 {ad:w} 9D 05 D4")
# Table "nextstep" idiom shared by wave/pulse/filter execution:
# ``lda lefttbl,y; cmp #LOOPx($FF); iny; tya; bcc; ...; lda righttbl-1,y``.
_P_TABLEPAIR = CodePattern("B9 {left:w} C9 FF C8 98 90 ?? B9 {right:w}")
_P_WAVE = CodePattern("B9 {w:w} C9 FF C8 98")  # lda wavetbl,y (start of tables)
_P_WEXEC = CodePattern("BC ?? ?? F0 ?? B9 {w:w}")  # lda wavetbl-1,y (waveexec)
# ``ldy chninstr,x; lda insgatetimer-1,y; sta chngatetimer,x`` -> gate present.
_P_GATE = CodePattern("BC ?? ?? B9 {g:w} 9D ?? ?? BD")
# ``lda inspulseptr-1,y; beq skippulse; sta chnpulseptr,x; lda #0`` -> pulse.
_P_PULSEPTR = CodePattern("B9 {v:w} F0 ?? 9D ?? ?? A9 00")


@dataclass
class PackedInfo:
    """Playroutine options and layout recovered from the packed image.

    ``freq_table`` is the tuning greloc packed, entry ``n`` for note
    ``firstnote + n``; ``note_read`` is what the packed player reads for each
    note index 0-127, the tuning and the bytes around it, which is what a
    :class:`~pygoattracker.player.Player` takes as its ``freq_table``.
    """

    firstnote: int = 0
    lastnote: int = constants.MAX_NOTES - 1
    freq_table: tuple = ()
    note_read: tuple = ()
    simplepulse: bool = False
    live_vibrato: bool = False
    nowavedelay: bool = False
    layout: PackedLayout | None = None
    build: PlayerBuild = field(default_factory=PlayerBuild)
    state: PlayerState | None = None
    init_regs: tuple = ()
    wrapper_flush: bool = False


@dataclass
class DecompiledSid:
    """Result of decompiling a ``.sid``: the song, its typed form, and how to play it."""

    song: Song
    info: PackedInfo
    header: SidHeader
    subtune: int = 0
    typed: TypedSong | None = None
    image: SidImage | None = field(default=None, repr=False)

    def player(self) -> Player:
        """A :class:`Player` of this subtune configured as the image's own player.

        The build's hard-restart ADSR and its pulse/realtime skip optimizations
        replace the editor's when the code states them.
        """
        info, build = self.info, self.info.build
        adparam = constants.DEFAULT_ADPARAM
        if build.adparam is not None:
            adparam = build.adparam << 8 | build.srparam
        return Player(
            self.typed,
            subtune=self.subtune,
            adparam=adparam,
            freq_table=info.note_read,
            simplepulse=info.simplepulse,
            live_vibrato=info.live_vibrato,
            optimize_pulse=True if build.pulseopt is None else build.pulseopt,
            optimize_realtime=True if build.realtime is None else build.realtime,
            state=info.state,
            default_tempo=build.deftempo,
            init_regs=info.init_regs,
            wrapper_flush=info.wrapper_flush,
        )

    def repack(self) -> list[tuple[int, bytes, bytes]]:
        """``(address, packed, image)`` for each piece :func:`pack` writes that the image
        does not hold: empty where the typed song re-encodes to the image's own bytes.
        """
        out = []
        for addr, data in pack(self.typed, self.info):
            have = bytes(self.image.mem[addr : addr + len(data)])
            if have != data:
                out.append((addr, data, have))
        return out


def _as_sid_parse_error(parse, raw: bytes):
    try:
        return parse(bytes(raw))
    except SidError as exc:
        raise SidParseError(str(exc)) from exc


def parse_sid_header(raw: bytes) -> SidHeader:
    """Parse a PSID/RSID header from ``raw`` file bytes."""
    return _as_sid_parse_error(_parse_sid_header, raw)


def _load_image(raw: bytes) -> SidImage:
    """Load ``raw`` ``.sid`` bytes into a :class:`~pysidtracker.SidImage`."""
    return _as_sid_parse_error(SidImage.from_sid, raw)


_FREQ_MIN_LEN = 8


def _find_freq_hi_anchor(image: SidImage):
    """Locate the frequency table by its high-byte column alone.

    Many real GoatTracker tunes ship a *finetuned* frequency table: the
    ``freqtblhi`` column is the stock GT progression but ``freqtbllo`` has been
    retuned, so the both-columns match in :func:`SidImage.find_split_table`
    misses them. The high column (a long monotonic run unique to GT) is the
    reliable signature; greloc still emits ``freqtbllo[first..last]`` in the
    ``length`` bytes immediately before it, so the low column is read from
    memory as-is. Returns ``(lo_addr, firstnote, length)`` for the longest high
    run of at least ``_FREQ_MIN_LEN`` whose low column stays in the image, else
    ``None``.
    """
    data = bytes(image.mem)
    hi = bytes(constants.FREQ_HI)
    load, end, n = image.load, image.end, len(constants.FREQ_HI)
    window = _FREQ_MIN_LEN
    best = None
    for firstnote in range(n - window + 1):
        needle = hi[firstnote : firstnote + window]
        for pos in image.find_all(needle):
            length = 0
            while (
                firstnote + length < n
                and pos + length < end
                and data[pos + length] == hi[firstnote + length]
            ):
                length += 1
            lo_addr = pos - length
            if length >= _FREQ_MIN_LEN and lo_addr >= load:
                if best is None or length > best[2]:
                    best = (lo_addr, firstnote, length)
    return best


def _recognize(image: SidImage):
    """Locate the greloc frequency-table anchor in ``image``.

    greloc emits ``freqtbllo[firstnote..lastnote]`` immediately followed by
    ``freqtblhi[firstnote..lastnote]`` (a contiguous slice of the known GT
    tables). Returns ``(addr, firstnote, length)`` for the longest match, or
    ``None``. Falls back to :func:`_find_freq_hi_anchor` for finetuned tables
    whose low column has been retuned away from the stock GT values.
    """
    anchor = image.find_split_table(
        constants.FREQ_LO, constants.FREQ_HI, min_length=_FREQ_MIN_LEN
    )
    if anchor is not None:
        return anchor
    return _find_freq_hi_anchor(image)


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


def _count_patterns(mem, patt_lo, first_patt, order_start):
    """Number of patterns, taken from the pattern-pointer table.

    greloc stores a ``patttbllo``/``patttblhi`` pointer array at ``patt_lo``
    with one entry per pattern; the patterns themselves are contiguous
    starting at ``first_patt`` (just past the orderlists). This bounds the
    pattern block exactly -- unlike the file end, which may have author info
    or other bytes trailing the patterns, or (for a decrunched image) no
    marker at all. Returns the largest pattern count whose pointer table
    matches the sequentially decoded pattern start addresses, or ``None``.
    """
    best = None
    for npat in range(1, constants.MAX_PATT + 1):
        if patt_lo + 2 * npat >= order_start:
            break
        if (mem[patt_lo] | (mem[patt_lo + npat] << 8)) != first_patt:
            continue
        pos = first_patt
        ok = True
        for i in range(npat):
            addr = mem[patt_lo + i] | (mem[patt_lo + npat + i] << 8)
            if addr != pos:
                ok = False
                break
            try:
                _, pos = decode_packed_pattern(mem, pos)
            except IndexError:
                ok = False
                break
        if ok:
            best = npat
    return best


def decode_packed_orderlist(mem, addr):
    """Decode a packed orderlist at ``addr`` into an :class:`Orderlist`.

    Packed orderlists use the .SNG entry encoding except that a repeat
    (``0xD0``-``0xDF``) trails the pattern it repeats instead of prefixing
    it. Ends at ``0xFF`` followed by a restart-position byte.

    An orderlist holds at most ``MAX_SONGLEN`` positions, each contributing a
    pattern plus an optional trailing repeat/leading transpose; walking more
    entries than that means the pointer was not a real orderlist (a wrong
    subtune-count guess in :func:`decompile_sid` lands here), so bail with
    ``ValueError`` instead of scanning unbounded garbage to the next ``0xFF``.
    """
    entries = []
    pos = addr
    limit = 3 * constants.MAX_SONGLEN
    while mem[pos] != _LOOPSONG:
        if len(entries) > limit:
            raise ValueError("orderlist has no terminator (not an orderlist)")
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


def _solve_layout(mem, region_start, region_end, num_instr, eps, build=None):
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
    # vib-vs-gate can be ambiguous (same K, same tiling): the player's code says
    # which pair it reads, else a FIXEDPARAMS-suppressible pair is the vibrato's.
    build = build or PlayerBuild()
    solutions.sort(
        key=lambda s: (
            build.insvib is not None and s["vib"] != build.insvib,
            build.gatecol is not None and s["gate"] != build.gatecol,
            s["gate"],
        )
    )
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
# Code-scan layout: read the instrument/table geometry from the player code.
#
# The four wave/pulse/filter tables sit contiguously after the instrument
# arrays. Each is reached by ``player.s``'s "nextstep" idiom (:data:`_P_TABLEPAIR`)
# whose two ``LDA table,Y`` operands give the left column and (right-1) column
# bases. Because the three tables abut (``right = left + len``; next table's
# ``left = this.left + 2*len``), the located pairs form a self-validating
# *chain*: wave (always present), then pulse and/or filter. The instrument
# region ``[insad, wavetbl)`` is then ``instruments * K`` bytes, and
# ``K = 3 + pulse + filt + 2*vib + 2*gate`` pins the remaining flags (gate is
# read from :data:`_P_GATE`; vib follows from K). This is the primary path for
# tunes the exact-fit tiling (:func:`_solve_layout`) cannot segment; it is
# validated by the same executable-consistency checks tiling uses, so a
# spurious chain is rejected rather than silently accepted.
# ---------------------------------------------------------------------------


def _chain_tables(image, insad, order_start):
    """Return ``(wavetbl, chain)`` located from the player's table-access code.

    ``chain`` is the list of ``(left, right)`` column bases for the jump-
    terminated tables (wave, then pulse/filter) in memory order, or ``None`` if
    no wave table access is found in ``[insad, order_start)``.
    """
    pairs = sorted(
        {
            (m.captures["left"], m.captures["right"] + 1)
            for m in find_code_all(image, _P_TABLEPAIR)
            if insad < m.captures["left"] < order_start
        }
    )
    if not pairs:
        return None
    wave_cands = {m.captures["w"] for m in find_code_all(image, _P_WAVE)}
    wave_cands |= {m.captures["w"] + 1 for m in find_code_all(image, _P_WEXEC)}
    wave_cands = {w for w in wave_cands if insad < w < order_start}
    start = None
    for left, right in pairs:
        if left in wave_cands:
            start = (left, right)
            break
    if start is None:
        start = pairs[0]
    by_left = dict(pairs)
    chain = [start]
    cur = start
    while len(chain) < 3:
        nxt = cur[0] + 2 * (cur[1] - cur[0])
        if nxt not in by_left:
            break
        cur = (nxt, by_left[nxt])
        chain.append(cur)
    return start[0], chain


def _chain_layout(image, mem, insad, num_instr, order_start, eps):
    """Locate the instrument/table layout from player code (see section note).

    Returns a solution dict compatible with :func:`_build_song`, or ``None`` if
    the code-located geometry is inconsistent (caller falls back to tiling).
    """
    located = _chain_tables(image, insad, order_start)
    if located is None:
        return None
    wtbl, chain = located
    wlen = chain[0][1] - chain[0][0]
    if wlen <= 0:
        return None
    if num_instr <= 0 or (wtbl - insad) % num_instr:
        return None
    k = (wtbl - insad) // num_instr
    gate = (
        1
        if any(
            insad < m.captures["g"] + 1 < wtbl for m in find_code_all(image, _P_GATE)
        )
        else 0
    )
    extra = len(chain) - 1  # pulse + filter tables present
    pulse = filt = 0
    plen = flen = 0
    if extra >= 2:
        pulse, filt = 1, 1
        plen = chain[1][1] - chain[1][0]
        flen = chain[2][1] - chain[2][0]
    elif extra == 1:
        onelen = chain[1][1] - chain[1][0]
        pulse_present = any(
            insad < m.captures["v"] + 1 < wtbl
            for m in find_code_all(image, _P_PULSEPTR)
        )
        if pulse_present:
            pulse, plen = 1, onelen
        else:
            filt, flen = 1, onelen
    rem = k - 3 - pulse - filt - 2 * gate
    if rem < 0 or rem % 2:
        return None
    vib = rem // 2
    if vib not in (0, 1) or k != 3 + pulse + filt + 2 * vib + 2 * gate:
        return None
    speedstart = wtbl + 2 * wlen + 2 * plen + 2 * flen
    remaining = order_start - speedstart
    if remaining < 0 or remaining % 2:
        return None
    if remaining == 0:
        slen, zeros = 0, 0
    else:
        zeros, slen = 1, (remaining - 2) // 2
        if slen < 0:
            return None
    off = insad
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
    if off != wtbl:
        return None
    if not _chain_valid(
        mem, arrays, eps, wtbl, wlen, pulse, plen, filt, flen, vib, slen
    ):
        return None
    return {
        "arrays": arrays,
        "pulse": pulse,
        "filt": filt,
        "vib": vib,
        "gate": gate,
        "tstart": wtbl,
        "wlen": wlen,
        "plen": plen,
        "flen": flen,
        "slen": slen,
        "zeros": zeros,
    }


def _chain_valid(mem, arrays, eps, wtbl, wlen, pulse, plen, filt, flen, vib, slen):
    """Executable-consistency check for a code-located layout (as tiling uses)."""
    wave_eps, pulse_eps, filt_eps, speed_eps = eps
    wl, ok = _executed_length(mem, wtbl, set(arrays["waveptr"]) | wave_eps)
    if not ok or wl != wlen or max(arrays["waveptr"], default=0) > wlen:
        return False
    if pulse:
        pl, ok = _executed_length(
            mem, wtbl + 2 * wlen, set(arrays["pulseptr"]) | pulse_eps
        )
        if not ok or pl != plen or max(arrays["pulseptr"], default=0) > plen:
            return False
    if filt:
        fl, ok = _executed_length(
            mem, wtbl + 2 * wlen + 2 * plen, set(arrays["filtptr"]) | filt_eps
        )
        if not ok or fl != flen or max(arrays["filtptr"], default=0) > flen:
            return False
    speed_refs = set(speed_eps)
    if vib:
        speed_refs |= set(arrays["vibparam"])
    speed_refs.discard(0)
    return max(speed_refs, default=0) <= slen


# ---------------------------------------------------------------------------
# Reverse the packed table byte transforms to the editor form the model and
# the (editor) Player expect.
# ---------------------------------------------------------------------------


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


def _build_song(mem, header, sol, anchor, orderlists, order_addrs, where, build):
    """The typed song, the editor-form :class:`Song` it states, and the info."""
    addr, firstnote, length = anchor
    songtbl, patt_lo, patt_addrs = where
    tstart, wlen, plen, flen = sol["tstart"], sol["wlen"], sol["plen"], sol["flen"]
    lay = PackedLayout(
        freq_addr=addr,
        firstnote=firstnote,
        notes=length,
        songtbl=songtbl,
        songs=len(orderlists),
        order_addrs=tuple(order_addrs),
        patt_lo=patt_lo,
        patt_addrs=tuple(patt_addrs),
        ins_start=tstart - len(next(iter(sol["arrays"].values()))) * len(sol["arrays"]),
        num_instr=len(next(iter(sol["arrays"].values()))),
        pulse=bool(sol["pulse"]),
        filt=bool(sol["filt"]),
        vib=bool(sol["vib"]),
        gate=bool(sol["gate"]),
        tstart=tstart,
        wlen=wlen,
        plen=plen,
        flen=flen,
        slen=sol["slen"],
        zeros=sol["zeros"],
        gate_flags=any(v & 0xC0 for v in sol["arrays"].get("gatetimer", ())),
    )
    pleft = list(mem[tstart + 2 * wlen : tstart + 2 * wlen + plen]) if plen else []
    nowavedelay = _detect_nowavedelay(mem[tstart : tstart + wlen])
    simplepulse = _detect_simplepulse(pleft)
    subtunes = [
        Subtune(channels=orderlists[s * 3 : s * 3 + 3])
        for s in range(len(orderlists) // 3)
    ]
    typed = decode(mem, lay, build, header, subtunes, nowavedelay, simplepulse)
    noeffects = not any(isinstance(r, WaveCommand) for r in typed.wavetable) and all(
        isinstance(r.command, (DoNothing, type(None)))
        for p in typed.patterns
        for r in p
    )
    info = PackedInfo(
        firstnote=firstnote,
        lastnote=firstnote + length - 1,
        freq_table=typed.freq,
        note_read=_freq_table(mem, addr, firstnote, length),
        simplepulse=simplepulse,
        live_vibrato=noeffects,
        nowavedelay=nowavedelay,
        layout=lay,
        build=build,
    )
    return typed.to_song(), info, typed


def _all_anchors(image):
    """Every maximal greloc frequency-table anchor in ``image``, in order.

    The :func:`_find_freq_hi_anchor` scan, one best ``(addr, firstnote,
    length)`` per site, minus runs whose high column sits inside another's,
    so an image bundling several players yields one anchor per module.
    """
    data = bytes(image.mem)
    hi = bytes(constants.FREQ_HI)
    n = len(hi)
    sites = {}
    for firstnote in range(n - _FREQ_MIN_LEN + 1):
        for pos in image.find_all(hi[firstnote : firstnote + _FREQ_MIN_LEN]):
            length = 0
            while (
                firstnote + length < n
                and pos + length < image.end
                and data[pos + length] == hi[firstnote + length]
            ):
                length += 1
            if length < _FREQ_MIN_LEN or pos - length < image.load:
                continue
            lo = pos - length
            cur = sites.get(lo)
            if cur is None or length > cur[2]:
                sites[lo] = (lo, firstnote, length)
    anchors = sorted(sites.values())
    kept = []
    for a in anchors:
        if not any(
            b[0] + b[2] <= a[0] + a[2] and a[0] + 2 * a[2] <= b[0] + 2 * b[2]
            for b in kept
        ):
            kept.append(a)
    return kept


def _ini_cell(image, base):
    """The cell a module's ``mt_init`` stores its incoming ``A`` to, per the
    stock single-store idiom (``STA abs``/``STA zp`` first), else None."""
    if base is None:
        return None
    mem = image.mem
    ini = mem[base + 1] | (mem[base + 2] << 8)
    if mem[ini] in (0x85, 0x8D):
        return mem[ini + 1] | (mem[ini + 2] << 8)
    return None


def _modules(image):
    """``(anchor, song-cell)`` per player module of ``image``.

    One entry per module jump table found by :func:`player_span`: the
    module's longest frequency-table anchor and the cell its ``mt_init``
    first stores to (the stock idiom's 0-based per-module song).
    """
    modules = {}
    for anchor in _all_anchors(image):
        base = player_span(image, anchor[0]).start
        cell = _ini_cell(image, base)
        if cell is None:
            continue
        cur = modules.get(base)
        if cur is None or anchor[2] > cur[0][2]:
            modules[base] = (anchor, cell)
    return list(modules.values())


def _finish(decoded, song):
    """``decoded`` narrowed to the selected song, else a clean ``SidParseError``."""
    if not 0 <= song < len(decoded.song.subtunes):
        raise SidParseError(
            f"no such subtune: the song selects subtune {song}, "
            f"but the player decodes {len(decoded.song.subtunes)} subtune(s)"
        )
    decoded.subtune = song
    return decoded


def _play_target(image, subtune):
    """The play entry's target after the tune's own init for ``subtune``.

    A wrapper-selected build rewrites the play entry per song, so the target
    comes from the init's own effects (a private snapshot), not the as-loaded
    image.
    """
    h = image.header
    play = h.play_address or h.real_load_address
    lo, hi = play + 1, play + 2
    effects = init_effects(image, subtune, watch={lo, hi})
    lo_val = effects.writes[lo].value if lo in effects.writes else image.mem[lo]
    hi_val = effects.writes[hi].value if hi in effects.writes else image.mem[hi]
    return lo_val | (hi_val << 8)


def _entered_anchor(image, candidates, subtune):
    """The candidate player the tune's own play entry enters for ``subtune``.

    The entered player's code span contains the (post-init) play target; a
    secondary player bundled in the image has its own span and is not entered.
    """
    target = _play_target(image, subtune)
    for anchor in candidates:
        span = player_span(image, anchor[0])
        if span.start is not None and span.start <= target < anchor[0]:
            return anchor
    return None


def _wrapper_flush(image, subtune):
    """Zero-page ghost the entered play wrapper flushes to the chip, or None.

    A wrapper-selected build copies a zero-page ghost to the SID
    (``ldx #24; lda ghost,x; sta $d400,x``) instead of the player writing the
    chip directly. The ghost starts all-zero, so the first frame is muted.
    """
    target = _play_target(image, subtune)
    mem = image.mem
    for _ in range(4):
        if mem[target] == 0x4C:  # JMP on to the wrapper (or the next hop)
            target = mem[target + 1] | (mem[target + 2] << 8)
            continue
        break
    hi = min(target + 0x40, image.end)
    for spec in (
        "A2 18 B5 {g} 9D 00 D4 CA 10",
        "A2 00 B5 {g} 9D 00 D4 E8 E0 19 D0",
    ):
        for m in find_code_all(image, spec, start=target, end=hi):
            return m.captures["g"]
    return None


def _select_song(image, default_anchor, subtune, modules):
    """Decompile the player the tune's own init/play enters, for ``subtune``.

    A wrapper-selected build rewrites the play entry per song, so the entered
    player is decompiled (not the first recognised anchor); the song-claim
    modules and the default anchor are the fallbacks, with the claim's song.
    """
    anchor = _entered_anchor(image, _all_anchors(image), subtune)
    song = subtune
    if anchor is None:
        effects = init_effects(image, subtune)
        hits = [(a, effects.writes[c]) for a, c in modules if c in effects.writes]
        if hits:
            anchor, write = max(hits, key=lambda pair: pair[1].pc)
            song = write.value
        else:
            anchor = default_anchor
    decoded = _decompile_any_songs(image, image.header, image.end, anchor, song)
    return _finish(decoded, song)


def decompile_sid(src, subtune=0) -> DecompiledSid:
    """Decompile a packed GoatTracker ``.sid`` into a :class:`DecompiledSid`.

    ``src`` may be a path, ``bytes``, or a binary file object. Crunched images
    are unpacked via shared playroutine detection (emulated init); each header
    song is resolved through the player's own init to its module and subtune.
    """
    raw = read_bytes(src)
    image = _load_image(raw)
    detection = detect_playroutine(image, _recognize, subtune=subtune)
    if detection.anchor is None:
        raise SidParseError("no GoatTracker frequency table found (not a GT sid?)")
    decoded = _select_song(image, detection.anchor, subtune, _modules(image))
    decoded.info.init_regs = init_regs(_load_image(raw), subtune)
    return decoded


def init_regs(image, subtune=0) -> tuple:
    """``(reg, value)``: the SID registers the tune's init routine leaves set."""
    writes = trace_init(image, subtune=subtune).sid_writes
    base = constants.SID_BASE
    return tuple(
        sorted(
            (addr - base, value)
            for addr, value in writes.items()
            if base <= addr < base + constants.SID_REGISTERS
        )
    )


def _candidate_song_counts(header):
    """Subtune counts to try, most-trusted first.

    The PSID ``songs`` field is preferred but is not reliable: gt2reloc emits a
    song(order)-table sized to the GoatTracker song's subtune count, which some
    tunes leave larger than the exported PSID ``songs`` value (and a few PSIDs
    advertise an implausible count). The true count is fixed by the packed
    layout, so fall back to scanning ``1..MAX_SONGS`` and take the first count
    whose whole structure decodes.
    """
    counts = []
    if 1 <= header.songs <= constants.MAX_SONGS:
        counts.append(header.songs)
    counts.extend(n for n in range(1, constants.MAX_SONGS + 1) if n not in counts)
    return counts


def _layout_candidates(image, header, anchor):
    """``(songtbl, songs, patt_lo)`` hypotheses to try, most-trusted first.

    The stock layout (songtbl right after the frequency table) at the header
    count comes first, then the in-span :data:`_P_SONGTBL` sequencer signature
    (the player's stated base and count), then every stock candidate count.
    """
    addr, _firstnote, length = anchor
    songtbl0 = addr + 2 * length
    out = []
    seen = set()

    def add(cand):
        if cand not in seen:
            seen.add(cand)
            out.append(cand)

    if 1 <= header.songs <= constants.MAX_SONGS:
        add((songtbl0, header.songs, songtbl0 + 6 * header.songs))
    low = player_span(image, addr).start
    low = low if low is not None else image.load
    for match in find_code_all(image, _P_SONGTBL):
        if not low <= match.addr < addr:
            continue
        lo, hi = match.captures["lo"], match.captures["hi"]
        if hi <= lo or (hi - lo) % 3:
            continue
        n = (hi - lo) // 3
        if 1 <= n <= constants.MAX_SONGS:
            add((lo, n, lo + 6 * n))
    for n in _candidate_song_counts(header):
        add((songtbl0, n, songtbl0 + 6 * n))
    return out


def _decompile_any_songs(image, header, dataend, anchor, subtune) -> DecompiledSid:
    """Decompile trying each layout hypothesis until one decodes.

    Candidates (see :func:`_layout_candidates`) are tried most-trusted first. On
    total failure the reported error is the one from the trusted (stock layout,
    ``header.songs``) attempt, falling back to the first-tried candidate: a later
    candidate's error (e.g. ``orderlist pointer out of range``) misattributes
    the real cause of the failure.
    """
    primary_exc = None
    first_exc = None
    for songtbl, songs, patt_lo in _layout_candidates(image, header, anchor):
        try:
            return _decompile(
                image, header, dataend, anchor, subtune, songtbl, songs, patt_lo
            )
        except (SidParseError, IndexError, ValueError) as exc:
            if first_exc is None:
                first_exc = exc
            if primary_exc is None and songs == header.songs:
                primary_exc = exc
    exc = primary_exc if primary_exc is not None else first_exc
    if exc is None:
        raise SidParseError("empty subtune search")
    if isinstance(exc, (IndexError, ValueError)):
        raise SidParseError(f"malformed packed GoatTracker data: {exc}") from exc
    raise exc


def _decompile(
    image, header, dataend, anchor, subtune, songtbl, songs, patt_lo
) -> DecompiledSid:
    mem = image.mem
    code = player_span(image, anchor[0])
    build = read_build(code)
    all_addrs = [
        mem[songtbl + i] | (mem[songtbl + 3 * songs + i] << 8) for i in range(3 * songs)
    ]
    # A player may declare more songs than it fills, so take the valid prefix.
    i = 0
    while i < len(all_addrs) and 0 < all_addrs[i] < dataend:
        i += 1
    if not i or i % 3:
        raise SidParseError("orderlist pointer out of range")
    order_addrs = all_addrs[:i]
    orderlists = []
    order_end = 0
    order_start = min(order_addrs)
    for base in order_addrs:
        olist, nxt = decode_packed_orderlist(mem, base)
        orderlists.append(olist)
        order_end = max(order_end, nxt)
    # Cheap necessary condition: pattern 0 starts right after the orderlists, so
    # patttbllo[0] must equal the low byte of that address. This rejects a wrong
    # subtune count in O(1) before the O(patterns) scan below (the count search
    # in decompile_sid tries many candidates against packed/unrelated data).
    if mem[patt_lo] != (order_end & 0xFF):
        raise SidParseError("could not locate pattern-pointer table")
    npat = _count_patterns(mem, patt_lo, order_end, order_start)
    if npat is None:
        raise SidParseError("could not locate pattern-pointer table")
    patterns, patt_addrs = [], []
    pos = order_end
    for _ in range(npat):
        patt_addrs.append(pos)
        rows, pos = decode_packed_pattern(mem, pos)
        patterns.append(rows)
    region_start = patt_lo + 2 * npat
    region_end = order_start
    if not region_start < region_end:
        raise SidParseError("bad packed layout (instrument/table region empty)")
    num_instr = max((r.instrument for rows in patterns for r in rows), default=1)
    eps = _pattern_features(patterns)[1:]
    # Exact-fit tiling first (established path); if no flag combination tiles the
    # region, read the table bases directly from the player's table-access code.
    try:
        layout = _solve_layout(mem, region_start, region_end, num_instr, eps, build)
    except SidParseError:
        layout = _chain_layout(image, mem, region_start, num_instr, region_end, eps)
        if layout is None:
            raise
    where = (songtbl, patt_lo, patt_addrs)
    song, info, typed = _build_song(
        mem, header, layout, anchor, orderlists, order_addrs, where, build
    )
    info.state = read_state(code, build)
    info.wrapper_flush = _wrapper_flush(image, subtune) is not None
    return DecompiledSid(song, info, header, subtune, typed, image)


def read_sid(src, subtune=0) -> Song:
    """Read a packed GoatTracker ``.sid`` and return its :class:`Song`."""
    return decompile_sid(src, subtune=subtune).song


class GoatTrackerSidParser(BaseSidParser):
    """Plug GoatTracker ``.sid`` decompilation into the shared parser API.

    :meth:`parse` returns the recovered :class:`~pygoattracker.model.Song`;
    :meth:`recognize` locates the greloc frequency-table anchor so the inherited
    :meth:`~pysidtracker.BaseSidParser.detect` can classify direct-load vs
    packed/relocated tunes.
    """

    error_class = SidParseError

    def parse(self, data, subtune=0, **kwargs) -> Song:
        return read_sid(data, subtune=subtune)

    def recognize(self, image: SidImage):
        return _recognize(image)
