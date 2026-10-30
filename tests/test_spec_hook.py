"""``SidFormat.spec`` of both GoatTracker formats: the spec bound in place to a
decompiled tune's own image, played with the tune's note table or the standard one."""

from types import SimpleNamespace

import numpy as np
import pytest
from pysidtracker import SidImage
from pysidtracker.formats import SpecBinding
from pysidtracker.irqhost import TunePlayer
from pysidtracker.notes import frequency

from pygoattracker import constants, gt1spec, gt2spec
from pygoattracker.errors import SidParseError
from pygoattracker.formats import GOATTRACKER1, GOATTRACKER2
from pygoattracker.gt1 import Gt1Instrument
from pygoattracker.model import Instrument, Pattern, Row, Song
from pygoattracker.player import Player
from tests._gt2refs import sources
from tests.conftest import add_test_instrument, pass_frames
from tests.test_gt1 import _n, make_song, make_song11, make_song14

CLOCK = 985248
NTSC = 1022727
FIXTURES = ("gt2_full", "gt2_minimal")
SYNTHETIC_GT1 = {
    "1.53": make_song,
    "11.4": make_song11,
    "1.4b": make_song14,
}


def words(grid, voice: int = 0) -> list:
    """The frequency word of ``voice`` in each frame of ``grid``."""
    grid = np.asarray(grid)
    lo = voice * constants.VOICE_REG_SIZE
    return (grid[:, lo].astype(int) | grid[:, lo + 1].astype(int) << 8).tolist()


def fixture_sid(name: str) -> bytes:
    """A committed packed GoatTracker 2 tune."""
    with open(f"tests/data/{name}.sid", "rb") as sid:
        return sid.read()


def walk_song(bases) -> Song:
    """One instrument stepping through the relative notes 1-127 on each tick, played
    from each of the pattern notes ``bases`` (indexes from C-0), 24 rows apart."""
    song = Song(name="T", author="T", copyright="T")
    first = song.wavetable.add(0x41, 1)
    for step in range(2, 128):
        song.wavetable.add(0x41, step)
    song.wavetable.add(constants.TABLEJUMP, 0)
    song.instruments.append(
        Instrument(attack_decay=0x09, wave_ptr=first, gateoff_timer=2, first_wave=0x09)
    )
    pattern = Pattern.empty(24 * len(bases))
    for k, base in enumerate(bases):
        pattern.rows[24 * k] = Row(note=constants.FIRSTNOTE + base, instrument=1)
    song.patterns = [pattern]
    return song


def porta_song(base: int, count: int) -> Song:
    """A note held with a portamento up whose speed is calculated from the next
    note's distance, shifted right ``count`` times."""
    song = Song(name="T", author="T", copyright="T")
    instrument = add_test_instrument(song)
    speed = song.speedtable.add(0x80, count)
    pattern = Pattern.empty(8)
    pattern.rows[0] = Row(note=constants.FIRSTNOTE + base, instrument=instrument)
    pattern.rows[1] = Row(command=0x1, data=speed)
    song.patterns = [pattern]
    return song


STANDARD_TABLE = tuple(frequency(note, CLOCK) for note in range(128))


@pytest.mark.parametrize("bases", [(0, 92, 48)])
def test_wavetable_note_reads_are_the_standard_words(bases):
    """Each wavetable step's read, native table end and past it, is the word of the
    note played."""
    song = walk_song(bases)
    frames = pass_frames(Player(song))
    standard = words(Player(song, notes=CLOCK).render_grid(frames))
    native = words(Player(song).render_grid(frames))
    for k, base in enumerate(bases):
        start = 8 + 6 * 24 * k
        expected = [frequency((base + step) & 0x7F, CLOCK) for step in range(1, 128)]
        assert standard[start : start + 127] == expected
        assert native[start : start + 127] != expected


@pytest.mark.parametrize("base, count", [(0, 0), (48, 2), (60, 3)])
def test_calculated_speed_reads_the_next_note(base, count):
    """A calculated speed is the standard distance to the next note, shifted."""
    song = porta_song(base, count)
    frames = pass_frames(Player(song))
    steps = np.diff(words(Player(song, notes=CLOCK).render_grid(frames)))
    expected = (frequency(base + 1, CLOCK) - frequency(base, CLOCK)) >> count
    onset, *moving = steps[steps != 0]
    assert onset == frequency(base, CLOCK)
    assert expected and moving == [expected] * 5


@pytest.mark.parametrize("song", [walk_song((0, 92, 48)), porta_song(0, 1)])
def test_standard_mode_is_native_mode_on_a_standard_table(song):
    frames = pass_frames(Player(song))
    native = Player(song, freq_table=STANDARD_TABLE).render_grid(frames)
    assert Player(song, notes=CLOCK).render_grid(frames) == native


@pytest.mark.parametrize("name", FIXTURES)
def test_gt2_spec_player_plays_the_tunes_own_image(name):
    sources()
    raw = fixture_sid(name)
    model = GOATTRACKER2.load(raw)
    frames = pass_frames(model.player())
    played = GOATTRACKER2.spec_player(model).render_grid(frames)
    assert played == TunePlayer(raw).render_grid(frames)


def test_gt2_binding_is_the_models_own_image():
    sources()
    model = GOATTRACKER2.load(fixture_sid("gt2_full"))
    binding = GOATTRACKER2.spec(model)
    assert isinstance(binding, SpecBinding)
    assert binding.spec is gt2spec.spec()
    assert binding.image == model.image.image
    assert (binding.subtune, binding.psid) == (model.start, True)
    view = GOATTRACKER2.spec_player(model).view()
    play = gt2spec.binder(model).match(view)["mt_play"]
    assert set(binding.entries(view)) == {play}
    assert binding.bind(view, play)["nchan"] == 3


