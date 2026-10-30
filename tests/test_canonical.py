"""The canonical text: equal exactly when two songs play the same."""

from dataclasses import replace
from pathlib import Path

import pytest
from pysidtracker import maincli

from pygoattracker import cli, text
from pygoattracker import typed as T
from pygoattracker.canonical import canonical, canonical_song
from pygoattracker.errors import GoatTrackerError
from pygoattracker.formats import GOATTRACKER2
from pygoattracker.model import Orderlist, Pattern, PlayPattern, Subtune, Table
from pygoattracker.player import Player
from pygoattracker.reader import read_sng
from pygoattracker.rebuild import song_into_sid
from pygoattracker.sid import decompile_sid
from pygoattracker.writer import build_sng
from tests.conftest import pass_frames
from tests.test_gt1 import make_song11

DATA = Path(__file__).parent / "data"

LEAD = T.Instrument(0x09, 0x89, 1, 1, 1, 1, 2, 2, first_wave=T.SetWave(0x09))
BASS = T.Instrument(0x00, 0xF8, 4, 0, 0, 0, 0, 3, first_wave=T.SetWave(0x41))
WAVE = (
    T.WaveStep(T.SetWave(0x41), T.WaveNote(0, True)),
    T.WaveStep(T.SetWave(0x21), T.WaveNote(12, True)),
    T.Jump(1),
    T.WaveStep(T.SetWave(0x81), T.WaveNote(0x40, False)),
    T.WaveStep(T.SetWave(0x11), T.WaveNote(0, True)),
    T.Stop(),
)
PULSE = (T.PulseSet(0x800), T.PulseMod(0x20, 0x10), T.Stop())
FILTER = (
    T.FilterSet(0x10, 0x0F, 0x01),
    T.CutoffSet(0x40),
    T.FilterMod(0x20, 2),
    T.Stop(),
)
SPEED = (T.FixedSpeed(0x20), T.FixedSpeed(0x40))


def _r(note=None, ins=None, cmd=T.DoNothing()):
    return T.Row(T.Rest() if note is None else T.Note(note), ins, cmd)


PATTERNS = (
    (_r(48, 1), _r(), _r(50, 2, T.PortamentoUp(2)), _r(), _r(52, 1), _r()),
    (_r(36, 2), _r(), _r(), _r(43, None, T.Vibrato(1)), _r(), _r()),
    (_r(), _r(60, 1), _r(), _r(), _r(62, 2), _r()),
)


def song(order=((0,), (1,), (2,)), **fields) -> T.TypedSong:
    """A three-channel song of the module's tables, ``fields`` replaced."""
    sub = Subtune([Orderlist([PlayPattern(n) for n in o], 0) for o in order])
    base = T.TypedSong(
        subtunes=(sub,),
        patterns=PATTERNS,
        instruments=(LEAD, BASS),
        wavetable=WAVE,
        pulsetable=PULSE,
        filtertable=FILTER,
        speedtable=SPEED,
    )
    return replace(base, **fields)


def canon(s: T.TypedSong, subtune: int = 0) -> str:
    return text.dumps(canonical_song(s, subtune))


def grid(s: T.TypedSong, subtune: int = 0, **options):
    """``s``'s register grid over one whole pass of the song."""
    frames = pass_frames(Player(s, subtune=subtune, **options))
    return Player(s, subtune=subtune, **options).render_grid(frames)


def same_play(a: T.TypedSong, b: T.TypedSong) -> bool:
    """The canonical verdict, checked against the player's own register grids."""
    verdict = canon(a) == canon(b)
    assert verdict == (grid(a) == grid(b))
    return verdict


def test_unreachable_data_does_not_change_the_text():
    extra = song(
        patterns=PATTERNS + ((_r(70, 3, T.SetWavePtr(8)),),),
        instruments=(LEAD, BASS, replace(BASS, wave_ptr=7, pulse_ptr=4)),
        wavetable=WAVE + (T.WaveStep(T.SetWave(0x15), None), T.Jump(7)),
        pulsetable=PULSE + (T.PulseSet(0x400), T.Stop()),
        filtertable=FILTER + (T.CutoffSet(9), T.Stop()),
        speedtable=SPEED + (T.FixedSpeed(0x99),),
    )
    other = replace(
        extra,
        subtunes=extra.subtunes + (Subtune([Orderlist([PlayPattern(3)], 0)] * 3),),
    )
    assert same_play(song(), other)
    assert canon(other, 1) != canon(other)


