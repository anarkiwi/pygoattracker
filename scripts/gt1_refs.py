#!/usr/bin/env python3
"""Regenerate ``src/pygoattracker/_gt1_refs.py`` from the GoatTracker 1.x sources.

Assembles each playroutine with the jennings assembler (``pysidtracker.asm6502``)
from the fixture-cached V1.53 and V1.25 (musicroutine 11.x) archives; V1.50..V1.52
are V1.53 with the :data:`VARIANTS` edits and the V0.96..V1.22 standard
playroutines are musicroutine 11.1 with the :data:`PRE125` edits. ``--check``
exits 1 when the module is stale.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from pathlib import Path

import black
from pysidtracker import asm6502

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests._fixture_cache import gt1_archive  # noqa: E402

OUT = REPO / "src" / "pygoattracker" / "_gt1_refs.py"

LABELS = (
    "init",
    "play",
    "mt_filttime",
    "mt_filtstep",
    "mt_filtcutoffadd",
    "mt_d416reg",
    "mt_d417reg",
    "mt_d418reg",
    "mt_volume",
    "mt_chnloop",
    "mt_timecounter",
    "mt_funktempo",
    "mt_chnwave",
    "mt_freqtblhi",
    "mt_freqtbllo",
    "playflag",
    "mt_filtcutoff",
    "mt_filtctrl",
    "mt_filttype",
    "mt_chnnote",
    "mt_chnsongadrlo",
    "mt_hrtimer",
    "mt_chnfreqlo",
    "mt_chnfreqhi",
    "mt_chnnewnote",
    "mt_chncommand",
    "mt_chncmddata",
    "mt_chninstnum",
    "mt_chnwavetbl",
    "mt_chnpulse",
    "mt_chnpulsedir",
    "mt_chnarpcount",
    "mt_chnvibcount",
    "mt_chnsongptr",
    "mt_chnsongadrhi",
    "mt_chnpattptr",
    "mt_chnpattnum",
    "mt_chntick",
    "mt_chntempo",
    "mt_chnnext",
)

_PULSE_153 = """                beq mt_skippulse
                and #$f0
                sta mt_chnpulsedir,x
                sta $d402,x
                lda mt_instpulse,y
"""
_PULSE_152 = """                beq mt_skippulse
                pha
                and #$f0
                sta mt_chnpulsedir,x
                sta $d402,x
                pla
"""
_FILT_153 = """                tya
                beq mt_sfsnonext
                lda mt_filttbl+3,y
"""
_FILT_150 = """                lda mt_filttbl+3,y
"""
_SEQ_153 = """mt_trans:       cmp #LOOPSONG
                bcc mt_nosongloop
                lda (mt_temp1),y
                tay
                jmp mt_seqloop
mt_nosongloop:  sbc #TRANSUP-1
                sta mt_chntrans,x
                jmp mt_seqloop
mt_seqdone:     sta mt_chnpattnum,x
                tya
                sta mt_chnsongptr,x
"""
_SEQ_150 = """mt_trans:       sbc #TRANSUP-1
                sta mt_chntrans,x
                jmp mt_seqloop
mt_seqdone:     sta mt_chnpattnum,x
                lda (mt_temp1),y
                cmp #LOOPSONG
                tya
                bcc mt_seqdone3
                iny
                lda (mt_temp1),y
mt_seqdone3:    sta mt_chnsongptr,x
"""
_ZP_153 = """                pla
                sta mt_temp2                    ;Restore zeropage
                pla
                sta mt_temp1
"""
_ZP_150 = """                pla
                sta mt_temp1
                pla
                sta mt_temp2
"""

_FUNK_153 = """                lda mt_filttbl+2,y              ;Use filter0 for funktempo
"""
_FUNK_150 = """                lda mt_funktempo,y
"""
_TIME_153 = """mt_timecounter: dc.b 0
"""
_TIME_150 = """mt_funktempo:   dc.b 0,0
mt_timecounter: dc.b 0
"""

_NEWNOTE_123 = """                sta mt_chnarpcount,x
                sta mt_chnvibcount,x
                ldy mt_chninstnum,x
                lda mt_instfilter,y
                beq mt_nofilterchange
                sta mt_filtcutoff+1
                asl
                asl
                asl
                asl
                sta mt_filttype+1
mt_nofilterchange:
                lda mt_instpulse,y
                beq mt_skippulse
                sta mt_chnpulse,x
                sta $d402,x
                sta $d403,x
                lda #$80
                bne mt_skippulse2
mt_skippulse:   lda mt_chnpulsedir,x
                ora #$80
