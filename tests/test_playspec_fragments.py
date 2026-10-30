"""defMON and GoatTracker mechanism fragments on synthetic memory.

Expected bytes are derived by hand from each player's documented semantics
(pydefmon ``docs/format.md``, pygoattracker ``player.py``/``gt1player.py``).
"""

import pytest

from pathlib import Path

from pysidtracker.playspec import SpecRuntimeError, compile_program, elaborate, parse
from pysidtracker.playspec.pyexec import controls

SPECS = Path(__file__).resolve().parent.parent / "specs"


class Run:
    """A fragment bound to ``binding``, stepping a 64 KiB memory one call at a time."""

    def __init__(self, name, binding, **regs):
        self.program = elaborate(parse((SPECS / f"{name}.play").read_text()), binding)
        self.frame = compile_program(self.program)
        self.state = controls(self.program)
        self.mem = bytearray(0x10000)
        self.regs = {k[4:]: 0 for k in self.program.inputs} | regs

    def poke(self, addr, *values):
        """Store ``values`` from ``addr``."""
        self.mem[addr : addr + len(values)] = bytes(values)
        return self

    def step(self, frames=1):
        """Run ``frames`` calls."""
        for _ in range(frames):
            self.frame(
                self.mem.__getitem__, self.mem.__setitem__, self.state, None, self.regs
            )
        return self

    def peek(self, addr, count=1):
        """``count`` bytes from ``addr`` (one int for a single byte)."""
        values = list(self.mem[addr : addr + count])
        return values[0] if count == 1 else values


def test_gt2_funktempo_alternates_row_lengths():
    run = Run(
        "goattracker_tempo",
        {"tick": 0x1000, "tempo": 0x1010, "rows": 0x1020, "funk": 0x1030},
    )
    run.poke(0x1000, 1, 1, 1).poke(0x1010, 0, 5, 5).poke(0x1030, 8, 5)
    rows0, rows1 = [], []
    for frame in range(31):
        before = run.peek(0x1020, 2)
        run.step()
        after = run.peek(0x1020, 2)
        if after[0] != before[0]:
            rows0.append(frame)
        if after[1] != before[1]:
            rows1.append(frame)
    assert rows0 == [0, 9, 15, 24, 30]
    assert rows1 == [0, 6, 12, 18, 24, 30]


def test_gt2_orderlist_repeat_transpose_and_loop():
    run = Run(
        "goattracker_orderlist",
        {
            "songptr": 0x1000,
            "trans": 0x1010,
            "repeat": 0x1020,
            "pattnum": 0x1030,
            "song_lo": 0x1040,
            "song_hi": 0x1050,
        },
    )
    run.poke(0x1040, 0x00).poke(0x1050, 0x30).poke(
        0x3000, 0xE3, 0xD2, 0x05, 0x07, 0xFF, 0x00
    )
    seen = [run.step().peek(0x1030) for _ in range(5)]
    assert seen == [5, 5, 5, 7, 5]
    assert run.peek(0x1010) == 0xF3


def test_gt2_hard_restart_on_the_gate_timer_frame():
    run = Run(
        "goattracker_hardrestart",
        {
            "tick": 0x1000,
            "gatetimer": 0x1010,
            "pattptr": 0x1020,
            "pattern": 0x1030,
            "instr": 0x1040,
            "gate": 0x1050,
            "newnote": 0x1060,
            "trans": 0x1070,
            "sid": 0xD400,
            "instr_gt": 0x1100,
            "hr_ad": 0x2001,
            "hr_sr": 0x2003,
        },
    )
    run.poke(0x1000, 2, 5, 2).poke(0x1010, 2, 2, 2).poke(0x1070, 2, 0, 0).poke(
        0x1050, 0xFF, 0xFF, 0xFF
    )
    run.poke(0x1030, 0x00, 0x00, 0x10).poke(0x1033, 0x30, 0x30, 0x30)
    run.poke(0x3000, 0x30, 0x01, 0x00, 0x00).poke(0x3010, 0x30, 0x02, 0x00, 0x00)
    run.poke(0x1101, 0x00, 0x40).poke(0x2001, 0x0F).poke(0x2003, 0x00).poke(
        0xD405, 0x55, 0x66
    )
    run.step()
    assert (run.peek(0x1050), run.peek(0x1060), run.peek(0x1020)) == (0xFE, 0x32, 4)
    assert run.peek(0xD405, 2) == [0x0F, 0x00]
    assert run.peek(0x1051) == 0xFF and run.peek(0x1021) == 0
    assert run.peek(0x1052) == 0xFF and run.peek(0xD405 + 14, 2) == [0, 0]
    run.poke(0x1000, 2).poke(0x3004, 0xBE, 0, 0, 0).step()
    assert run.peek(0x1050) == 0xFE


