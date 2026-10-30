"""The GoatTracker V1.x playroutines as ``pysidtracker.playspec`` specs.

``specs/gt1_<family>.play`` states each family's play call; :func:`binding` resolves
its ``layout`` from the relocated reference in memory: labels, table bases, the
self-modified operands (``Reference.smc`` in code order) and the packer's parameters.
"""

from functools import lru_cache
from importlib import resources

from pysidtracker.playspec import parse as parse_spec

from pygoattracker.gt1 import (
    FILTER_STEP0,
    HARDRESTART_1FRAME,
    INIT_GATE,
    INIT_WAVE,
    MUSICROUTINE11_SFX,
    MUSICROUTINE11_TIMING,
    ORDERLIST_ENDSONG,
    ORDERLIST_LOOP_AFTER,
    PULSE_ALWAYS,
    PULSEDIR_RESET,
    Gt1Match,
    Reference,
    _match_at,
    find_player,
)
from pygoattracker.errors import SidParseError

SID = 0xD400
ZP = 0xFC
_LDA_ABS_Y, _STA_ABS, _CLC = 0xB9, 0x8D, 0x18

# The self-modified operands of each family, in code order (``Reference.smc``).
OPERANDS = {
    "1.5": (
        "init",
        "filttime",
        "filtstep",
        "cutoffadd",
        "d416",
        "d417",
        "d418",
        "volume",
        "chnloop",
        "fxjmp",
        "tick0jmp",
        "noteop",
        "vibdepth",
        "portalo",
        "portahi",
    ),
    "1.4": (
        "init",
        "chnloop",
        "fxjmp",
        "filtstep",
        "filttime",
        "filtcutoff",
        "cutoffadd",
        "tick0jmp",
    ),
    "11": ("tick0jmp", "fxjmp"),
}
PARAMS = ("tickinit", "gatetimer", "hrad", "hrsr")
# Jump-table dispatches: the operand a table entry is stored into.
DISPATCH = {
    "1.5": {"fxtab": "fxjmp", "tick0tab": "tick0jmp"},
    "1.4": {"fxtab": "fxjmp", "tick0tab": "tick0jmp"},
    "11": {"fxtab": "fxjmp", "tick0tab": "tick0jmp"},
}
# Channel variables bound by their ``mt_chn`` label (``_v``: a name a table takes).
_V11 = (
    "note",
    "freqlo_v",
    "freqhi_v",
    "newnote",
    "command",
    "cmddata",
    "instnum",
    "wave",
    "wavetbl_v",
    "pulse",
    "pulsedir",
    "arpcount",
    "vibcount",
    "songptr",
    "songadrlo",
    "songadrhi",
    "pattptr",
    "pattnum",
    "tick",
    "tempo",
    "next",
)
# Immediates bound by the label of the instruction they are the operand of.
_LABELLED = {
    "playflag": "playflag",
    "cutoff": "mt_filtcutoff",
    "cutoffadd": "mt_filtcutoffadd",
    "ctrl": "mt_filtctrl",
    "type": "mt_filttype",
    "volume": "mt_volume",
}
VARIABLES = {
    "11": _V11,
    "1.4": (
        "note",
        "freqlo_v",
        "freqhi_v",
        "newnote",
        "command",
        "cmddata",
        "instnum",
        "wave",
        "wavetbl_v",
        "pulse",
        "pulsedir",
        "arpcount",
        "packrest",
        "repeat",
        "songptr",
        "songnum",
        "pattptr",
        "pattnum",
        "tick",
        "tempo",
        "trans",
    ),
}


@lru_cache(maxsize=None)
def spec(family: str):
    """The parsed spec of playroutine ``family``."""
    name = f"specs/gt1_{family.replace('.', '')}.play"
    return parse_spec(resources.files(__package__).joinpath(name).read_text())


def _code(found: Gt1Match, mem) -> bytes:
    """The relocated playroutine's bytes in ``mem``."""
    return bytes(mem[found.address : found.address + found.ref.code_end])


def _table_of(found: Gt1Match, mem, operand: int) -> int:
    """The table ``lda table,y; sta operand`` dispatches from, in ``mem``."""
    target = operand.to_bytes(2, "little")
    code = _code(found, mem)
    for pos in range(len(code) - 5):
        if code[pos] == _LDA_ABS_Y and code[pos + 3] == _STA_ABS:
            if code[pos + 4 : pos + 6] == target:
                return int.from_bytes(code[pos + 1 : pos + 3], "little")
    raise SidParseError(f"{found.ref.name}: no table dispatch into ${operand:04x}")


