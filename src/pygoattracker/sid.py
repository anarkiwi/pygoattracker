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

import struct
from dataclasses import dataclass, field, replace

from pysidtracker import (
    BaseSidParser,
    CodePattern,
    SidError,
    SidHeader,
    SidImage,
    detect_playroutine,
    find_code_first,
    read_bytes,
    trace_init,
)
from pysidtracker import parse_sid_header as _parse_sid_header
from pysidtracker.image import load_prefix
from pysidtracker.emu import SidWriteLog
from pysidtracker.wrap import Wrap, Wrapped, count_entries, measure_wrap
from pysidtracker.wrap import boot as boot_wrap
from pysidtracker.poweron import power_on_ram

from pygoattracker import constants
from pygoattracker.errors import SidParseError
from pygoattracker.observe import Trace, instrument_layout, song_tables
from pygoattracker.packed import (
    WAVE_NEW,
    WAVE_NONE,
    WAVE_OLD,
    Build,
    PackedLayout,
    PlayerBuild,
    PlayerState,
    columns,
    decode,
    pack,
    packed_orderlist,
    pattern_rows,
    play_entry,
    player_span,
)
from pygoattracker.player import Player
from pygoattracker.probe import Bench, ProbeMiss, boot, measure, state
from pygoattracker.typed import DoNothing, TypedSong, WaveCommand
from pygoattracker.variant import heuristic_entry
from pygoattracker.model import Song, Subtune

# The packed player's pattern fetch, ``lda patttbllo,y; sta zp; lda patttblhi,y;
# sta zp; ldy chn,x; lda (zp),y; cmp #FX`` (the binary relocator's
# players step the index between the last two): :meth:`GoatTrackerSidParser.recognize`
# tells a GoatTracker image by it, the frequency table being common to most trackers.
_P_PATTTBL = (
    CodePattern("B9 {lo:w} 85 ?? B9 {hi:w} 85 ?? BC ?? ?? B1 ?? C9 40"),
    CodePattern("B9 {lo:w} 85 ?? B9 {hi:w} 85 ?? BC ?? ?? B1 ?? C8 C9 40"),
)


@dataclass
class PackedInfo:
    """Playroutine options and layout recovered from the packed image.

    ``freq_table`` is the tuning greloc packed, entry ``n`` for note
    ``firstnote + n``; ``note_read`` is what the packed player reads for each
    note index 0-127, the tuning and the bytes around it, which is what a
    :class:`~pygoattracker.player.Player` takes as its ``freq_table``;
    ``playback`` is what ``build`` and the song data resolve to; ``code`` the
    ``[start, end)`` of the player's own code.
    """

    firstnote: int = 0
    lastnote: int = constants.MAX_NOTES - 1
    freq_table: tuple = ()
    note_read: tuple = ()
    wave: int = WAVE_NEW
    layout: PackedLayout | None = None
    build: PlayerBuild = field(default_factory=PlayerBuild)
    playback: Build = field(default_factory=Build)
    state: PlayerState | None = None
    wrap: Wrap | None = None
    code: tuple | None = None

    @property
    def nowavedelay(self) -> bool:
        """Whether the wavetable has no delays (greloc NOWAVEDELAY)."""
        return self.wave == WAVE_NONE

    @property
    def channels(self) -> int:
        """The channels the player sequences."""
        return len(self.playback.voices or ()) or constants.MAX_CHN


@dataclass
class DecompiledSid:
    """Result of decompiling a ``.sid``: the song, its typed form, and how to play it."""

    song: Song
    info: PackedInfo
    header: SidHeader
    subtune: int = 0
    typed: TypedSong | None = None
    image: SidImage | None = field(default=None, repr=False)
    memory: bytes | None = field(default=None, repr=False)

    def player(self) -> Player:
        """A :class:`Player` of this subtune configured as the image's own player.

        The build's hard-restart ADSR and its pulse/realtime skip optimizations
        replace the editor's when the code states them.
        """
        player = Player(
            self.typed,
            subtune=self.subtune,
            build=self.info.playback,
            state=self.info.state,
            freq_table=self.info.note_read,
        )
        if self.info.wrap is None:
            return player
        sink = self.info.wrap.sink
        return Wrapped(lambda: player.tick(sink), self.image, self.info.wrap)

    def repack(self) -> list[tuple[int, bytes, bytes]]:
        """``(address, packed, held)`` for each piece :func:`pack` writes that the memory
        the player read the song from does not hold: empty where the typed song
        re-encodes to its own bytes.
        """
        mem = self.image.mem if self.memory is None else self.memory
        out = []
        for addr, data in pack(self.typed, self.info):
            have = bytes(mem[addr : addr + len(data)])
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


PACKER_LOAD = 0x1000  # where the GoatTracker packer places the player by default


