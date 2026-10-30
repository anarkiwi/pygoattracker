"""A GoatTracker 2 playroutine source assembled at test time, never committed.

:func:`assemble` lays a :func:`tests._gt2refs.sources` tree out as Magnus Lind's
assembler does (passes until labels settle, zero page for operands known below
``$100``) and names every symbol in a :class:`~pygoattracker.gt2spec.Match`.
"""

from __future__ import annotations

from jennings.opcodes import OPCODES

from pygoattracker.gt2spec import Match
from tests._gt2match import _Env

_ENCODE = {v: op for op, v in sorted(OPCODES.items(), reverse=True)}
_FORMS = {"mem": ("zp", "abs"), "memx": ("zpx", "absx"), "memy": ("zpy", "absy")}
_ZP = frozenset(("zp", "zpx", "zpy"))
_PASSES = 8
# Relocator options the spec layout does not read, as greloc defaults them.
GRELOC = {
    "SOUNDSUPPORT": 0,
    "VOLSUPPORT": 0,
    "NOSETAD": 0,
    "NOSETSR": 0,
    "NOSETWAVE": 0,
    "NOSETWAVEPTR": 0,
    "NOSETPULSEPTR": 0,
    "NOSETFILTPTR": 0,
    "NOSETFILTCTRL": 0,
    "NOSETFILTCUTOFF": 0,
    "NOSETMASTERVOL": 0,
    "FIRSTWAVEPARAM": 0,
    "GATETIMERPARAM": 0,
}


class AssemblyError(ValueError):
    """The source does not assemble with the symbols given."""


def _encode(mnem: str, mode: str, value, final: bool) -> tuple:
    """``(opcode, operand size)`` of one instruction."""
    for form in _FORMS.get(mode, (mode,)):
        op = _ENCODE.get((mnem, form))
        if op is None or (form in _ZP and (value is None or not 0 <= value < 0x100)):
            continue
        if form in ("impl", "acc"):
            return op, 0
        return op, 1 if form in _ZP or form in ("imm", "rel", "indx", "indy") else 2
    if final or mode not in _FORMS:
        raise AssemblyError(f"no encoding of {mnem} {mode} {value}")
    return _ENCODE[(mnem, _FORMS[mode][1])], 2


def _operand(op: int, size: int, value: int, pc: int) -> bytes:
    if OPCODES[op][1] == "rel":
        offset = value - (pc + 2)
        if not -128 <= offset < 128:
            raise AssemblyError(f"branch at ${pc:04X} out of range")
        return bytes((offset & 0xFF,))
    return (value & (0xFF if size == 1 else 0xFFFF)).to_bytes(size, "little")


def _pass(tree, symbols: dict, labels: dict, equ: dict, final: bool) -> tuple:
    """One pass: ``(memory, end, labels defined, env, instruction addresses)``."""
    env = _Env(dict(symbols) | labels, dict(equ))
    env.passed = set(symbols)
    stack, mem, pc, new, ops = [(tree, 0)], {}, 0, {}, set()

    def value(expr):
        have = env.value(expr)
        if have is None and final:
            raise AssemblyError(f"unknown {env.unknowns(expr)} in {expr}")
        return have

    def emit(data):
        nonlocal pc
        for byte in data:
            mem[pc] = byte
            pc += 1

    while stack:
        nodes, i = stack.pop()
        if i == len(nodes):
            continue
        stack.append((nodes, i + 1))
        node = nodes[i]
        kind = node[0]
        if kind == "?":
            taken = env.value(node[1])
            if taken is None:
                raise AssemblyError(f"undecided condition {node[1]}")
            stack.append((node[2] if taken else node[3], 0))
        elif kind == "L":
            new[node[1]] = env.sym[node[1]] = pc
            env.passed.add(node[1])
        elif kind == "E":
            env.equ[node[1]] = node[2]
            env.sym.pop(node[1], None)
            env.passed.add(node[1])
        elif kind == "O":
            pc = value(node[1])
        elif kind == "B":
            emit((value(item) or 0) & 0xFF for item in node[1])
        elif kind == "F":
            emit(bytes(value(node[1])))
        else:
            mnem, mode, expr = node[1:]
            have = None if expr is None else value(expr)
            op, size = _encode(mnem, mode, have, final)
            arg = _operand(op, size, have, pc) if final and size else bytes(size)
            ops.add(pc)
            emit(bytes((op,)) + arg)
    return mem, pc, new, env, ops


def referenced(nodes, out=None) -> set:
    """Every symbol the source tree ``nodes`` names."""
    out = set() if out is None else out
    for node in nodes:
        if node[0] == "?":
            _names(node[1], out)
            referenced(node[2], out)
            referenced(node[3], out)
        elif node[0] in "EI":
            _names(node[-1], out)
        elif node[0] == "B":
            for item in node[1]:
                _names(item, out)
    return out


def _names(tree, out: set) -> None:
    if isinstance(tree, str):
        out.add(tree)
    elif isinstance(tree, tuple):
        for part in tree[1:]:
            _names(part, out)


def assemble(name: str, source: tuple, symbols: dict) -> tuple:
    """``(code, match, entries)``: source ``(tree, rewritten)`` assembled with
    ``symbols`` (relocator defines, ``base``, ``zpbase`` and the data labels it
    reads); ``entries`` are the labels on instructions, in address order."""
    tree, _ = source
    symbols = GRELOC | dict(symbols)
    labels: dict = {}
    equ: dict = {}
    for _ in range(_PASSES):
        _, _, new, env, _ = _pass(tree, symbols, labels, equ, False)
        equ = env.equ
        if new == labels:
            break
        labels = new
    else:
        raise AssemblyError(f"{name}: labels do not settle")
    mem, end, new, env, ops = _pass(tree, symbols, labels, equ, True)
    start = symbols["base"]
    code = bytes(mem.get(a, 0) for a in range(start, end))
    equ = {k: env.value(v) for k, v in env.equ.items()}
    found = {k: v for k, v in equ.items() if v is not None} | env.sym
    entries = tuple(sorted(ops.intersection(new.values())))
    return code, Match(name, start, end, found, {}, frozenset(new)), entries
