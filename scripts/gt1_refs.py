#!/usr/bin/env python3
"""Regenerate ``src/pygoattracker/_gt1_refs.py`` from the GoatTracker 1.x sources.

Assembles each playroutine with ``dasm`` from the fixture-cached V1.53/V1.25
archives; V1.50..V1.52 are V1.53's source with the :data:`VARIANTS` edits.
``--check`` exits 1 when the module is stale.
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import black

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

_V153 = ("goattrk.zip", "src/player1.s")
_V125 = "goatold.zip"
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
RECOGNIZED = {
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
        else:
            old, new = edit
        if src.count(old) != 1:
            raise SystemExit(f"source edit does not apply uniquely: {old[:40]!r}")
        src = src.replace(old, new)
    return src


def assemble(src: str) -> tuple[bytes, dict]:
    """Assemble ``src`` with dasm; return the code and its symbol addresses."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "p.s").write_text(src, encoding="latin-1")
        subprocess.run(
            ["dasm", "p.s", "-op.bin", "-f3", "-sp.sym"],
            cwd=tmpdir,
            check=True,
            capture_output=True,
        )
        code = (tmpdir / "p.bin").read_bytes()
        symbols = {}
        for line in (tmpdir / "p.sym").read_text(encoding="latin-1").splitlines():
            parts = line.split()
            if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{4}", parts[1]):
                symbols[parts[0]] = int(parts[1], 16)
    return code, symbols


def build() -> str:
    """Render the generated ``_gt1_refs`` module source."""
    lines = [
        '"""Assembled GoatTracker V1.x playroutines (generated by scripts/gt1_refs.py).',
        "",
        "``code`` is assembled at $1000, ``author`` the byte range the packer",
        "overwrites and ``labels`` the label offsets; ``native`` marks a player.",
        '"""',
        "",
        "PLAYERS = {",
    ]
    entries = dict(VARIANTS)
    entries.update(
        {name: (arc, mem, auth, ()) for name, (arc, mem, auth) in RECOGNIZED.items()}
    )
    for name, (archive, member, author, edits) in entries.items():
        code, symbols = assemble(_patched(_source(archive, member), edits))
        labels = {lab: symbols[lab] - 0x1000 for lab in LABELS if lab in symbols}
        lines += [
            f'    "{name}": {{',
            f'        "native": {name in VARIANTS},',
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
