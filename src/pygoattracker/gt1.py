"""Recognize and decompile GoatTracker V1.x packed ``.sid`` tunes.

The packer (``relocator()`` in ``goattrk.c``) copies a playroutine, relocates its
absolute operands and appends the song tables; this module matches the relocated
code against the assembled playroutines in :mod:`pygoattracker._gt1_refs`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from pysidtracker import run_init
from pysidtracker.mos6502 import disasm_len

from pygoattracker._gt1_refs import PLAYERS
from pygoattracker.errors import SidParseError
from pygoattracker.sid import _load_image

PLAYER_ORG = 0x1000
# Initial tick, gate timer and hard-restart AD/SR the packer writes by default.
DEFAULT_PARAMS = (4, 2, 0x0F, 0x00)
_STOP_OPS = (0x00, 0x01, 0x97)
_PARAM_OP = 0xFF
_SID_PAGE = 0xD4
_ZP_TEMPS = range(0xFC, 0x100)
_ZP_MODES = frozenset(
    op
    for op in range(0x100)
    if op & 0x1F in (0x01, 0x04, 0x05, 0x06, 0x11, 0x14, 0x15, 0x16)
)
_SMC_STORES = frozenset((0x8D, 0x8E, 0x8C, 0xEE, 0xCE))

# Virtual table pages of the V1.5x players and of musicroutine 11.x.
_TAGS_15 = {
    0x40: "instad",
    0x41: "wavetbl",
    0x42: "notetbl",
    0x43: "songtbllo",
    0x44: "songtblhi",
    0x45: "patttbllo",
    0x46: "patttblhi",
    0x47: "filttbl",
}
_TAGS_11 = {
    0x40: "instad",
    0x50: "wavetbl",
    0x51: "notetbl",
    0x60: "songtbllo",
    0x70: "songtblhi",
    0x80: "patttbllo",
    0x90: "patttblhi",
}

# Playroutine variants that differ in behaviour from V1.53.
FILTER_STEP0 = frozenset(("1.50", "1.50b"))
ORDERLIST_LOOP_AFTER = frozenset(("1.50",))

_SPEEDCODE = re.compile(rb"\xa2(.)\x8e\x04\xdc\xa2(.)\x8e\x05\xdc", re.S)
_INSTR_FIELDS = 8
_ORDER_END = 0xFF
_PATT_END = 0xFF
_NOCMD = 0x60


@dataclass(frozen=True)
class Reference:
    """One assembled playroutine and what the packer changes in it."""

    name: str
    native: bool
    code: bytes
    labels: dict
    code_end: int
    relocs: tuple
    params: tuple
    smc: tuple
    zeropage: tuple
    regex: re.Pattern

    @property
    def free(self) -> tuple:
        """Code bytes a tune may change: self-modified operands and zero-page temps."""
        return tuple(sorted(self.smc + self.zeropage))


def _walk(code: bytes, author):
    """Port of the packer's relocation walk: ``(relocs, params, end)``."""
    relocs, params, pos = [], [], 0
    while pos < len(code):
        if author and pos == author[0]:
            pos = author[1]
        op = code[pos]
        if op in _STOP_OPS:
            break
        if op == _PARAM_OP:
            params.append(pos)
            pos += 2
            continue
        size = disasm_len(op)
        if size == 3 and code[pos + 2] != _SID_PAGE:
            relocs.append(pos + 1)
        pos += size
    return tuple(relocs), tuple(params), pos


def _instructions(code: bytes, author, end: int):
    pos = 0
    while pos < end:
        if author and pos == author[0]:
            pos = author[1]
        op = code[pos]
        size = 2 if op == _PARAM_OP else disasm_len(op)
        yield pos, op, size
        pos += size


@lru_cache(maxsize=None)
def reference(name: str) -> Reference:
    """The preprocessed reference for playroutine ``name``."""
    entry = PLAYERS[name]
    code, author = entry["code"], entry["author"]
    relocs, params, end = _walk(code, author)
    wild = set()
    for pos in relocs:
        wild.update((pos, pos + 1))
    for pos in params:
        wild.update((pos, pos + 1))
    smc, zeropage = set(), set()
    for pos, op, size in _instructions(code, author, end):
        if size == 2 and op in _ZP_MODES and code[pos + 1] in _ZP_TEMPS:
            zeropage.add(pos + 1)
        if size == 3 and op in _SMC_STORES and pos + 1 in relocs:
            target = int.from_bytes(code[pos + 1 : pos + 3], "little") - PLAYER_ORG
            if 0 <= target < end:
                smc.add(target)
    wild.update(smc | zeropage)
    if author:
        wild.update(range(32 if entry["native"] else author[0], author[1]))
    pattern = b"".join(
        b"." if pos in wild else re.escape(code[pos : pos + 1]) for pos in range(end)
    )
    return Reference(
        name=name,
        native=entry["native"],
        code=code,
        labels=dict(entry["labels"]),
        code_end=end,
        relocs=relocs,
        params=params,
        smc=tuple(sorted(smc)),
        zeropage=tuple(sorted(zeropage)),
        regex=re.compile(pattern, re.S),
    )