def test_identical_chains_at_different_rows_merge():
    copy = WAVE[:2] + (T.Jump(1),) + WAVE[:2] + (T.Jump(4),)
    twin = song(
        instruments=(LEAD, replace(LEAD, wave_ptr=4, name="twin")),
        wavetable=copy,
    )
    merged = canonical_song(twin, 0)
    assert len(merged.wavetable) == 3
    assert len(merged.instruments) == 1
    assert same_play(twin, song(instruments=(LEAD, LEAD), wavetable=copy))


def test_cycles_of_any_phase_merge():
    unrolled = WAVE[:2] * 2 + (T.Jump(3),) + WAVE[3:]
    unrolled = song(wavetable=unrolled, instruments=(LEAD, replace(BASS, wave_ptr=6)))
    assert same_play(song(), unrolled)
    assert canonical_song(unrolled, 0).wavetable == WAVE


def test_renumbered_by_first_use():
    permuted = song(
        order=((2,), (0,), (1,)),
        patterns=(PATTERNS[1], PATTERNS[2], PATTERNS[0]),
        speedtable=SPEED[::-1],
    )
    permuted = replace(
        permuted,
        patterns=tuple(tuple(_swap_speed(r) for r in p) for p in permuted.patterns),
        instruments=(replace(LEAD, vibrato_speed=2), BASS),
    )
    assert same_play(song(), permuted)
    out = canonical_song(permuted, 0)
    assert out.patterns[0][0].note == T.Note(48)
    assert out.speedtable == (T.FixedSpeed(0x20), T.FixedSpeed(0x40))
    assert [i.wave_ptr for i in out.instruments] == [1, 4]


def _swap_speed(row):
    if isinstance(row.command, (T.PortamentoUp, T.Vibrato)):
        return replace(row, command=replace(row.command, speed=3 - row.command.speed))
    return row


def test_songs_that_play_differently_differ():
    changed = PATTERNS[:2] + ((_r(), _r(61, 1)) + PATTERNS[2][2:],)
    assert not same_play(song(), song(patterns=changed))


def test_a_filter_set_takes_a_cutoff_only_right_after_it():
    split = (FILTER[0], T.Jump(3), FILTER[1]) + FILTER[2:]
    assert not same_play(song(), song(filtertable=split))
    detour = FILTER[:2] + (T.Jump(5), T.Stop()) + FILTER[2:]
    assert same_play(song(), song(filtertable=detour))


def test_a_pointer_onto_a_jump_stops_the_song():
    via_jump = (T.Jump(2),) + PULSE
    direct = replace(LEAD, pulse_ptr=2)
    assert not same_play(
        song(pulsetable=via_jump, instruments=(LEAD, BASS)),
        song(pulsetable=via_jump, instruments=(direct, BASS)),
    )


def test_bits_the_player_ignores_do_not_count():
    wide = song(pulsetable=(T.PulseSet(0x7800),) + PULSE[1:])
    assert same_play(song(), wide)
    held = (
        PATTERNS[:1] + ((_r(36, 2, T.DoNothing(7)),) + PATTERNS[1][1:],) + PATTERNS[2:]
    )
    assert same_play(song(), song(patterns=held))


def test_packed_and_editor_rows_read_the_same():
    packed = tuple(T.packed_rows(p) for p in PATTERNS)
    assert any(r.command is None or r.length > 1 for p in packed for r in p)
    assert canon(song()) == canon(song(patterns=packed))


def test_packed_rows_inverts_raw_rows():
    rests = (_r(48, 1),) + (_r(),) * 70 + (_r(None, None, T.SetAD(3)), _r())
    for rows in PATTERNS + (rests,):
        packed = T.packed_rows(rows)
        assert T.rows_of(Pattern(T.raw_rows(packed))) == rows
    assert [r.length for r in T.packed_rows(rests)] == [1, 64, 6, 1, 1]
    lazy = T.packed_rows((_r(),) * 4, state_first=False)
    assert lazy == (T.Row(T.Rest()), T.Row(T.Rest(), length=3))


def test_fixture_canonical_text():
    model = GOATTRACKER2.load((DATA / "gt2_full.sid").read_bytes(), 1)
    out = canonical(model)
    assert out.startswith("# playback Build(") and "default_tempo=5" in out
    assert 'instrument $01 "" ad $09' in out
    assert "pattern $00\n  G-3 $02 Vibrato($01)" in out
    assert GOATTRACKER2.snapshot_probes()["canonical"](model) == out.encode()
    assert text.loads(out.split("# wrap None\n", 1)[1]).subtunes


