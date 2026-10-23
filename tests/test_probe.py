"""The build defines measured by running real packed players (HVSC tunes).

Each tune is a whole-song oracle-exact one (``test_oracle_hvsc``) or one whose
instrument classes the player's own ``cmp #FIRSTNOHRINSTR`` operands state.
"""

import dataclasses
from pathlib import Path

import pytest
from pysidtracker import SidImage
from pysidtracker.header import write_psid
from pysidtracker.testing import resolve_tune

from pygoattracker import probe, sid
from pygoattracker.packed import NONE, PackedLayout, PlayerBuild
from pygoattracker.typed import Note, Rest, Row

BUILDS = {
    # NOWAVEDELAY off without delay rows.
    "DEMOS/0-9/2_Hours_NOT_Enough.sid": dict(
        wavedelay=True, simplepulse=False, adparam=0x0F, srparam=0
    ),
    # a ghost build's stock funk tempo table.
    "MUSICIANS/R/Radiantx/Cheese_Wings.sid": dict(ghost=5332, alt=False, funk=(8, 5)),
    # notes split over short patterns; a hard restart to $FF/$F0.
    "MUSICIANS/D/Dalezy/You_Am_I-You_Are_the_RNGMNN.sid": dict(
        adparam=0xFF, srparam=0xF0
    ),
    # a play wrapper flushing its zero-page ghost.
    "MUSICIANS/B/Bop/Ewe_Woz_Ere_DX.sid": dict(wrapper=True),
    # NONORMALSPEED: realtime from a calculated-speed effect.
    "MUSICIANS/P/Psych858o/Squarish.sid": dict(realtime=False),
    # realtime from the tick 0 of a tie; no speed rows but greloc's zero row.
    "MUSICIANS/B/Bayliss_Richard/Sand_Dunes_in_Paradise.sid": dict(realtime=False),
    # a NOPULSEMOD build: the pulse optimization from alternating set rows.
    "MUSICIANS/F/Flotsam/Escherian_Stairwell.sid": dict(pulseopt=False),
    "DEMOS/G-L/Goat_City.sid": dict(wavedelay=False, pulseopt=True, deftempo=5),
    # a two-row pulse table: the pulse optimization from the gate timer's fetch.
    "MUSICIANS/M/MCH/Peep_Show.sid": dict(pulseopt=False),
    # writes the SID through its $D420 mirror.
    "DEMOS/A-F/A_Crack_in_the_Facade.sid": dict(wavedelay=False, fixed=(2, 9)),
    # no play address: greloc's ``jmp mt_play`` plays it.
    "DEMOS/G-L/Grandi.sid": dict(wavedelay=False, deftempo=5),
    "MUSICIANS/M/Mibri/Halloween-Main_Title.sid": dict(simplepulse=True),
    "MUSICIANS/N/Narciso/Burning_Madness.sid": dict(
        ghost=5003, alt=False, pulseopt=False, realtime=True
    ),
    "MUSICIANS/J/Jammer/Ryan_Went_Sling.sid": dict(
        ghost=4765, alt=True, adparam=0xFF, srparam=0, fixed=(1, 9)
    ),
    "MUSICIANS/N/Nordischsound/Monkey_Island_2-Bone_Song.sid": dict(realtime=False),
    "MUSICIANS/N/Nordischsound/Metroid-Brinstar.sid": dict(deftempo=17),
    "MUSICIANS/P/Patfield_Kent/Galaga_Start_Theme.sid": dict(adparam=0x10, srparam=0),
    "MUSICIANS/J/Joeli_Jani/Heavy_Industrial_Machinery.sid": dict(
        adparam=0xFF, srparam=0x0F, firstnohr=5
    ),
    "MUSICIANS/D/DJ_Space/Contra_Jungle_Theme.sid": dict(adparam=0x0F, srparam=0x0F),
    "DEMOS/UNKNOWN/Live_in_Mokba.sid": dict(firstnohr=10, firstlegato=12),
    "DEMOS/A-F/Datatanten_Theme.sid": dict(firstnohr=3, firstlegato=NONE),
}


def _decompiled(rel):
    path = resolve_tune(rel)
    if path is None:
        pytest.skip(f"{rel} unavailable (offline, not cached)")
    return sid.decompile_sid(Path(path).read_bytes())


@pytest.mark.parametrize("rel", BUILDS)
def test_the_build_is_what_the_player_does(rel):
    build = _decompiled(rel).info.build
    want = BUILDS[rel]
    assert {k: getattr(build, k) for k in want} == want


def test_a_ghost_build_starts_from_its_ghost_and_its_filter_immediates():
    d = _decompiled("MUSICIANS/N/Narciso/Burning_Madness.sid")
    assert d.info.state.registers == tuple(d.image.mem[5003 : 5003 + 25])
    assert (d.info.state.volume, d.info.state.funk) == (0x0F, (8, 5))