mt_skippulse2:  sta mt_chnpulsedir,x
                jmp mt_nextchn
"""
_NEWNOTE_122 = """                sta mt_chnpulsedir,x
                sta mt_chnarpcount,x
                sta mt_chnvibcount,x
                ldy mt_chninstnum,x
                lda mt_instpulse,y
                beq mt_skippulse
                sta mt_chnpulse,x
                sta $d402,x
                sta $d403,x
mt_skippulse:   lda mt_instfilter,y
                beq mt_nofilterchange
                sta mt_filtcutoff+1
                asl
                asl
                asl
                asl
                sta mt_filttype+1
mt_nofilterchange:
                jmp mt_nextchn
"""
_DIR_123 = """                bpl mt_noadsrinit
                and #$7f
"""
_DIR_122 = """                bpl mt_noadsrinit
                lda #$00
"""
_CLC_122 = """mt_play:        clc
mt_filtcutoff:"""
_CLC_121 = """mt_play:
mt_filtcutoff:"""
_INIT_122 = """                sta mt_chnpulsedir,x
                sta $d404,x
                lda #$05
                sta mt_chntick,x
                sta mt_chntempo,x
                sta mt_chnnewnote,x
"""
_INIT_121 = """                sta mt_chnpulsedir,x
                lda #$05
                sta mt_chntick,x
                sta mt_chntempo,x
                sta mt_chnnewnote,x
                lda #$08
                sta $d404,x
"""
_INIT_116 = """                lda #$05
                sta mt_chntick,x
                sta mt_chntempo,x
                sta mt_chnnewnote,x
                lda #$08
                sta mt_chnwave,x
                sta $d404,x
"""
_PULSE_116 = """                beq mt_skippulse
                sta mt_chnpulse,x
                sta $d402,x
                sta $d403,x
mt_skippulse:   lda mt_instfilter,y
"""
_PULSE_115 = """                sta mt_chnpulse,x
                sta $d402,x
                sta $d403,x
                lda mt_instfilter,y
"""
_ORDER_115 = """                cmp #LOOPSONG
                bcc mt_noloopsong
                iny
                lda (mt_temp1),y
                tay
                lda (mt_temp1),y
mt_noloopsong:  sta mt_chnpattnum,x
                iny
                tya
                sta mt_chnsongptr,x
                lda mt_chnnewnote,x
"""
_ORDER_111 = """                cmp #LOOPSONG-1
                beq mt_repeatlastpattern
                bcc mt_noloopsong
                ldy #$00
                lda (mt_temp1),y
mt_noloopsong:  sta mt_chnpattnum,x
                iny
                tya
                sta mt_chnsongptr,x
mt_repeatlastpattern:
                lda mt_chnnewnote,x
"""
_ENTRY_111 = """                jmp init
                jmp play
setvolume:      sta mt_volume+1
                rts

init:           sta playflag+1
                rts

play:           ldx #$00
playflag:       lda #$00
                bmi mt_play
                asl
                adc playflag+1
                tay
"""
_ENTRY_103 = """play:
playflag:       lda #$00
                ldx #$00
                asl
                beq mt_play
                adc playflag+1
                tay
"""
_INITEND_111 = """                sta mt_chnpattptr,x
                sta playflag+1
                lda mt_chnnext,x
                tax
                bne mt_initloop
                sta $d415
                sta mt_filtctrl+1
                sta mt_filtcutoffadd+1
                rts
"""
_INITEND_103 = """                sta mt_chnpattptr,x
                lda mt_chnnext,x
                tax
                bne mt_initloop
                sta $d415
                sta mt_filtctrl+1
                sta mt_filtcutoffadd+1
                sta playflag+1
                rts