def as_psid(raw: bytes, load_address: int | None = None) -> bytes:
    """``raw`` as PSID bytes: a PSID/RSID passes through, a bare packer ``.prg``
    (2-byte load address, then the image) or ``.bin`` (the image alone, at
    ``load_address``) is wrapped in a header whose init/play are the packer's
    jump table at the load address (+0 and +3)."""
    raw = bytes(raw)
    if raw[:4] in (b"PSID", b"RSID"):
        return raw
    if load_address is None:
        if len(raw) < 3:
            raise SidParseError("not a PSID/RSID file or a .prg image")
        load_address, raw = int.from_bytes(raw[:2], "little"), raw[2:]
    header = bytearray(0x7C)
    header[0:4] = b"PSID"
    struct.pack_into(
        ">HHHHHHH", header, 4, 2, 0x7C, 0, load_address, load_address + 3, 1, 1
    )
    return bytes(header) + load_prefix(load_address) + raw


def _bin_default(src, load_address):
    """``load_address``, or the packer's default for a path ending in ``.bin``."""
    if load_address is None and str(getattr(src, "name", src)).lower().endswith(".bin"):
        return PACKER_LOAD
    return load_address


def _load_image(raw: bytes, load_address: int | None = None) -> SidImage:
    """Load ``raw`` ``.sid`` (or packer ``.prg``/``.bin``) bytes into a
    :class:`~pysidtracker.SidImage`, over the power-on RAM the player finds
    around them."""
    raw = as_psid(raw, load_address)
    image = _as_sid_parse_error(SidImage.from_sid, raw)
    ram = power_on_ram()
    ram[image.load : image.end] = image.mem[image.load : image.end]
    image.mem[:] = ram
    return image


_FREQ_MIN_LEN = 8
_INIT_CYCLES = 8_000_000


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


def _detect_nowavedelay(wave_left):
    """greloc's optimized packing: NOWAVEDELAY unless a wavetable delay row
    (0x01-0x0F) exists. Only where the player cannot be run."""
    return not any(0x01 <= b <= 0x0F for b in wave_left)


def _detect_simplepulse(pulse_left):
    """greloc's optimized packing: SIMPLEPULSE forces set-pulse rows to a 0x80
    marker. Only where the player cannot be run."""
    return not any(0x81 <= b <= 0xFE for b in pulse_left)


def _freq_table(mem, addr, firstnote, length, bases=None):
    """128-entry note-frequency table the packed player reads, reproducing its
    out-of-range overruns into adjacent memory: by the bases its note loads index
    where they are seen, else from the frequency-table anchor."""
    lo, hi = bases or (addr - firstnote, addr + length - firstnote)
    return tuple(
        (mem[(hi + n) & 0xFFFF] << 8) | mem[(lo + n) & 0xFFFF] for n in range(128)
    )