def test_gt2_wavetable_delay_waveform_jump_and_relative_note():
    run = Run(
        "goattracker_wavetable",
        {
            "wptr": 0x1000,
            "wavetime": 0x1010,
            "wave": 0x1020,
            "note": 0x1030,
            "freq_lo_w": 0x1040,
            "freq_hi_w": 0x1050,
            "wl": 0x1100,
            "wr": 0x1200,
            "freq_lo": 0x1300,
            "freq_hi": 0x1400,
        },
    )
    run.poke(0x1100, 0x02, 0x41, 0xFF).poke(0x1200, 0x80, 0x0C, 0x02)
    run.poke(0x1300, *range(128)).poke(0x1400, *(i | 0x80 for i in range(128)))
    run.poke(0x1000, 1).poke(0x1030, 0x10)
    run.step(2)
    assert (run.peek(0x1000), run.peek(0x1010)) == (1, 2)
    run.step()
    assert (run.peek(0x1000), run.peek(0x1010), run.peek(0x1040)) == (2, 0, 0)
    run.step()
    assert (run.peek(0x1020), run.peek(0x1000)) == (0x41, 2)
    assert (run.peek(0x1040), run.peek(0x1050)) == (0x1C, 0x9C)


def test_gt2_pulse_table_set_modulate_jump_and_wrap():
    run = Run(
        "goattracker_pulse",
        {
            "pptr": 0x1000,
            "ptime": 0x1010,
            "pulse_lo": 0x1020,
            "pulse_hi": 0x1030,
            "pl": 0x1100,
            "pr": 0x1200,
        },
    )
    run.poke(0x1100, 0x88, 0x02, 0xFF).poke(0x1200, 0x40, 0xF0, 0x02).poke(0x1000, 1, 2)
    run.poke(0x1021, 0x05).poke(0x1031, 0x00)
    widths = []
    for _ in range(4):
        run.step()
        widths.append(run.peek(0x1020) | run.peek(0x1030) << 8)
    assert widths == [0x840, 0x830, 0x820, 0x810]
    assert run.peek(0x1021) | run.peek(0x1031) << 8 == 0xFC5


def test_gt2_filter_table_runs_before_the_channels():
    run = Run(
        "goattracker_filter",
        {
            "fptr": 0x1000,
            "ftime": 0x1001,
            "cutoff": 0x1002,
            "ctrl": 0x1003,
            "ftype": 0x1004,
            "fader": 0x1005,
            "fl": 0x1100,
            "fr": 0x1200,
            "sid": 0xD400,
        },
    )
    run.poke(0x1100, 0x90, 0x00, 0x03).poke(0x1200, 0xF1, 0x40, 0x10).poke(
        0x1000, 1
    ).poke(0x1005, 0x0F)
    run.step()
    assert run.peek(0xD415, 4) == [0x00, 0x40, 0xF1, 0x1F]
    cutoffs = [run.step().peek(0xD416) for _ in range(3)]
    assert cutoffs == [0x50, 0x60, 0x70] and run.peek(0x1000) == 4


