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
from pysidtracker.poweron import power_on_ram

from pygoattracker import constants
from pygoattracker.errors import SidParseError
from pygoattracker.observe import instrument_layout, song_tables
from pygoattracker.packed import (
    PackedLayout,
    PlayerBuild,
    PlayerState,
    columns,
    decode,
    pack,
    pattern_rows,
    player_span,
)
from pygoattracker.player import Player
from pygoattracker.probe import Bench, ProbeMiss, boot, measure, state
from pygoattracker.typed import DoNothing, TypedSong, WaveCommand
from pygoattracker.model import (
    Orderlist,
    PlayPattern,
    Repeat,
    Song,
    Subtune,
    Transpose,
)

# Packed orderlist grammar (shares .SNG encoding except repeats trail
# their pattern instead of prefixing it).
_REPEAT = 0xD0
_TRANSDOWN = 0xE0
_TRANSUP = 0xF0
_LOOPSONG = 0xFF

# The packed player's pattern fetch, ``lda patttbllo,y; sta zp; lda patttblhi,y;
# sta zp; ldy chn,x; lda (zp),y; cmp #FX``: :meth:`GoatTrackerSidParser.recognize`
# tells a GoatTracker image by it, the frequency table being common to most trackers.
_P_PATTTBL = CodePattern("B9 {lo:w} 85 ?? B9 {hi:w} 85 ?? BC ?? ?? B1 ?? C9 40")


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
    memory: bytes | None = field(default=None, repr=False)

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


def _load_image(raw: bytes) -> SidImage:
    """Load ``raw`` ``.sid`` bytes into a :class:`~pysidtracker.SidImage`, over
    the power-on RAM the player finds around them."""
    image = _as_sid_parse_error(SidImage.from_sid, raw)
    ram = power_on_ram()
    ram[image.load : image.end] = image.mem[image.load : image.end]
    image.mem[:] = ram
    return image


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
    nowavedelay = (
        _detect_nowavedelay(mem[tstart : tstart + wlen])
        if build.wavedelay is None
        else not build.wavedelay
    )
    simplepulse = (
        _detect_simplepulse(pleft) if build.simplepulse is None else build.simplepulse
    )
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
        note_read=_freq_table(mem, addr, firstnote, length, bases),
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


def _finish(decoded, song):
    """``decoded`` narrowed to the selected song, else a clean ``SidParseError``."""
    if not 0 <= song < len(decoded.song.subtunes):
        raise SidParseError(
            f"no such subtune: the song selects subtune {song}, "
            f"but the player decodes {count} subtune(s)"
        )
    decoded.subtune = song
    return decoded


def decompile_sid(src, subtune=0) -> DecompiledSid:
    """Decompile a packed GoatTracker ``.sid`` into a :class:`DecompiledSid`.

    ``src`` may be a path, ``bytes``, or a binary file object. Crunched images
    are unpacked via shared playroutine detection (emulated init); the song data
    is where the player the header song runs reads it (:mod:`pygoattracker.observe`).
    """
    raw = read_bytes(src)
    image = _load_image(raw)
    detection = detect_playroutine(image, _recognize, subtune=subtune)
    if detection.anchor is None:
        raise SidParseError("no GoatTracker frequency table found (not a GT sid?)")
    try:
        decoded = _decompile(image, image.header, detection.anchor, subtune)
    except (IndexError, ValueError) as exc:
        raise SidParseError(f"malformed packed GoatTracker data: {exc}") from exc
    except KeyError as exc:
        raise SidParseError(f"malformed packed GoatTracker data: byte {exc}") from exc
    except ProbeMiss as exc:
        raise SidParseError(f"the song data does not hold a probe: {exc}") from exc
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


def _decompile(image, header, anchor, subtune) -> DecompiledSid:
    """The song data the player the tune's init selects reads, decompiled."""
    base = player_span(image, anchor[0]).start
    play = header.play_address or (None if base is None else base + 3)
    booted = boot(image, subtune)
    mem = bytes(booted[0])
    after = SidImage(bytearray(mem), 0, len(mem), header, image.image, b"")
    t = song_tables((*booted[:3], play or booted[3]))

    def pointers(lo, hi, n):
        return [mem[lo + i] | mem[hi + i] << 8 for i in range(n)]

    order_addrs = pointers(t.song_lo, t.song_hi, 3 * t.songs)
    patt_addrs = pointers(t.patt_lo, t.patt_hi, t.patterns)
    orderlists, orders = [], []
    for addr in order_addrs:
        olist, nxt = decode_packed_orderlist(mem, addr)
        orderlists.append(olist)
        orders.append(nxt - addr)
    sizes = tuple(pattern_rows(mem, a)[1] - a for a in patt_addrs)
    lay = PackedLayout(
        *anchor,
        songtbl=t.song_lo,
        songs=t.songs,
        order_addrs=tuple(order_addrs),
        patt_lo=t.patt_lo,
        patt_addrs=tuple(patt_addrs),
        **dict.fromkeys(("ins_start", "num_instr", "tstart", "wlen", "plen"), 0),
        **dict.fromkeys(("flen", "slen", "zeros"), 0),
        **dict.fromkeys(("pulse", "filt", "vib", "gate"), False),
        songtbl_hi=t.song_hi,
        patt_hi=t.patt_hi,
    )

    def bench(lay):
        span = (player_span(after, lay.freq_addr).start, lay.freq_addr)
        return Bench(image, lay, sizes, tuple(orders), subtune, play, span, t.song)

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
    build = measure(probe)
    song, info, typed = _build_song(mem, header, lay, anchor, orderlists, build, bases)
    info.state = state(probe, build)
    info.wrapper_flush = build.wrapper
    decoded = DecompiledSid(song, info, header, subtune, typed, image, mem)
    return _finish(decoded, t.song)


def read_sid(src, subtune=0) -> Song:
    """Read a packed GoatTracker ``.sid`` and return its :class:`Song`."""
    return decompile_sid(src, subtune=subtune).song


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
        if find_code_first(image, _P_PATTTBL) is None:
            return None
        return _recognize(image)
