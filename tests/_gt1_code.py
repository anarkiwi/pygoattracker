"""GoatTracker V1.x playroutine code for tests, never committed: the archive
builds assembled from the fixture-cached sources, the others extracted from a
pinned HVSC tune of that release.
"""

from __future__ import annotations

import io
import re
import zipfile
from functools import lru_cache

from pysidtracker import SidImage, asm6502
from pysidtracker.testing import resolve_tune

from pygoattracker.gt1 import extract_code, find_player, register_code
from pygoattracker._gt1_builds import PLAYERS
from tests._fixture_cache import gt1_archive

_V153 = ("goattrk.zip", "src/player1.s")
_V14B = "goattracker_v1.4b.zip"
_V125 = "goatold.zip"
# name -> (archive, member) of each build its archive holds as source.
ARCHIVE = {
    "1.53": _V153,
    "1.4b": (_V14B, "SRC/PLAYER1.S"),
    "1.4b-hr1": (_V14B, "SRC/PLAYER4.S"),
    "1.4b-multi": (_V14B, "SRC/PLAYER3.S"),
    "11.1": (_V125, "SRC/PLAYER1.S"),
    "11.2": (_V125, "SRC/PLAYER2.S"),
    "11.3": (_V125, "SRC/PLAYER3.S"),
    "11.4": (_V125, "SRC/PLAYER4.S"),
    "11.5": (_V125, "SRC/PLAYER5.S"),
}
# name -> an HVSC tune packed with that build.
TUNES = {
    "1.52": "DEMOS/A-F/Beautiful_Day.sid",
    "1.51": "MUSICIANS/F/Finnr/H-Fusion.sid",
    "1.50b": "MUSICIANS/D/Dexter/Menace47.sid",
    "1.50": "MUSICIANS/B/Bayliss_Richard/Ann_Gong.sid",
    "0.96": "MUSICIANS/C/Cadaver/GoatTracker_example_MW1_title.sid",
    "1.01": "DEMOS/G-L/Kaori_360.sid",
    "1.1": "MUSICIANS/H/Hoffmann_Michal/Disco_Fever.sid",
    "1.15": "MUSICIANS/J/Jean_Nine/Kaakkuri_Beats.sid",
    "1.16": "DEMOS/G-L/Improper_C0nnections.sid",
    "1.21": "MUSICIANS/H/Hukka/Plaah.sid",
    "1.22": "MUSICIANS/H/Hukka/Bee.sid",
}


def _source(archive: str, member: str) -> str:
    with zipfile.ZipFile(io.BytesIO(gt1_archive(archive))) as zf:
        return zf.read(member).decode("latin-1").replace("\r\n", "\n")


_CONST = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*(\$[0-9a-fA-F]+|\d+)\s*(;.*)?$")
_DEC = re.compile(r"(?<![\w$%.+\-])(\d+)")
_ASSERT = re.compile(
    r"^\(\s*(\w+)\s*&\s*\$([0-9a-fA-F]+)\s*\)\s*!=\s*\(\s*(\w+)\s*&\s*\$([0-9a-fA-F]+)\s*\)$"
)