@dataclass
class Gt1Match:
    """A relocated playroutine found in a memory image."""

    ref: Reference
    address: int
    tables: dict
    params: tuple


def _tags(ref: Reference) -> dict:
    return _TAGS_15 if ref.native else _TAGS_11


def _match_at(mem, ref: Reference, address: int) -> Gt1Match | None:
    """Validate the relocations of a regex hit; None if they are inconsistent."""
    tags = _tags(ref)
    tables: dict = {}
    for pos in ref.relocs:
        want = int.from_bytes(ref.code[pos : pos + 2], "little")
        have = mem[address + pos] | mem[address + pos + 1] << 8
        tag = tags.get(want >> 8)
        if tag is None:
            moved = (want - PLAYER_ORG + address) & 0xFFFF
            if pos in ref.smc:
                have, moved = have >> 8, moved >> 8
            if have != moved:
                return None
            continue
        base = (have - (want & 0xFF)) & 0xFFFF
        if tables.setdefault(tag, base) != base:
            return None
    params = tuple(mem[address + pos + 1] for pos in ref.params)
    return Gt1Match(ref=ref, address=address, tables=tables, params=params)


def find_player(mem) -> Gt1Match | None:
    """Locate a known GoatTracker V1.x playroutine in a 64 KiB image."""
    buf = bytes(mem)
    for name in PLAYERS:
        ref = reference(name)
        for hit in ref.regex.finditer(buf):
            found = _match_at(buf, ref, hit.start())
            if found is not None:
                return _prefer_data_match(buf, found)
    return None


def _prefer_data_match(buf: bytes, found: Gt1Match) -> Gt1Match:
    """Among code-identical variants, pick the one whose data block also matches."""
    ref = found.ref
    for name in PLAYERS:
        other = reference(name)
        if other.regex.pattern != ref.regex.pattern:
            continue
        start = found.address + other.labels["mt_freqtblhi"]
        if (
            buf[start : found.address + len(other.code)]
            == other.code[other.labels["mt_freqtblhi"] :]
        ):
            return Gt1Match(other, found.address, found.tables, found.params)
    return found


@dataclass
class Gt1Instrument:
    """One packed instrument (``instrwork`` row)."""

    attack_decay: int = 0
    sustain_release: int = 0
    pulse: int = 0
    pulse_speed: int = 0
    pulse_low: int = 0
    pulse_high: int = 0
    filter: int = 0
    wave_ptr: int = 0

    def to_bytes(self) -> bytes:
        """The packed 8-byte row."""
        return bytes(
            (
                self.attack_decay,
                self.sustain_release,
                self.pulse,
                self.pulse_speed,
                self.pulse_low,
                self.pulse_high,
                self.filter,
                self.wave_ptr,
            )
        )


