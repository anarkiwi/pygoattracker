"""Disassemble a ``.sid`` image from an address (default: the header's init and play).

python scripts/disasm_sid.py tune.sid [addr ...] [--count N]
"""

import argparse

from py65.devices.mpu6502 import MPU
from py65.disassembler import Disassembler

from pygoattracker.sid import _load_image


def disasm(mem, addr, count):
    """``count`` instructions from ``addr`` as ``(addr, text)`` pairs."""
    mpu = MPU()
    mpu.memory[:] = list(mem)
    dis = Disassembler(mpu)
    out = []
    for _ in range(count):
        length, text = dis.instruction_at(addr)
        out.append((addr, text))
        addr = (addr + length) & 0xFFFF
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sid")
    ap.add_argument("addr", nargs="*", type=lambda s: int(s, 16))
    ap.add_argument("--count", type=int, default=40)
    a = ap.parse_args(argv)
    with open(a.sid, "rb") as f:
        data = f.read()
    img = _load_image(data)
    starts = a.addr or [int.from_bytes(data[o : o + 2], "big") for o in (10, 12)]
    for start in starts:
        print(f"; ${start:04X}")
        for addr, text in disasm(img.mem, start, a.count):
            print(f"{addr:04X}  {text}")


if __name__ == "__main__":
    main()