def _build_song(mem, header, lay, anchor, orderlists, build, bases=None):
    """The typed song, the editor-form :class:`Song` it states, and the info;
    ``bases`` the frequency-table bases the player's note loads index."""
    addr, firstnote, length = anchor
    tstart, wlen, plen = lay.tstart, lay.wlen, lay.plen
    pleft = list(mem[tstart + 2 * wlen : tstart + 2 * wlen + plen]) if plen else []
    nowavedelay = heuristic_entry(
        None if build.wavedelay is None else not build.wavedelay,
        lambda: _detect_nowavedelay(mem[tstart : tstart + wlen]),
        "wavetable",
    ).value
    wave = WAVE_OLD if build.lowdelay else WAVE_NONE if nowavedelay else WAVE_NEW
    simplepulse = heuristic_entry(
        build.simplepulse, lambda: _detect_simplepulse(pleft), "pulse table"
    ).value
    n = lay.channels
    subtunes = [
        Subtune(channels=orderlists[s * n : s * n + n])
        for s in range(len(orderlists) // n)
    ]
    typed = decode(mem, lay, build, header, subtunes, wave, simplepulse)
    noeffects = heuristic_entry(
        build.noeffects,
        lambda: not any(isinstance(r, WaveCommand) for r in typed.wavetable)
        and all(
            isinstance(r.command, (DoNothing, type(None)))
            for p in typed.patterns
            for r in p
        ),
        "patterns",
    ).value
    info = PackedInfo(
        firstnote=firstnote,
        lastnote=firstnote + length - 1,
        freq_table=typed.freq,
        note_read=_freq_table(mem, addr, firstnote, length, bases),
        wave=wave,
        layout=lay,
        build=build,
        playback=build.resolve(
            simplepulse=simplepulse,
            live_vibrato=noeffects and not lay.binary,
            vibstop=lay.binary,
        ),
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


def _played_anchor(after, anchor, entered):
    """The frequency-table anchor of the first player whose jump table the init
    ``entered``, else ``anchor``: a wrapper bundling several players runs the one
    its song selects."""
    for found in _all_anchors(after):
        start = player_span(after, found[0], entered).start
        if start is not None and start in entered:
            return found
    return anchor


def _finish(decoded, song):
    """``decoded`` narrowed to the selected song, else a clean ``SidParseError``."""
    if not 0 <= song < len(decoded.song.subtunes):
        raise SidParseError(
            f"no such subtune: the song selects subtune {song}, "
            f"but the player decodes {len(decoded.song.subtunes)} subtune(s)"
        )
    decoded.subtune = song
    return decoded


def decompile_sid(src, subtune=0, load_address=None) -> DecompiledSid:
    """Decompile a packed GoatTracker ``.sid`` into a :class:`DecompiledSid`.

    ``src`` may be a path, ``bytes``, or a binary file object, holding a PSID/RSID
    or the packer's bare ``.prg`` (load address first) or ``.bin`` (raw image at
    ``load_address``, ``$1000`` for a ``.bin`` path). Crunched images
    are unpacked via shared playroutine detection (emulated init); the song data
    is where the player the header song runs reads it (:mod:`pygoattracker.observe`).
    """
    raw = as_psid(read_bytes(src), _bin_default(src, load_address))
    image = _load_image(raw)
    detection = detect_playroutine(image, _recognize, subtune=subtune)
    if detection.anchor is None:
        raise SidParseError("no GoatTracker frequency table found (not a GT sid?)")
    try:
        decoded = _decompile(
            image, image.header, detection.anchor, subtune, _chips(raw)
        )
    except (IndexError, ValueError) as exc:
        raise SidParseError(f"malformed packed GoatTracker data: {exc}") from exc
    except KeyError as exc:
        raise SidParseError(f"malformed packed GoatTracker data: byte {exc}") from exc
    except ProbeMiss as exc:
        raise SidParseError(f"the song data does not hold a probe: {exc}") from exc
    info = decoded.info
    info.playback = replace(
        info.playback, init_regs=init_regs(_load_image(raw), subtune)
    )
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


# PSID header bytes of the second (v3+) and third (v4+) SID's ``$Dxx0`` address.
_EXTRA_SIDS = ((3, 0x7A), (4, 0x7B))


def _chips(raw) -> set:
    """The chips (``$20`` blocks from ``$D400``) the ``.sid`` header declares."""
    version = int.from_bytes(raw[4:6], "big")
    chips = {0}
    for since, pos in _EXTRA_SIDS:
        addr = 0xD000 | raw[pos] << 4 if len(raw) > pos else 0
        if version >= since and 0xD420 <= addr < 0xD800:
            chips.add((addr - constants.SID_BASE) // 0x20)
    return chips


def _mirror(voices, chips):
    """``voices`` with each register offset on a chip not in ``chips`` folded onto
    ``$D400``, which the chip select mirrors there."""
    if voices is None:
        return None
    return tuple(v if v is None or v // 0x20 in chips else v % 0x20 for v in voices)


def _flushed(image, wrap, voices):
    """``voices`` (offsets from ``$D400`` the play routine writes each channel to)
    as offsets into the ghost ``wrap`` flushes: the ghost byte the routine copies
    to each voice's first register."""
    if voices is None:
        return None
    mpu, mem = boot_wrap(image, wrap.song)[1:3]
    log = SidWriteLog(mpu, mirrors=False)
    for _ in range(2):
        writes = log.call(mem, wrap.play)
    read = {reg: addr - wrap.sink for reg, addr, _, _ in writes if addr is not None}
    return tuple(v if v is None else read.get(v, v) for v in voices)


def _decompile(image, header, anchor, subtune, chips=frozenset((0,))) -> DecompiledSid:
    """The song data the player the tune's init selects reads, decompiled, its
    voices on the header's ``chips``."""

    base = player_span(image, anchor[0]).start
    play = header.play_address or (None if base is None else base + 3)
    traced = []

    def init_run(subject, mpu, mem, address, song):
        traced.append(Trace(subject, mpu, mem))
        traced[0].call(
            address, song, _INIT_CYCLES, "init", idle=not header.play_address
        )

    booted = boot(image, subtune, init_run=init_run)
    mem = bytes(booted[0])
    after = SidImage(bytearray(mem), 0, len(mem), header, image.image, b"")
    copies = {}
    for addr, (org, exact, *_) in traced[0].cells.items():
        if exact and org is not None and org != addr:
            copies.setdefault(org, []).append(addr)
    entered = traced[0].pcs
    anchor = _played_anchor(after, anchor, entered)
    t = song_tables((*booted[:3], play or booted[3]), traced[0])

    def pointers(lo, hi, n):
        return [mem[lo + i] | mem[hi + i] << 8 for i in range(n)]

    order_addrs = pointers(t.song_lo, t.song_hi, t.stride * t.songs)
    # greloc lays out an empty song (all its channels' orderlists zero-length, so
    # their pointers coincide) when a song in its range has a zero-length channel;
    # a real orderlist is at least 2 bytes, so drop such trailing songs.
    songs = t.songs
    while songs > 1 and len(set(order_addrs[(songs - 1) * t.stride :])) == 1:
        songs -= 1
        del order_addrs[songs * t.stride :]
    patt_addrs = pointers(t.patt_lo, t.patt_hi, t.patterns)
    orderlists, orders = [], []
    for addr in order_addrs:
        olist, nxt = packed_orderlist(mem, addr)
        orderlists.append(olist)
        orders.append(nxt - addr)
    # a player sequencing fewer channels than greloc lays leaves the rest's pointers
    # unread, where a packer may point them anywhere, into the player's own code
    for k in range(len(orders)):
        if k % t.stride >= t.channels:
            orders[k] = 0
    sizes = tuple(pattern_rows(mem, a)[1] - a for a in patt_addrs)
    lay = PackedLayout(
        *anchor,
        songtbl=t.song_lo,
        songs=songs,
        order_addrs=tuple(order_addrs),
        patt_lo=t.patt_lo,
        patt_addrs=tuple(patt_addrs),
        **dict.fromkeys(("ins_start", "num_instr", "tstart", "wlen", "plen"), 0),
        **dict.fromkeys(("flen", "slen", "zeros"), 0),
        **dict.fromkeys(("pulse", "filt", "vib", "gate"), False),
        songtbl_hi=t.song_hi,
        patt_hi=t.patt_hi,
        channels=t.stride,
    )

    code = player_span(after, anchor[0], entered)
    entries = frozenset() if code.start is None else play_entry(after, code)
    wrap = measure_wrap(image, subtune, play, entries)
    if play is not None and count_entries(image, subtune, play, entries) > 1:
        play = code.start + 3

    def bench(lay):
        span = (player_span(after, lay.freq_addr, entered).start, lay.freq_addr)
        return Bench(
            image, lay, sizes, tuple(orders), subtune, play, span, t.song, copies
        )

    tables = (t.song_lo, t.song_hi, t.patt_lo, t.patt_hi)
    fields = instrument_layout(bench(lay), _all_anchors(after), tables)
    anchor, bases = fields.pop("anchor") or anchor, fields.pop("bases")
    lay = replace(lay, freq_addr=anchor[0], firstnote=anchor[1], notes=anchor[2])
    lay = replace(lay, **fields)
    gate = columns(lay).index("gate") if lay.gate else None
    if gate is not None:
        col = lay.ins_start + gate * lay.num_instr
        lay = replace(
            lay, gate_flags=any(v & 0xC0 for v in mem[col : col + lay.num_instr])
        )
    probe = bench(lay)
    build = replace(measure(probe, t.channels), idle=t.idle)
    if wrap is not None and wrap.sink is not None:
        voices = _flushed(image, wrap, build.voices)
        build = replace(build, ghost=wrap.sink, voices=voices)
    build = replace(build, voices=_mirror(build.voices, chips))
    song, info, typed = _build_song(mem, header, lay, anchor, orderlists, build, bases)
    info.state = state(probe, build)
    info.wrap = wrap
    info.code = probe.span
    decoded = DecompiledSid(song, info, header, subtune, typed, image, mem)
    return _finish(decoded, t.song)


def read_sid(src, subtune=0, load_address=None) -> Song:
    """Read a packed GoatTracker ``.sid``/``.prg``/``.bin`` and return its :class:`Song`."""
    return decompile_sid(src, subtune=subtune, load_address=load_address).song


class GoatTrackerSidParser(BaseSidParser):
    """Plug GoatTracker ``.sid`` decompilation into the shared parser API.

    :meth:`parse` returns the :class:`DecompiledSid` of a subtune (its ``song``
    and a :meth:`DecompiledSid.player` of it); :meth:`recognize` returns the
    greloc frequency-table anchor, so the inherited
    :meth:`~pysidtracker.BaseSidParser.detect` can classify direct-load vs
    packed/relocated tunes, but only when the image also holds the packed
    player's pattern-fetch idiom: the standard frequency table alone is common
    to most trackers' tunes.
    """

    error_class = SidParseError

    def parse(self, data, subtune=0, **kwargs) -> DecompiledSid:
        return decompile_sid(data, subtune=subtune)

    def recognize(self, image: SidImage):
        if all(find_code_first(image, p) is None for p in _P_PATTTBL):
            return None
        return _recognize(image)
