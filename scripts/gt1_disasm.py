"""Disassemble a GoatTracker V1.x reference playroutine with its labels.

python scripts/gt1_disasm.py 1.53 > player153.txt
"""

import argparse

from pysidtracker import SidImage
from pysidtracker.disasm import disassemble

from pygoattracker.gt1 import PLAYER_ORG, reference


def listing(name: str) -> list:
    """``address  bytes  text`` lines of reference ``name`` at ``PLAYER_ORG``."""
    ref = reference(name)
    image = SidImage.from_prg(PLAYER_ORG.to_bytes(2, "little") + ref.code)
    labels = {f"{k}": PLAYER_ORG + v for k, v in ref.labels.items()}
    names = {v: k for k, v in labels.items()}
    out = []
    for addr, raw, text in disassemble(
        image, PLAYER_ORG, PLAYER_ORG + ref.code_end, labels
    ):
        label = names.get(addr, "")
        out.append(f"{addr:04X} {raw.hex(' '):9} {label:18} {text}")
    return out


def main() -> None:
    """Print the listing of each named reference."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="+")
    for name in parser.parse_args().names:
        print("\n".join(listing(name)))


if __name__ == "__main__":
    main()