def test_binder_refuses_a_tune_without_code_or_image():
    with pytest.raises(SidParseError, match="no playroutine code"):
        gt2spec.binder(SimpleNamespace(info=SimpleNamespace(code=None)))
    with pytest.raises(SidParseError, match="no image"):
        gt2spec.spec_binding(SimpleNamespace(image=None))


def test_binder_refuses_code_no_source_matches():
    sources()
    with pytest.raises(SidParseError, match="no GoatTracker 2 playroutine"):
        gt2spec.Binder((0x1000, 0x1100)).match(bytes(0x10000))


def gt1_notes_song(player: str):
    """A V1.x song whose one channel steps through every pattern note in turn."""
    halves = [
        b"".join(_n(n, 1) for n in range(first, first + 48)) + b"\xff"
        for first in (0, 48)
    ]
    return SYNTHETIC_GT1[player](
        player,
        instruments=[Gt1Instrument(0x09, 0, wave_ptr=1)],
        wavetable=[(0x41, 0), (0xFF, 0)],
        patterns=halves,
        orderlists=[[bytes((0, 1, 0xFF, 0))] * 3],
    )


@pytest.mark.parametrize("player", SYNTHETIC_GT1)
def test_gt1_note_reads_are_the_standard_words(player):
    model = GOATTRACKER1.load(gt1_notes_song(player).to_sid())
    steps = []
    frames = pass_frames(model.player())
    for word in words(GOATTRACKER1.spec_player(model, notes=CLOCK).render_grid(frames)):
        if not steps or word != steps[-1]:
            steps.append(word)
    assert steps[:95] == [0] + [frequency(note, CLOCK) for note in range(94)]


@pytest.mark.parametrize("player", SYNTHETIC_GT1)
def test_gt1_spec_player_plays_what_the_family_player_plays(player):
    model = GOATTRACKER1.load(SYNTHETIC_GT1[player](player).to_sid())
    frames = pass_frames(model.player())
    assert GOATTRACKER1.spec_player(model).render_grid(frames) == (
        GOATTRACKER1.player(model).render_grid(frames)
    )


def test_gt1_binding_is_the_models_own_image():
    model = GOATTRACKER1.load(make_song().to_sid())
    binding = GOATTRACKER1.spec(model)
    assert binding.image == SidImage.from_bytes(model.sid).image
    assert (binding.subtune, binding.psid) == (0, True)
    assert binding.spec is gt1spec.spec("1.5")
    view = GOATTRACKER1.spec_player(model).view()
    entries = binding.entries(view)
    assert entries == gt1spec.entries(view)
    assert binding.bind(view, next(iter(entries)))["sid"] == constants.SID_BASE


def with_clock(raw: bytes, clock: int) -> bytes:
    """``raw`` with the PSID header's clock flags set to ``clock`` (1 PAL, 2 NTSC)."""
    head = bytearray(raw)
    flags = int.from_bytes(head[0x76:0x78], "big") & ~0xC | clock << 2
    head[0x76:0x78] = flags.to_bytes(2, "big")
    return bytes(head)


def note_steps(grid) -> list:
    """The distinct successive frequency words of voice 0 in ``grid``."""
    steps = []
    for word in words(grid):
        if not steps or word != steps[-1]:
            steps.append(word)
    return steps


@pytest.mark.parametrize("name", FIXTURES)
def test_gt2_binding_sid_is_the_tunes_own_container(name):
    sources()
    raw = fixture_sid(name)
    assert GOATTRACKER2.spec(GOATTRACKER2.load(raw)).sid == raw


@pytest.mark.parametrize("name", FIXTURES)
def test_gt2_bare_prg_has_no_sid(name):
    sources()
    prg = fixture_sid(name)[0x7C:]
    assert GOATTRACKER2.spec(GOATTRACKER2.load(prg)).sid is None


@pytest.mark.parametrize("name", FIXTURES)
def test_gt2_notes_follow_the_header_clock(name):
    sources()
    pal = GOATTRACKER2.load(with_clock(fixture_sid(name), 1))
    ntsc = GOATTRACKER2.load(with_clock(fixture_sid(name), 2))
    frames = pass_frames(pal.player())
    grid = GOATTRACKER2.spec_player(ntsc, notes=True).render_grid(frames)
    assert grid == GOATTRACKER2.spec_player(ntsc, notes=NTSC).render_grid(frames)
    assert grid != GOATTRACKER2.spec_player(pal, notes=True).render_grid(frames)
    assert GOATTRACKER2.spec_player(pal, notes=True).render_grid(frames) == (
        GOATTRACKER2.spec_player(pal, notes=CLOCK).render_grid(frames)
    )


@pytest.mark.parametrize("clock, hz", [(1, CLOCK), (2, NTSC)])
@pytest.mark.parametrize("player", SYNTHETIC_GT1)
def test_gt1_notes_follow_the_header_clock(player, clock, hz):
    raw = with_clock(gt1_notes_song(player).to_sid(), clock)
    model = GOATTRACKER1.load(raw)
    assert GOATTRACKER1.spec(model).sid == raw
    grid = GOATTRACKER1.spec_player(model, notes=True).render_grid(
        pass_frames(model.player())
    )
    assert note_steps(grid)[:95] == [0] + [frequency(n, hz) for n in range(94)]


@pytest.mark.parametrize("player", SYNTHETIC_GT1)
def test_gt1_bare_prg_has_no_sid(player):
    prg = gt1_notes_song(player).to_sid()[0x7C:]
    model = GOATTRACKER1.load(prg)
    assert GOATTRACKER1.spec(model).sid is None
    frames = pass_frames(model.player())
    assert GOATTRACKER1.spec_player(model).render_grid(frames) == (
        GOATTRACKER1.player(model).render_grid(frames)
    )