def test_gt2_porta_toneporta_and_vibrato():
    run = Run(
        "goattracker_porta",
        {
            "freq_lo_w": 0x1000,
            "freq_hi_w": 0x1010,
            "note": 0x1020,
            "vibtime": 0x1030,
            "effect": 0x1040,
            "speed": 0x1050,
            "depth": 0x1060,
            "freq_lo": 0x1100,
            "freq_hi": 0x1200,
        },
    )
    run.poke(0x1040, 1, 3, 4).poke(0x1050, 0x23, 0x80, 0x10).poke(
        0x1053, 0x01, 0x00, 0x00
    )
    run.poke(0x1000, 0x00, 0x00, 0x00).poke(0x1010, 0x10, 0x05, 0x20)
    run.poke(0x1021, 2).poke(0x1102, 0x40).poke(0x1202, 0x05).poke(0x1031, 9).poke(
        0x1032, 1
    )
    run.poke(0x1062, 2)
    run.step()
    assert run.peek(0x1000) | run.peek(0x1010) << 8 == 0x1123
    assert (run.peek(0x1001) | run.peek(0x1011) << 8, run.peek(0x1031)) == (0x0540, 0)
    assert (run.peek(0x1002) | run.peek(0x1012) << 8, run.peek(0x1032)) == (0x1FF0, 3)
    run.step()
    assert (run.peek(0x1002) | run.peek(0x1012) << 8, run.peek(0x1032)) == (
        0x2000,
        0xFE,
    )


def test_gt2_multispeed_ghost_registers_lag_one_call():
    run = Run(
        "goattracker_multispeed",
        {"calls": 2, "ghost": 0x1000, "freq": 0x1010, "sid": 0xD400},
    )
    run.step()
    assert run.peek(0x1010, 6) == [2] * 6
    for chan in range(6):
        assert run.peek(0xD400 + chan // 3 * 0x20 + chan % 3 * 7) == 1


def test_gt1_operand_globals_and_arpeggio_phases():
    run = Run(
        "gt1_globals",
        {
            "filttime": 0x2001,
            "cutoffadd": 0x2003,
            "d416": 0x2005,
            "d417": 0x2007,
            "d418": 0x2009,
            "volume": 0x200B,
            "chn": 0x1000,
            "freq_lo": 0x1100,
            "freq_hi": 0x1200,
            "sid": 0xD400,
        },
    )
    run.poke(0x2001, 2).poke(0x2003, 3).poke(0x2005, 0x10).poke(0x2009, 0x1F).poke(
        0x200B, 0x0F
    )
    run.poke(0x1100, *range(128)).poke(0x1200, *(0x80 | i for i in range(128)))
    run.poke(0x1000 + 21, 0x10).poke(0x1000 + 42, 0x47).poke(0x1000 + 63, 0x80)
    notes = [run.step().peek(0x1000 + 21 * 21) for _ in range(3)]
    assert notes == [0x14, 0x17, 0x10]
    assert (
        run.peek(0xD416) == 0x10 + 3 + 3
        and run.peek(0xD418) == 0x0F
        and run.peek(0x2001) == 0
    )


@pytest.mark.parametrize("carry, cutoff", [(True, 0x24), (False, 0x23)])
def test_gt1_channel_chain_and_entry_carry(carry, cutoff):
    run = Run(
        "gt1_chain",
        {
            "cutoff": 0x1000,
            "cutoffadd": 0x1001,
            "chnnext": 0x1100,
            "visits": 0x1200,
            "order": 0x1300,
            "sid": 0xD400,
        },
        c=carry,
    )
    run.poke(0x1000, 0x20, 3).poke(0x1100, 7).poke(0x1107, 14).poke(0x110E, 0)
    run.step()
    assert run.peek(0xD416) == cutoff and run.peek(0x1300, 3) == [0, 7, 14]
    assert (run.peek(0x1200), run.peek(0x1207), run.peek(0x120E)) == (1, 1, 1)
    run.poke(0x1107, 7)
    with pytest.raises(SpecRuntimeError, match="ran past 256"):
        run.step()