"""

_V153 = ("goattrk.zip", "src/player1.s")
_V125 = "goatold.zip"
_V093 = ("goattrk093.zip", "src/PLAYER.S")
_PULSE = (_PULSE_153, _PULSE_152)
_FILT = (_FILT_153, _FILT_150)
_FUNK = ((_FUNK_153, _FUNK_150), (_TIME_153, _TIME_150))

# name -> (archive, member, packer author-info skip range, source edits).
VARIANTS = {
    "1.53": (*_V153, (16, 64), ()),
    "1.52": (*_V153, (16, 64), (_PULSE,)),
    "1.51": (*_V153, (16, 64), (_PULSE, "oldfreq")),
    "1.50b": (*_V153, (16, 64), (_PULSE, _FILT, *_FUNK, "oldfreq")),
    "1.50": (
        *_V153,
        (16, 64),
        (
            _PULSE,
            _FILT,
            *_FUNK,
            (_SEQ_153, _SEQ_150),
            (_ZP_153, _ZP_150),
            "oldfreq",
        ),
    ),
}
_PRE123 = ((_NEWNOTE_123, _NEWNOTE_122), (_DIR_123, _DIR_122))
_PRE122 = (*_PRE123, (_CLC_122, _CLC_121), (_INIT_122, _INIT_121))
_PRE121 = (*_PRE122, (_INIT_121, _INIT_116))
_PRE116 = (*_PRE121, (_PULSE_116, _PULSE_115))
_PRE115 = (*_PRE116, (_ORDER_115, _ORDER_111))
_PRE111 = (*_PRE115, (_ENTRY_111, _ENTRY_103), (_INITEND_111, _INITEND_103), "data093")

# name -> musicroutine 11.1 source edits, by the editor release that brought them.
PRE125 = {
    "0.96": (*_PRE111, "freq093"),
    "1.01": _PRE111,
    "1.1": _PRE115,
    "1.15": _PRE116,
    "1.16": _PRE121,
    "1.21": _PRE122,
    "1.22": _PRE123,
}
MUSICROUTINE11 = {
    "11.1": (_V125, "SRC/PLAYER1.S", None),
    "11.2": (_V125, "SRC/PLAYER2.S", None),
    "11.3": (_V125, "SRC/PLAYER3.S", (17, 64)),
    "11.4": (_V125, "SRC/PLAYER4.S", (32, 64)),
    "11.5": (_V125, "SRC/PLAYER5.S", (17, 65)),
}


def _source(archive: str, member: str) -> str:
    with zipfile.ZipFile(io.BytesIO(gt1_archive(archive))) as zf:
        return zf.read(member).decode("latin-1").replace("\r\n", "\n")


def _freq_block(src: str) -> str:
    """The ``mt_freqtblhi``/``mt_freqtbllo`` tables of a playroutine source."""
    match = re.search(
        r"^mt_freqtblhi:.*?^mt_freqtbllo:.*?(?=\n\s*\n|\n\S|\Z)", src, re.S | re.M
    )
    return match.group(0)


def _patched(src: str, edits) -> str:
    for edit in edits:
        if edit == "oldfreq":
            old = _freq_block(src)
            new = _freq_block(_source(_V125, "SRC/PLAYER3.S"))
        elif edit == "freq093":
            old, new = _freq_block(src), _freq_block(_source(*_V093))
        elif edit == "data093":
            v093 = _source(*_V093)
            old = src[src.index("mt_tick0cmdtbllo:") :]
            new = v093[v093.index("mt_chnnote:") :]
        else:
            old, new = edit
        if src.count(old) != 1:
            raise SystemExit(f"source edit does not apply uniquely: {old[:40]!r}")
        src = src.replace(old, new)
    return src


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


def build() -> str:
    """Render the generated ``_gt1_refs`` module source."""
    lines = [
        '"""Assembled GoatTracker V1.x playroutines (generated by scripts/gt1_refs.py).',
        "",
        "``code`` is assembled at $1000, ``author`` the byte range the packer",
        "overwrites, ``labels`` the label offsets and ``family`` the player design.",
        '"""',
        "",
        "PLAYERS = {",
    ]
    entries = dict(VARIANTS)
    entries.update(
        {
            name: (arc, mem, auth, ())
            for name, (arc, mem, auth) in MUSICROUTINE11.items()
        }
    )
    entries.update(
        {name: (_V125, "SRC/PLAYER1.S", None, edits) for name, edits in PRE125.items()}
    )
    for name, (archive, member, author, edits) in entries.items():
        code, symbols = assemble(_patched(_source(archive, member), edits))
        labels = {lab: symbols[lab] - 0x1000 for lab in LABELS if lab in symbols}
        lines += [
            f'    "{name}": {{',
            f'        "family": "{"1.5" if name in VARIANTS else "11"}",',
            f'        "author": {author!r},',
            f'        "labels": {labels!r},',
            '        "code": bytes.fromhex(',
        ]
        hexcode = code.hex()
        lines += [
            f'            "{hexcode[p:p + 64]}"' for p in range(0, len(hexcode), 64)
        ]
        lines += ["        ),", "    },"]
    lines.append("}")
    return black.format_str("\n".join(lines) + "\n", mode=black.Mode())


def main(argv=None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only compare")
    args = parser.parse_args(argv)
    text = build()
    if args.check:
        if OUT.read_text() != text:
            print(f"{OUT} is stale; rerun scripts/gt1_refs.py", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
