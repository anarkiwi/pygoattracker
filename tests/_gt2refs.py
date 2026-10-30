"""The GoatTracker 2 playroutine sources as match trees, built at test time.

Each release's playroutine sources, read from the pinned release archives
(:data:`tests._fixture_cache.GT2_ARCHIVES`), are parsed into the node trees
:mod:`tests._gt2match` matches a tune's code against; equal trees are kept once.
"""

from __future__ import annotations

import io
import re
import zipfile
from functools import lru_cache

from tests._fixture_cache import gt2_archive

ARCHIVES = (
    "gt2_206.zip",
    "gt2_235.zip",
    "gt2_255.zip",
    "gt2_261.zip",
    "gt2_265.zip",
    "gt2_268.zip",
    "gt2_273.zip",
    "gt2_276.zip",
    "gt2_sf11.zip",
    "gt2stereo_259.zip",
    "gt2stereo_274.zip",
    "gt2stereo_276.zip",
    "gt2stereo_277.zip",
)

_STORES = {"STA", "STX", "STY", "INC", "DEC"}
_BRANCHES = {"BCC", "BCS", "BEQ", "BNE", "BMI", "BPL", "BVC", "BVS"}
_SHIFTS = {"ASL", "LSR", "ROL", "ROR"}
_OPCODES = set(
    "ADC AND ASL BCC BCS BEQ BIT BMI BNE BPL BRK BVC BVS CLC CLD CLI CLV CMP CPX "
    "CPY DEC DEX DEY EOR INC INX INY JMP JSR LDA LDX LDY LSR NOP ORA PHA PHP PLA "
    "PLP ROL ROR RTI RTS SBC SEC SED SEI STA STX STY TAX TAY TSX TXA TXS TYA".split()
)
_INDEXED = {("LDY", "memy"): "memx", ("STY", "memy"): "memx", ("LDX", "memx"): "memy"}
_TOKEN = re.compile(
    r"\s*(\$[0-9a-fA-F]+|%[01]+|\d+|\.DEFINED|[A-Za-z_][A-Za-z_0-9]*"
    r"|==|!=|<=|>=|&&|\|\||<<|>>|[-+*/%&|^()<>!,#])"
)
_BINARY = (
    ("||",),
    ("&&",),
    ("|",),
    ("^",),
    ("&",),
    ("==", "!="),
    ("<", ">", "<=", ">="),
    ("<<", ">>"),
    ("+", "-"),
    ("*", "/", "%"),
)
_DATA = re.compile(r"^(\.BYTE|dc\.b)\b(.*)$", re.I)
_FILL = re.compile(r"^ds\.b\s+([^,\s]+)", re.I)
_EQU = re.compile(r"^([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.+)$")
_LABEL = re.compile(r"^([A-Za-z_][A-Za-z_0-9]*):(.*)$")
_IF = re.compile(r"^\.IF\s*(\(.*)$", re.I)


class _Expr:
    """A precedence-climbing parser of one assembler expression."""

    def __init__(self, text: str):
        self.toks, self.i, pos = [], 0, 0
        text = text.strip()
        while pos < len(text):
            m = _TOKEN.match(text, pos)
            if m is None:
                raise ValueError(f"bad expression {text!r}")
            self.toks.append(m.group(1))
            pos = m.end()

    def peek(self):
        """The next token, None at the end."""
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self):
        """Consume the next token."""
        tok = self.peek()
        self.i += 1
        return tok

    def parse(self, level=0):
        """A binary expression of precedence ``level`` and above."""
        if level == len(_BINARY):
            return self.unary()
        left = self.parse(level + 1)
        while self.peek() in _BINARY[level]:
            op = self.take()
            left = (op, left, self.parse(level + 1))
        return left

    def unary(self):
        """A unary expression or atom."""
        tok = self.take()
        if tok in ("<", ">", "-", "!"):
            return ("u" + tok, self.unary())
        if tok == "(":
            inner = self.parse()
            if self.take() != ")":
                raise ValueError("unbalanced parenthesis")
            return inner
        if tok == ".DEFINED":
            self.take()
            name = self.take()
            self.take()
            return ("D", name)
        if tok.startswith("$"):
            return int(tok[1:], 16)
        if tok.startswith("%"):
            return int(tok[1:], 2)
        if tok.isdigit():
            return int(tok)
        return tok


def expr(text: str):
    """The expression tree of ``text``."""
    parser = _Expr(text)
    tree = parser.parse()
    if parser.peek() is not None:
        raise ValueError(f"trailing {parser.peek()!r} in {text!r}")
    return tree


def _items(text: str) -> tuple:
    """The comma-separated expressions of a data directive."""
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    depth, start, out = 0, 0, []
    for k, ch in enumerate(text + ","):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and not depth:
            out.append(expr(text[start:k]))
            start = k + 1
    return tuple(out)


def _operand(mnem: str, text: str):
    """``(mode, expression)`` of an instruction's operand text."""
    text = text.strip()
    low = text.replace(" ", "").lower()
    if not text or (mnem in _SHIFTS and low == "a"):
        return ("acc" if mnem in _SHIFTS else "impl"), None
    if mnem in _BRANCHES:
        return "rel", expr(text)
    if text.startswith("#"):
        return "imm", expr(text[1:])
    if low.startswith("(") and low.endswith("),y"):
        return "indy", expr(text[1 : text.rindex(")")])
    if low.startswith("(") and low.endswith(",x)"):
        return "indx", expr(text[1 : text.rindex(",")])
    if mnem == "JMP" and low.startswith("(") and low.endswith(")"):
        return "ind", expr(text[1:-1])
    for suffix, mode in ((",x", "memx"), (",y", "memy")):
        if low.endswith(suffix):
            return _INDEXED.get((mnem, mode), mode), expr(text[: text.rindex(",")])
    return "mem", expr(text)