def zero_page(ref: Reference, mem, base: int) -> dict:
    """``zp0``-``zp3``: where the tune's code put the reference's $FC-$FF temporaries."""
    out = {f"zp{k}": ZP + k for k in range(4)}
    for pos, zp in reversed(ref.zeropage):
        out[f"zp{zp - ZP}"] = mem[base + pos]
    return out


def layout(found: Gt1Match, mem) -> dict:
    """The spec layout of the relocated playroutine ``found`` in memory ``mem``."""
    ref, base, tables = found.ref, found.address, found.tables
    lab = {name: base + off for name, off in ref.labels.items()}
    hrtimer = ref.labels.get("mt_hrtimer")
    out = {}
    if ref.family == "11":
        out = {name: lab[label] + 1 for name, label in _LABELLED.items()}
    taken = {hrtimer, *(a - base for a in out.values())}
    smc = [pos for pos in ref.smc if pos not in taken]
    out.update(zip(OPERANDS[ref.family], (base + pos for pos in smc)))
    for var in VARIABLES.get(ref.family, ()):
        out[var] = lab["mt_chn" + var.removesuffix("_v")]
    out.update(zip(PARAMS, (base + pos + 1 for pos in ref.params)))
    for table, operand in DISPATCH[ref.family].items():
        out[table] = _table_of(found, mem, out[operand])
    out.update(
        chn=lab.get("mt_chnwave", 0),
        funk=lab.get("mt_funktempo", tables.get("filttbl", 0) + 2),
        ins=tables["instad"],
        wavetbl=tables["wavetbl"],
        notetbl=tables["notetbl"],
        songlo=tables["songtbllo"],
        songhi=tables["songtblhi"],
        pattlo=tables["patttbllo"],
        patthi=tables["patttblhi"],
        filt=tables.get("filttbl", 0),
        freqlo=lab["mt_freqtbllo"],
        freqhi=lab["mt_freqtblhi"],
        sid=SID,
        **zero_page(ref, mem, base),
        hrtimer=0 if hrtimer is None else base + hrtimer,
        has_hrtimer=int(hrtimer is not None),
        hr1=int(ref.name in HARDRESTART_1FRAME),
        filter_step0=int(ref.name in FILTER_STEP0),
        loop_after=int(ref.name in ORDERLIST_LOOP_AFTER),
        sfx=int(ref.name in MUSICROUTINE11_SFX),
        timing=int(ref.name in MUSICROUTINE11_TIMING),
        stub=int(ref.stub_init),
        entry_carry=int(ref.family == "11" and mem[lab["mt_filtcutoff"] - 1] != _CLC),
        endsong=int(ref.name in ORDERLIST_ENDSONG),
        pulse_always=int(ref.name in PULSE_ALWAYS),
        init_wave=int(ref.name in INIT_WAVE),
        init_gate=int(ref.name in INIT_GATE),
        dir_reset=int(ref.name in PULSEDIR_RESET),
    )
    return out


def row_reader(found: Gt1Match, mem) -> tuple:
    """``(start, stop)`` of a V1.5x player's row reader in ``mem``: from its pattern
    pointer load (``ldy mt_chnpattnum,x; lda patttbllo,y``) to the next channel's step.
    """
    ref, base = found.ref, found.address
    pattnum = (base + ref.labels["mt_chnpattnum"]).to_bytes(2, "little")
    table = found.tables["patttbllo"].to_bytes(2, "little")
    code = _code(found, mem)
    start = code.find(b"\xbc" + pattnum + b"\xb9" + table)
    return base + start, base + code.find(b"\xe0\x0e\xb0", start)


def locate(view, entry: int | None = None) -> Gt1Match:
    """The relocated playroutine in memory ``view`` (whose play entry is ``entry``)."""
    found = find_player(view)
    if found is None:
        raise SidParseError("no GoatTracker V1.x playroutine in memory")
    if entry is not None and entry - found.ref.play_entry != found.address:
        found = _match_at(bytes(view), found.ref, entry - found.ref.play_entry) or found
    return found


def binding(view, entry: int) -> dict:
    """The spec layout of the playroutine whose play entry is ``entry``."""
    return layout(locate(view, entry), view)


def entries(view) -> set:
    """The play entry of the playroutine in ``view``."""
    found = locate(view)
    return {found.address + found.ref.play_entry}
