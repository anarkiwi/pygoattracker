"""A tune's GoatTracker 2 playroutine, matched against the playroutine sources.

:func:`match` walks a source tree (:func:`tests._gt2refs.sources`) over the
image's bytes depth first, taking each conditional block the bytes and the defines
chosen so far allow; a whole match gives every label's address and the defines.
"""

from __future__ import annotations

from functools import lru_cache

from jennings.opcodes import OPCODES

from pygoattracker.gt2spec import Match
from tests._gt2refs import sources

_MODES = {
    "mem": ("zp", "abs"),
    "memx": ("zpx", "absx"),
    "memy": ("zpy", "absy"),
}
_DOMAINS = {"NUMCHANNELS": (3, 2, 1, 6), "NUMSONGS": (1, 2)}
_BOOL = (0, 1)
_OPS = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a // b if b else 0,
    "%": lambda a, b: a % b if b else 0,
    "&": lambda a, b: a & b,
    "|": lambda a, b: a | b,
    "^": lambda a, b: a ^ b,
    "<<": lambda a, b: a << b,
    ">>": lambda a, b: a >> b,
    "==": lambda a, b: int(a == b),
    "!=": lambda a, b: int(a != b),
    "<": lambda a, b: int(a < b),
    ">": lambda a, b: int(a > b),
    "<=": lambda a, b: int(a <= b),
    ">=": lambda a, b: int(a >= b),
}
_UNARY = {
    "u<": lambda a: a,
    "u>": lambda a: (a >> 8) & 0xFF,
    "u-": lambda a: -a,
    "u!": lambda a: int(not a),
}


class _Env:
    """Symbol values, equates, operand-expression values and labels passed."""

    __slots__ = ("sym", "equ", "expr", "passed", "smc", "slip", "slips")

    def __init__(self, sym=None, equ=None, exprs=None, passed=None, smc=None):
        self.sym = sym if sym is not None else {}
        self.equ = equ if equ is not None else {}
        self.expr = exprs if exprs is not None else {}
        self.passed = passed if passed is not None else set()
        self.smc = smc if smc is not None else set()
        self.slip = None
        self.slips = None

    def copy(self) -> "_Env":
        """An independent copy."""
        env = _Env(
            dict(self.sym),
            dict(self.equ),
            dict(self.expr),
            set(self.passed),
            set(self.smc),
        )
        env.slip, env.slips = self.slip, self.slips
        return env

    def slipped(self, have: int, value: int) -> bool:
        """Whether ``value`` is ``have`` moved by the one offset every operand the
        relocation missed keeps (None ``slips``: none allowed); counts it."""
        if self.slips is None:
            return False
        delta = (value - have) & 0xFFFF
        if self.slip is None:
            self.slip = delta
        if delta not in (self.slip, -self.slip & 0xFFFF):
            return False
        self.slips += 1
        return True

    def value(self, tree, seen=()):
        """The value of expression ``tree``, None while a symbol in it is unknown."""
        if isinstance(tree, int):
            return tree
        if isinstance(tree, str):
            if tree in self.sym:
                return self.sym[tree]
            if tree in self.equ and tree not in seen:
                return self.value(self.equ[tree], (*seen, tree))
            return None
        op = tree[0]
        if op == "D":
            return int(tree[1] in self.passed)
        if op in _UNARY:
            arg = self.value(tree[1], seen)
            return None if arg is None else _UNARY[op](arg)
        a = self.value(tree[1], seen)
        if op in ("&&", "||"):
            stop = 0 if op == "&&" else 1
            if a is not None and bool(a) == bool(stop):
                return stop
            b = self.value(tree[2], seen)
            if b is not None and bool(b) == bool(stop):
                return stop
            return None if a is None or b is None else 1 - stop
        b = self.value(tree[2], seen)
        return None if a is None or b is None else _OPS[op](a, b)

    def unknowns(self, tree, seen=()) -> list:
        """The unknown symbols ``tree`` depends on, equates followed."""
        if isinstance(tree, str):
            if tree in self.sym:
                return []
            if tree in self.equ and tree not in seen:
                return self.unknowns(self.equ[tree], (*seen, tree))
            return [tree]
        if isinstance(tree, int) or tree[0] == "D":
            return []
        return [s for part in tree[1:] for s in self.unknowns(part, seen)]

    def bind(self, tree, value: int, mask: int) -> bool:
        """Make ``tree`` equal ``value`` (under ``mask``); False if it cannot."""
        have = self.value(tree)
        if have is not None:
            return (have - value) & mask == 0
        if isinstance(tree, str):
            if tree in self.equ:
                return self.bind(self.equ[tree], value, mask)
            self.sym[tree] = value & mask
            return True
        op = tree[0]
        if op == "u<":
            return self.bind(tree[1], value, mask)
        if op in ("+", "-"):
            a, b = self.value(tree[1]), self.value(tree[2])
            if b is not None:
                return self.bind(tree[1], value - b if op == "+" else value + b, mask)
            if a is not None:
                return self.bind(tree[2], value - a if op == "+" else a - value, mask)
        free = self.unknowns(tree)
        if len(free) == 1 and free[0] in _DOMAINS:
            for option in _DOMAINS[free[0]]:
                self.sym[free[0]] = option
                if (self.value(tree) - value) & mask == 0:
                    return True
            del self.sym[free[0]]
            return False
        key = repr(tree)
        if key in self.expr:
            return (self.expr[key] - value) & mask == 0
        self.expr[key] = value & mask
        return True