def _adapt_dasm(src: str) -> tuple[str, list, int]:
    """dasm source to plain asm6502 statements, plus its ``if``/``err`` asserts."""
    org, constants, asserts, out, in_if = 0, {}, [], [], False
    for raw in src.splitlines():
        code, _, comment = raw.partition(";")
        strip = code.strip()
        if not strip:
            continue
        if in_if and re.match(r"^endif\b", strip):
            in_if = False
            continue
        if in_if and re.match(r"^err\b", strip):
            continue
        if re.match(r"^if\b", strip):
            if in_if:
                raise SystemExit(f"nested if-block: {strip!r}")
            in_if = True
            asserts.append(strip[2:].strip())
            continue
        if _CONST.match(raw):
            match = _CONST.match(raw)
            value = match.group(2)
            hexnum = value.startswith("$")
            constants[match.group(1)] = int(
                value[1:] if hexnum else value, 16 if hexnum else 10
            )
            continue
        if re.match(r"^processor\s*6502\b", strip):
            continue
        if re.match(r"^org\s*\$([0-9a-fA-F]+)", strip):
            org = int(re.match(r"^org\s*\$([0-9a-fA-F]+)", strip).group(1), 16)
            continue
        code = re.sub(r"\bdc\.b\b", ".byte", code, flags=re.I)
        code = re.sub(r"\bdc\.w\b", ".word", code, flags=re.I)
        body = re.sub(r"^(?:[A-Za-z_][\w.]*\s*:)+\s*", "", code.strip())
        storage = re.match(
            r"^ds\.[bw]\s*(\d+)(?:[ \t]*,[ \t]*(\S+))?[ \t]*$", body, re.I
        )
        if storage:
            label = code.strip()[: len(code.strip()) - len(body)].strip()
            directive = ".byte" if body.startswith("ds.b") else ".word"
            fill = ",".join([storage.group(2) or "0"] * int(storage.group(1)))
            code = (f"{label}: " if label else "") + f"{directive} {fill}"
        for name in sorted(constants, key=len, reverse=True):
            low, high = constants[name] & 0xFF, constants[name] >> 8
            code = re.sub(rf"(?<![A-Za-z0-9_])<\s*{name}\b", f"${low:02x}", code)
            code = re.sub(rf"(?<![A-Za-z0-9_])>\s*{name}\b", f"${high:02x}", code)
            code = re.sub(
                rf"(?<![A-Za-z0-9_<>]){name}\b", f"${constants[name]:04x}", code
            )
        code = _DEC.sub(lambda m: f"${int(m.group(1)):02x}", code)
        out.append(code + (f";{comment}" if comment else ""))
    if in_if:
        raise SystemExit("unterminated if-block")
    return "\n".join(out), asserts, org


_ADJ = re.compile(
    r"(?<![\w$<>])(\$[0-9a-fA-F]+|[<>]?[A-Za-z_]\w*)[ \t]*([+-])[ \t]*(\d+)\b"
)


def _adj_value(prefix: str, base: str, sign: str, amount: int, labels: dict) -> str:
    if base.startswith("$"):
        value = int(base[1:], 16)
    else:
        name = base[1:] if base[:1] in "<>" else base
        if name not in labels:
            raise SystemExit(f"undefined label {name!r}")
        value = labels[name]
    value = (value + (amount if sign == "+" else -amount)) & 0xFFFF
    if prefix == ">":
        return f"${value >> 8 & 0xFF:02x}"
    if prefix == "<":
        return f"${value & 0xFF:02x}"
    return f"${value:04x}"


def assemble(src: str) -> tuple[bytes, dict]:
    """Assemble ``src`` with the jennings assembler; return code and labels."""
    text, asserts, org = _adapt_dasm(src)
    text = re.sub(r";.*", "", text)
    phase1 = _ADJ.sub(lambda m: m.group(1), text)
    _, labels = asm6502.assemble(phase1, org)
    phase2 = _ADJ.sub(
        lambda m: _adj_value(
            m.group(1)[:1] if m.group(1)[:1] in "<>" else "",
            m.group(1)[1:] if m.group(1)[:1] in "<>" else m.group(1),
            m.group(2),
            int(m.group(3)),
            labels,
        ),
        text,
    )
    code, labels = asm6502.assemble(phase2, org)
    for cond in asserts:
        match = _ASSERT.match(cond)
        if match is None:
            raise SystemExit(f"cannot evaluate dasm assert: {cond!r}")
        side = [
            (labels[match.group(1)] & int(match.group(2), 16)),
            (labels[match.group(3)] & int(match.group(4), 16)),
        ]
        if side[0] != side[1]:
            raise SystemExit(f"assembly assert failed: {cond}")
    return code, labels


def source_of(name: str) -> str:
    """The dasm source of archive build ``name``."""
    return _source(*ARCHIVE[name])


@lru_cache(maxsize=None)
def code_of(name: str) -> bytes:
    """The assembled code of playroutine ``name``."""
    if name in ARCHIVE:
        code, _ = assemble(source_of(name))
        return code
    image = SidImage.from_bytes(resolve_tune(TUNES[name]).read_bytes())
    found = find_player(image.mem)
    assert found is not None and found.ref.name == name, TUNES[name]
    return extract_code(image.mem, found)


def register_all() -> None:
    """Register every build's code with :func:`pygoattracker.gt1.register_code`."""
    for name in PLAYERS:
        register_code(name, code_of(name))