@dataclass
class Gt1Song:
    """A decompiled GoatTracker V1.5x packed tune.

    ``state`` holds the playroutine's self-modified operands and ``data`` its
    variable/frequency block as saved in the image, so :meth:`pack` round-trips.
    """

    player: str
    address: int
    params: tuple = DEFAULT_PARAMS
    author: bytes = bytes(32)
    state: dict = field(default_factory=dict)
    data: bytes = b""
    instruments: list = field(default_factory=list)
    wavetable: list = field(default_factory=list)
    filtertable: bytes = b""
    orderlists: list = field(default_factory=list)
    patterns: list = field(default_factory=list)
    initialized: bool = False

    @property
    def ref(self) -> Reference:
        """The playroutine reference."""
        return reference(self.player)

    def layout(self) -> dict:
        """Absolute addresses of the packed tables, as ``relocator()`` lays them out."""
        ref = self.ref
        inst = self.address + len(ref.code)
        wave = inst + _INSTR_FIELDS * len(self.instruments)
        wavelen = len(self.wavetable)
        songtbl = wave + 2 * wavelen
        nsongs = len(self.orderlists)
        patttbl = songtbl + 6 * nsongs
        filt = patttbl + 2 * len(self.patterns)
        songs = filt + len(self.filtertable)
        return {
            "instad": inst - _INSTR_FIELDS,
            "wavetbl": wave - 1,
            "notetbl": wave + wavelen - 1,
            "songtbllo": songtbl,
            "songtblhi": songtbl + 3 * nsongs,
            "patttbllo": patttbl,
            "patttblhi": patttbl + len(self.patterns),
            "filttbl": filt,
            "songs": songs,
            "patterns": songs + sum(len(o) for chans in self.orderlists for o in chans),
        }

    def _player_code(self, tables: dict) -> bytearray:
        ref = self.ref
        code = bytearray(ref.code)
        tags = _tags(ref)
        for pos in ref.relocs:
            want = int.from_bytes(ref.code[pos : pos + 2], "little")
            tag = tags.get(want >> 8)
            if tag is None:
                value = want - PLAYER_ORG + self.address
            else:
                value = tables[tag] + (want & 0xFF)
            code[pos : pos + 2] = (value & 0xFFFF).to_bytes(2, "little")
        for pos, opcode, value in zip(
            ref.params, (0xA9, 0xC9, 0xA9, 0xA9), self.params
        ):
            code[pos : pos + 2] = bytes((opcode, value))
        code[32:64] = self.author
        for pos, value in self.state.items():
            code[pos] = value
        if self.data:
            code[ref.code_end :] = self.data
        return code

    def pack(self) -> tuple[int, bytes]:
        """``(address, bytes)`` of the playroutine and song data, as the packer saves them."""
        lay = self.layout()
        out = self._player_code(lay)
        out += b"".join(ins.to_bytes() for ins in self.instruments)
        out += bytes(w for w, _ in self.wavetable) + bytes(n for _, n in self.wavetable)
        addrs, pos = [], lay["songs"]
        for chans in self.orderlists:
            for order in chans:
                addrs.append(pos)
                pos += len(order)
        out += bytes(a & 0xFF for a in addrs) + bytes(a >> 8 for a in addrs)
        patt_addrs, pos = [], lay["patterns"]
        for patt in self.patterns:
            patt_addrs.append(pos)
            pos += len(patt)
        out += bytes(a & 0xFF for a in patt_addrs) + bytes(a >> 8 for a in patt_addrs)
        out += self.filtertable
        out += b"".join(o for chans in self.orderlists for o in chans)
        out += b"".join(self.patterns)
        return self.address, bytes(out)

    def to_sid(self, name: str = "", author: str = "", released: str = "") -> bytes:
        """A PSID v2 file playing this song, as the packer's SID output writes it."""
        address, data = self.pack()
        head = bytearray(0x7C)
        head[0:8] = b"PSID\x00\x02\x00\x7c"
        head[0x0A:0x0C] = address.to_bytes(2, "big")
        head[0x0C:0x0E] = (address + 3).to_bytes(2, "big")
        head[0x0E:0x10] = len(self.orderlists).to_bytes(2, "big")
        head[0x10:0x12] = (1).to_bytes(2, "big")
        for offset, text in ((0x16, name), (0x36, author), (0x56, released)):
            head[offset : offset + 32] = text.encode("latin-1")[:32].ljust(32, b"\0")
        return bytes(head) + address.to_bytes(2, "little") + data


def _word(mem, lo: int, hi: int) -> int:
    return mem[lo & 0xFFFF] | mem[hi & 0xFFFF] << 8


def _orderlist_len(mem, addr: int) -> int:
    for pos in range(addr, min(addr + 0x101, 0xFFFF)):
        if mem[pos] == _ORDER_END:
            return pos + 2 - addr
    raise SidParseError(f"GT1 orderlist at ${addr:04X} has no end mark")


def pattern_len(mem, addr: int, end: int = 0x10000) -> int:
    """Length of the packed pattern at ``addr`` through its end mark, cut at ``end``."""
    pos = addr
    while pos < end and pos - addr < 0x100:
        pos += 3 if mem[pos] < _NOCMD else 1
        if pos >= end:
            break
        if mem[pos] == _PATT_END:
            return pos + 1 - addr
    if pos >= end:
        return max(end - addr, 0)
    raise SidParseError(f"GT1 pattern at ${addr:04X} has no end mark")


def _need(cond: bool, what: str) -> None:
    if not cond:
        raise SidParseError(f"GT1 packed layout: {what}")