def _statements(text: str):
    """Each source line as ``(label, statement)``, comments stripped."""
    for raw in text.replace("\r", "").split("\n"):
        line = raw.split(";", 1)[0].rstrip()
        if not line.strip():
            continue
        label = None
        if not line[0].isspace():
            m = _LABEL.match(line)
            if m:
                label, line = m.group(1), m.group(2)
            elif not _EQU.match(line):
                label, _, line = line.partition(" ")
        yield label, line.strip()


def _split_if(stmt: str):
    """``(condition, rest)`` of a ``.IF`` statement, else None."""
    m = _IF.match(stmt)
    if m is None:
        return None
    text, depth = m.group(1), 0
    for k, ch in enumerate(text):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if not depth:
            return expr(text[: k + 1]), text[k + 1 :].strip()
    raise ValueError(f"unbalanced condition {stmt!r}")


def _statement(stmt: str, relocated: set):
    """The node of one statement (None: nothing the code holds)."""
    word = stmt.split(None, 1)[0]
    rest = stmt[len(word) :]
    if word.lower() == "processor":
        return None
    if word.lower() in ("org", ".org"):
        return ("O", "base")
    m = _DATA.match(stmt)
    if m:
        return ("B", _items(m.group(2)))
    m = _FILL.match(stmt)
    if m:
        return ("F", expr(m.group(1)))
    m = _EQU.match(stmt)
    if m:
        return None if m.group(1) in relocated else ("E", m.group(1), expr(m.group(2)))
    mnem = word.upper()
    return ("I", mnem, *_operand(mnem, rest))


def _symbols(tree, out: set) -> set:
    """Every symbol name ``tree`` mentions."""
    if isinstance(tree, str):
        out.add(tree)
    elif isinstance(tree, tuple):
        for part in tree[1:]:
            _symbols(part, out)
    return out


def _relocated(lines) -> set:
    """Equates of plain numbers used as addresses: the V2.0x virtual addresses the
    relocator rewrites, left unknown."""
    addressed = set()
    for _, stmt in lines:
        word = stmt.split(None, 1)[0] if stmt else ""
        rest = stmt[len(word) :].strip()
        if word.upper() in _OPCODES and rest and not rest.startswith("#"):
            _symbols(_operand(word.upper(), rest)[1], addressed)
    out = set()
    for _, stmt in lines:
        m = _EQU.match(stmt)
        if m and m.group(1) in addressed and isinstance(expr(m.group(2)), int):
            out.add(m.group(1))
    return out


def _rewritten(nodes, out: dict) -> dict:
    """``{label: offsets}`` of the code bytes the source's stores rewrite."""
    for node in nodes:
        if node[0] == "?":
            _rewritten(node[2], out)
            _rewritten(node[3], out)
        elif node[0] == "I" and node[1] in _STORES:
            tree = node[3]
            if isinstance(tree, tuple) and tree[0] == "+" and isinstance(tree[2], int):
                if isinstance(tree[1], str):
                    out.setdefault(tree[1], set()).add(tree[2])
    return out


def parse(text: str) -> tuple:
    """``(tree, rewritten)``: the node tree of one playroutine source and the
    operand bytes its stores rewrite (``{label: offsets}``)."""
    lines = list(_statements(text))
    relocated = _relocated(lines)
    root: list = []
    stack = [root]
    for label, stmt in lines:
        if label:
            stack[-1].append(("L", label))
        if not stmt:
            continue
        cond = _split_if(stmt)
        if cond is not None and cond[1].upper().endswith(".ENDIF"):
            node = _statement(cond[1][: -len(".ENDIF")].strip(), relocated)
            stack[-1].append(["?", cond[0], [node] if node else [], []])
        elif cond is not None:
            stack[-1].append(["?", cond[0], [], []])
            stack.append(stack[-1][-1][2])
        elif stmt.upper() == ".ELSE":
            stack.pop()
            stack.append(stack[-1][-1][3])
        elif stmt.upper() == ".ENDIF":
            stack.pop()
        elif (node := _statement(stmt, relocated)) is not None:
            stack[-1].append(node)
    tree = _freeze(root)
    return tree, {k: tuple(sorted(v)) for k, v in sorted(_rewritten(tree, {}).items())}


def _freeze(nodes) -> tuple:
    """``nodes`` as nested tuples."""
    return tuple(
        ("?", n[1], _freeze(n[2]), _freeze(n[3])) if n[0] == "?" else tuple(n)
        for n in nodes
    )


def _sources(data: bytes):
    """``(member, text)`` of every GoatTracker 2 playroutine source in a zip
    (nested zips included)."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in sorted(zf.namelist()):
            if name.lower().endswith(".zip"):
                yield from _sources(zf.read(name))
            elif (
                re.search(r"(^|/)(alt)?player\d?b?\.s$", name) and "MACOSX" not in name
            ):
                text = zf.read(name).decode("latin-1")
                if "GoatTracker V2" in text[:400]:
                    yield name, text


@lru_cache(maxsize=None)
def sources() -> dict:
    """``{name: (tree, rewritten)}`` of every distinct playroutine source."""
    trees = {}
    for archive in ARCHIVES:
        for member, text in _sources(gt2_archive(archive)):
            tree = parse(text)
            if tree not in trees.values():
                trees[f"{archive[:-4]}/{member.rsplit('/', 1)[-1][:-2]}"] = tree
    return trees