def _choices(cond, env: _Env):
    """``(taken, env)`` for each way the defines can decide ``cond``."""
    have = env.value(cond)
    if have is not None:
        return [(bool(have), env)]
    name = env.unknowns(cond)[0]
    out = []
    for option in _DOMAINS.get(name, _BOOL):
        branch = env.copy()
        branch.sym[name] = option
        out += _choices(cond, branch)
    return out


def _step(node, mem, pos: int, env: _Env, data: list, rewritten: dict):
    """Match one non-conditional node at ``pos``: the position after it, else None."""
    kind = node[0]
    if kind == "L":
        name = node[1]
        env.passed.add(name)
        env.smc.update(pos + k for k in rewritten.get(name, ()))
        have = env.sym.get(name)
        if have is not None and have != pos and env.slipped(pos, have):
            env.sym[name] = pos
        return pos if env.bind(name, pos, 0xFFFF) else None
    if kind == "E":
        name, tree = node[1], node[2]
        value = env.sym.pop(name, None)
        env.equ[name] = tree
        return pos if value is None or env.bind(tree, value, 0xFFFF) else None
    if kind == "O":
        return pos if env.bind(node[1], pos, 0xFFFF) else None
    if kind == "B":
        data.extend((pos + k, item) for k, item in enumerate(node[1]))
        return pos + len(node[1])
    if kind == "F":
        size = env.value(node[1])
        return None if size is None else pos + size
    mnem, mode, tree = node[1], node[2], node[3]
    if pos >= len(mem):
        return None
    name, have = OPCODES.get(mem[pos], ("", ""))
    if name != mnem or have not in _MODES.get(mode, (mode,)):
        return None
    if have in ("impl", "acc"):
        return pos + 1
    if pos + 1 in env.smc or pos + 2 in env.smc:
        return pos + 3 if have in ("abs", "absx", "absy", "ind") else pos + 2
    if have == "rel":
        rel = mem[pos + 1]
        target = (pos + 2 + rel - (rel >> 7 << 8)) & 0xFFFF
        return pos + 2 if env.bind(tree, target, 0xFFFF) else None
    if have in ("abs", "absx", "absy", "ind"):
        value = mem[pos + 1] | mem[pos + 2] << 8
        if env.bind(tree, value, 0xFFFF):
            return pos + 3
        known = env.value(tree)
        if known is None:
            known = env.expr.get(repr(tree))
        return pos + 3 if known is not None and env.slipped(known, value) else None
    return pos + 2 if env.bind(tree, mem[pos + 1], 0xFF) else None


def _data_holds(mem, data, env: _Env) -> bool:
    """Whether every data byte stated by label arithmetic holds that byte."""
    for pos, item in data:
        if isinstance(item, int):
            continue
        have = env.value(item)
        if have is not None and have & 0xFF != mem[pos]:
            return False
    return True


def match_tree(
    source, mem, start: int, name: str = "", furthest=None, slips=False
) -> Match | None:
    """``source`` (a ``(tree, rewritten)`` pair) matched over ``mem`` from ``start``,
    else None; ``slips`` allows operands the relocation missed (one offset off),
    ``furthest`` (a list) collects the deepest mismatch's ``(position, node)``."""
    tree, rewritten = source
    env = _Env()
    env.slips = 0 if slips else None
    stack = [(start, env, [], ((tree, 0),))]
    while stack:
        pos, env, data, cont = stack.pop()
        while cont and pos is not None:
            nodes, i = cont[-1]
            if i == len(nodes):
                cont = cont[:-1]
                continue
            node = nodes[i]
            cont = (*cont[:-1], (nodes, i + 1))
            if node[0] != "?":
                at, pos = pos, _step(node, mem, pos, env, data, rewritten)
                if pos is None and furthest is not None:
                    if not furthest or at > furthest[0][0]:
                        furthest[:] = [(at, node)]
                continue
            options = _choices(node[1], env)
            for taken, other in reversed(options[1:]):
                body = node[2] if taken else node[3]
                stack.append((pos, other.copy(), list(data), (*cont, (body, 0))))
            taken, env = options[0]
            cont = (*cont, (node[2] if taken else node[3], 0))
        if pos is not None and _data_holds(mem, data, env):
            equ = {k: env.value(v) for k, v in env.equ.items()}
            symbols = {k: v for k, v in equ.items() if v is not None} | env.sym
            return Match(
                name,
                start,
                pos,
                symbols,
                dict(env.expr),
                frozenset(env.passed),
                env.slips or 0,
            )
    return None


@lru_cache(maxsize=256)
def _match_cached(code: bytes, start: int, slips: bool) -> Match | None:
    mem = bytearray(start) + code
    for name, tree in sources().items():
        found = match_tree(tree, mem, start, name, slips=slips)
        if found is not None:
            return found
    return None


_JMP = 0x4C
_TABLE = 4


def match(mem, start: int, end: int) -> Match | None:
    """The first source whose code matches ``mem`` from ``start`` (or from an earlier
    entry of the run of jumps ``start`` is in) up to ``end``."""
    first = start
    while first >= 3 and start - first < 3 * _TABLE and mem[first - 3] == _JMP:
        first -= 3
    for slips in (False, True):
        for base in range(first, start + 1, 3):
            found = _match_cached(bytes(mem[base:end]), base, slips)
            if found is not None:
                return found
    return None