def decompile_image(mem, found: Gt1Match, end: int = 0x10000) -> Gt1Song:
    """Decompile the packed tables that follow a matched V1.5x playroutine.

    ``end`` is where the loaded data stops: a last pattern cut short by it keeps
    only the loaded bytes.
    """
    ref, tab = found.ref, found.tables
    _need(ref.native, f"no native player for musicroutine {ref.name}")
    _need(set(tab) == set(_TAGS_15.values()), "missing table references")
    inst = (tab["instad"] + _INSTR_FIELDS) & 0xFFFF
    wave = (tab["wavetbl"] + 1) & 0xFFFF
    wavelen = (tab["notetbl"] + 1 - wave) & 0xFFFF
    nsongs3 = (tab["songtblhi"] - tab["songtbllo"]) & 0xFFFF
    npatt = (tab["patttblhi"] - tab["patttbllo"]) & 0xFFFF
    _need(inst == found.address + len(ref.code), "instruments do not follow the player")
    _need((wave - inst) % _INSTR_FIELDS == 0 and wave > inst, "instrument table size")
    _need(tab["songtbllo"] == wave + 2 * wavelen, "songtable does not follow wavetable")
    _need(nsongs3 % 3 == 0 and nsongs3, "songtable size")
    _need(tab["patttbllo"] == tab["songtbllo"] + 2 * nsongs3, "patterntable position")
    _need(
        tab["filttbl"] == tab["patttbllo"] + 2 * npatt and npatt, "filtertable position"
    )
    order_addrs = [
        _word(mem, tab["songtbllo"] + i, tab["songtblhi"] + i) for i in range(nsongs3)
    ]
    filtsize = order_addrs[0] - tab["filttbl"]
    _need(filtsize > 0 and filtsize % 4 == 0, "filtertable size")
    orders = []
    pos = order_addrs[0]
    for addr in order_addrs:
        _need(addr == pos, "orderlists are not contiguous")
        size = _orderlist_len(mem, addr)
        orders.append(bytes(mem[addr : addr + size]))
        pos += size
    patt_addrs = [
        _word(mem, tab["patttbllo"] + i, tab["patttblhi"] + i) for i in range(npatt)
    ]
    patterns = []
    for num, addr in enumerate(patt_addrs):
        _need(addr == pos, "patterns are not contiguous")
        if num + 1 < npatt:
            size = patt_addrs[num + 1] - addr
            _need(0 < size <= 0x100, "pattern size")
        else:
            size = pattern_len(mem, addr, end)
        patterns.append(bytes(mem[addr : addr + size]))
        pos += size
    base = found.address
    rows = [mem[i : i + _INSTR_FIELDS] for i in range(inst, wave, _INSTR_FIELDS)]
    return Gt1Song(
        player=ref.name,
        address=base,
        params=found.params,
        author=bytes(mem[base + 32 : base + 64]),
        state={pos: mem[base + pos] for pos in ref.free},
        data=bytes(mem[base + ref.code_end : base + len(ref.code)]),
        instruments=[Gt1Instrument(*row) for row in rows],
        wavetable=list(
            zip(mem[wave : wave + wavelen], mem[wave + wavelen : wave + 2 * wavelen])
        ),
        filtertable=bytes(mem[tab["filttbl"] : order_addrs[0]]),
        orderlists=[orders[i : i + 3] for i in range(0, nsongs3, 3)],
        patterns=patterns,
    )


def speedcode_timer(mem, init: int) -> int | None:
    """The CIA timer value of a multispeed tune's ``init`` speed code, if present."""
    hit = _SPEEDCODE.fullmatch(bytes(mem[init : init + 10]))
    return hit and hit.group(1)[0] | hit.group(2)[0] << 8


def decompile_gt1(raw: bytes, subtune: int = 0) -> Gt1Song:
    """Decompile a GoatTracker V1.5x packed ``.sid`` into a :class:`Gt1Song`.

    A tune whose header init is not the packed entry (an init stub) is decompiled
    from memory after running that init for ``subtune`` in a 6502 emulator.
    """
    image = _load_image(raw)
    found = find_player(image.mem)
    if found is None:
        raise SidParseError("no GoatTracker V1.x playroutine found")
    header, address = image.header, found.address
    if header.play_address != address + 3:
        raise SidParseError("GT1 tune play address is not the packed jump table")
    if header.init_address == address or (
        header.init_address == address - 10
        and speedcode_timer(image.mem, address - 10) is not None
    ):
        return decompile_image(image.mem, found, image.end)
    run_init(image, subtune=subtune)
    found = _match_at(bytes(image.mem), found.ref, address)
    if found is None:
        raise SidParseError("GT1 init stub changed the playroutine")
    song = decompile_image(image.mem, found, image.end)
    song.initialized = True
    return song