def _write(path: Path, data: bytes) -> str:
    path.write_bytes(data)
    return str(path)


def _equiv(*paths) -> int:
    try:
        return maincli.main(["equiv", *paths])
    except SystemExit as exc:
        return exc.code


def test_equiv_of_a_sng_round_trip(tmp_path, capsys):
    tune = DATA / "gt2_full.sid"
    sng = tmp_path / "full.sng"
    assert cli.main(["sid2sng", str(tune), str(sng)]) == 0
    out = tmp_path / "back.sid"
    assert cli.main(["sng2sid", str(tune), str(sng), str(out)]) == 0
    assert _equiv(str(tune), str(out)) == 0
    assert _equiv("--subtune", "1", str(tune), str(out)) == 0
    swapped = read_sng(sng.read_bytes())
    table = swapped.speedtable
    swapped.speedtable = Table(table.left[::-1], table.right[::-1])
    swapped.instruments[0] = replace(swapped.instruments[0], vibrato_param=2)
    for pattern in swapped.patterns:
        pattern.rows[:] = [
            replace(r, data=3 - r.data) if r.command in (1, 4) else r
            for r in pattern.rows
        ]
    moved = song_into_sid(tune.read_bytes(), swapped)
    assert moved != tune.read_bytes()
    assert _equiv(str(tune), _write(tmp_path / "moved.sid", moved)) == 0
    capsys.readouterr()
    swapped.patterns[0].rows[0] = replace(swapped.patterns[0].rows[0], note=0x61)
    wrong = _write(tmp_path / "wrong.sid", song_into_sid(tune.read_bytes(), swapped))
    assert _equiv(str(tune), wrong) == 1
    assert "-  C-4 $01" in capsys.readouterr().out


def test_sng2sid_reads_song_text_and_refuses_v1(tmp_path, capsys):
    tune = DATA / "gt2_minimal.sid"
    txt = tmp_path / "min.txt"
    assert cli.main(["text", str(tune), str(txt)]) == 0
    out = tmp_path / "back.sid"
    assert cli.main(["sng2sid", str(tune), str(txt), str(out)]) == 0
    assert out.read_bytes() == tune.read_bytes()
    capsys.readouterr()
    old = tmp_path / "old.txt"
    gt1 = tmp_path / "gt1.sid"
    gt1.write_bytes(make_song11("11.3").to_sid())
    assert cli.main(["text", str(gt1), str(old)]) == 0
    assert cli.main(["sng2sid", str(tune), str(old), str(out)]) == 1
    assert "V1.x" in capsys.readouterr().err


def test_a_song_that_does_not_fit_is_refused(monkeypatch):
    data = (DATA / "gt2_full.sid").read_bytes()
    typed = decompile_sid(data).typed
    longer = replace(typed, patterns=(typed.patterns[0] * 2,) + typed.patterns[1:])
    with pytest.raises(GoatTrackerError, match="does not fit"):
        song_into_sid(data, longer)
    with pytest.raises(GoatTrackerError, match="does not fit the tune"):
        song_into_sid(data, replace(typed, patterns=typed.patterns[:-1]))

    def unpacked(raw, subtune=0):
        dec = decompile_sid(raw, subtune)
        dec.memory = bytes(len(dec.image.mem))
        return dec

    monkeypatch.setattr("pygoattracker.rebuild.decompile_sid", unpacked)
    with pytest.raises(GoatTrackerError, match="unpacks"):
        song_into_sid(data, typed)


def test_build_sng_export_round_trips_through_the_image():
    data = (DATA / "gt2_minimal.sid").read_bytes()
    dec = decompile_sid(data)
    assert song_into_sid(data, read_sng(build_sng(dec.song))) == data


def test_only_the_tuning_the_player_can_read_counts():
    tuning = tuple(range(0x100, 0x180))
    unread = tuning[:120] + (0,) + tuning[121:]
    played = tuning[:48] + (0,) + tuning[49:]
    for other, same in ((unread, True), (played, False)):
        texts = [text.dumps(canonical_song(song(), 0, f)) for f in (tuning, other)]
        grids = [grid(song(), freq_table=f) for f in (tuning, other)]
        assert (texts[0] == texts[1]) == (grids[0] == grids[1]) == same