def test_a_chip_writing_build_starts_from_the_variables_init_leaves():
    d = _decompiled("MUSICIANS/M/Maktone/Struggle_Within.sid")
    state = d.info.state
    assert not state.ghost
    assert state.registers[0:2] == tuple(d.image.mem[0x1390:0x1392])
    assert (state.cutoff, state.filttype, state.volume) == (0x58, 0x10, 0x0F)
    assert probe.state(_bench(), PlayerBuild()).registers[:24] == (0,) * 24
    assert probe.state(_bench(wlen=1), PlayerBuild()) is None


def test_a_song_init_call_loads_the_channel_registers_init_leaves():
    d = _decompiled("MUSICIANS/H/Hannula_Antti/Laid_Back_Sunday.sid")
    init = dict(d.info.state.init)
    mem = d.image.mem
    for c in (0, 7, 14):
        assert (init[c], init[c + 1], init[c + 2]) == tuple(
            mem[0x13DF + c : 0x13E2 + c]
        )
        assert (init[c + 5], init[c + 6]) == tuple(mem[0x13F1 + c : 0x13F3 + c])
        assert init[c + 4] == 0
    state = _decompiled("MUSICIANS/M/Maktone/Struggle_Within.sid").info.state
    assert dict(state.init) == {4: 0, 11: 0, 18: 0, 0x15: 0}
    assert probe.state(_bench(), PlayerBuild()).init == ()


def test_a_calculated_speed_build_starts_from_the_lastnotes_init_leaves():
    d = _decompiled("MUSICIANS/L/Linus/Commando_Take_Me_to_the_Bridge_short.sid")
    assert d.info.state.lastnote == tuple(d.image.mem[0x0C70:0x0C85:7])
    assert any(d.info.state.lastnote)
    d = _decompiled("MUSICIANS/H/Hannula_Antti/Laid_Back_Sunday.sid")
    assert d.info.state.lastnote == ()
    assert probe.state(_bench(), PlayerBuild()).lastnote == ()
    assert probe.state(_bench(notes=96), PlayerBuild()).lastnote == ()


def _bench(**lay):
    """A bench over an image whose init and play only return."""
    body = bytearray(0x1000)
    body[0] = body[3] = 0x60
    image = SidImage.from_bytes(
        write_psid(load=0x1000, init=0x1000, play=0x1003, image=body)
    )
    base = dict(freq_addr=0, firstnote=0, notes=0, songtbl=0, songs=1)
    base.update(order_addrs=(0x1100,) * 3, patt_lo=0, patt_addrs=(0x1200,))
    base.update(ins_start=0x1300, num_instr=2, pulse=False, filt=False, vib=False)
    base.update(gate=True, tstart=0x1400, wlen=2, plen=0, flen=0, slen=0, zeros=0)
    base.update(lay)
    return probe.Bench(image, PackedLayout(**base), (8,), (3, 3, 3))


def test_planted_data_lands_at_the_layout_or_misses():
    bench = _bench()
    rows = (Row(Note(48), 2), Row(Rest(), None, None, 4))
    buf = bench.plant([rows], {2: {"ad": 0x12}}, {"wave": probe._WAVE})
    at = lambda a, n=1: bytes(buf.get(a + k, 0) for k in range(n))  # noqa: E731
    assert at(0x1100, 3) == bytes((0, 0xFF, 0))
    assert at(0x1200, 4) == bytes((2, 0x90, 0xFC, 0))
    assert at(0x1301) == b"\x12" and at(0x1300 + 4 * 2 + 1) == b"\x09"
    assert at(0x1400, 4) == bytes((0x51, 0xFF, 0x80, 0))
    misses = (
        lambda b: b.plant([rows] * 2),
        lambda b: b.plant([rows * 3]),
        lambda b: b.plant([rows], {3: {}}),
        lambda b: b.plant([rows], tables={"pulse": probe._SETPULSE}),
        lambda b: b.plant([rows], tables={"wave": probe._WAVE * 2}),
    )
    for miss in misses:
        with pytest.raises(probe.ProbeMiss):
            miss(bench)
    lay = dataclasses.replace(bench.lay, order_addrs=(0x1100, 0x1110, 0x1120))
    planted = probe.Bench(bench.image, lay, (8,), (3, 2, 3)).plant([rows])
    assert {0x1100, 0x1120} <= set(planted) and 0x1110 not in planted
    with pytest.raises(probe.ProbeMiss):
        probe.Bench(bench.image, lay, (8,), (2, 3, 3)).plant([rows])
    copied = probe.Bench(bench.image, lay, (8,), (3, 3, 3), copies={0x1100: [0x0900]})
    planted = copied.plant([rows])
    assert planted[0x0900] == planted[0x1100]
    two = dataclasses.replace(lay, order_addrs=tuple(range(0x1100, 0x1160, 0x10)))
    planted = probe.Bench(bench.image, two, (8,), (2,) * 3 + (3,) * 3, song=1).plant(
        [rows]
    )
    assert 0x1100 not in planted and 0x1130 in planted


def test_a_player_that_plays_nothing_shows_no_build():
    bench = _bench()
    assert probe.measure(bench) == PlayerBuild()
