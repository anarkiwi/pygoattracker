"""The song grammar (song.lark): every typed song prints and parses back to itself."""

import itertools
from pathlib import Path

import pytest

from pygoattracker import cli, sid, text
from pygoattracker import typed as T
from pygoattracker.errors import SngParseError
from pygoattracker.model import Instrument as RawInstrument
from pygoattracker.model import Orderlist, PlayPattern, Repeat, Subtune, Transpose
from pygoattracker.reader import read_sng
from pygoattracker.writer import build_sng

from tests.test_roundtrip import rich_song

DATA = Path(__file__).parent / "data"
ALL = range(256)
EDGES = (0x00, 0x01, 0x5F, 0x60, 0x7F, 0x80, 0x81, 0xFE, 0xFF)


def _rows(decode):
    return tuple(decode(a, b) for a, b in itertools.product(ALL, EDGES))


def _every_operation() -> T.TypedSong:
    commands = [T.command_of(n, d) for n, d in itertools.product(range(16), ALL)]
    notes = [T.note_of(b) for b in range(T.C.KEYON + 1)]
    rows = [
        T.Row(n, i, c, ln)
        for c, n, i, ln in zip(
            (*commands, None),
            itertools.cycle(notes),
            itertools.cycle((None, 0, 1, 0x3F)),
            itertools.cycle((1, 1, 2, 64)),
        )
    ]
    raws = [
        RawInstrument(1, 2, 3, 4, 5, 6, 7, gate, first, f"i{gate}")
        for gate, first in zip(ALL, reversed(ALL))
    ][:63]
    order = [PlayPattern(207), Repeat(0), Repeat(15), Transpose(-16), Transpose(14)]
    return T.TypedSong(
        name='quote " backslash \\ and é',
        author="",
        copyright="2026",
        subtunes=(Subtune([Orderlist(order, 4), Orderlist([], 0)] * 3),) * 2,
        patterns=tuple(tuple(rows[k : k + 128]) for k in range(0, len(rows), 128)),
        instruments=tuple(T.Instrument.of(r) for r in raws),
        wavetable=_rows(T.wave_row),
        pulsetable=_rows(T.pulse_row) + _rows(lambda a, b: T.pulse_row(a, b, True)),
        filtertable=_rows(T.filter_row),
        speedtable=_rows(T.speed_row),
        simplepulse=True,
        firstnote=0x30,
        freq=(0x1168, 0xFFFF),
        speed0=T.CalculatedSpeed(1, 2),
    )


def test_every_typed_operation_prints_and_parses_back():
    song = _every_operation()
    assert text.loads(text.dumps(song)) == song


def test_the_grammar_names_every_typed_constructor():
    grammar = text.parser().rules
    names = {rule.origin.name for rule in grammar} | {
        rule.alias for rule in grammar if rule.alias
    }
    typed_classes = {
        name
        for name, cls in text._CLASSES.items()  # pylint: disable=protected-access
        if cls not in (T.TypedSong, T.Row, T.Instrument)
    }
    assert typed_classes <= names


@pytest.mark.parametrize("name", ["gt2_full", "gt2_minimal"])
def test_a_decompiled_tune_prints_and_parses_back(name):
    typed = sid.decompile_sid((DATA / f"{name}.sid").read_bytes()).typed
    assert text.loads(text.dumps(typed)) == typed


def test_a_sng_comes_back_byte_for_byte_through_its_text():
    song = rich_song()
    back = text.loads(text.dumps(T.TypedSong.from_song(song))).to_song()
    assert build_sng(back) == build_sng(song)


def test_a_hand_written_song():
    song = text.loads("""
        name "hand" author "me" copyright "2026"
        subtune 0
          orderlist Transpose(-2) Repeat(1) PlayPattern(0) restart 0  # comment
          orderlist PlayPattern(0) restart 0
          orderlist PlayPattern(0) restart 0
        instrument 1 "lead" ad $09 sr 0 wave 1 pulse 0 filter 0 vibrato 0
          delay 0 gate 2 nohardrestart legato first GateOn
        wavetable
          1: WaveStep(SetWave($41), none)
          2: Jump(1)
        pulsetable
        filtertable
        speedtable
          1: FixedSpeed($0230)
        pattern 0
          C#4 1 SetTempo(6, channel)
          --- Vibrato(1) *4
          Note(-3)
        """)
    assert song.instruments[0].first_wave == T.GateOn()
    assert not song.instruments[0].hard_restart and song.instruments[0].legato
    assert song.patterns[0] == (
        T.Row(T.Note(49), 1, T.SetTempo(6, channel=True)),
        T.Row(T.KeyOff(), None, T.Vibrato(1), 4),
        T.Row(T.Note(-3)),
    )
    assert song.speed(song.patterns[0][1].command) == T.FixedSpeed(0x230)


@pytest.mark.parametrize(
    "src,match",
    [
        ("name", "Unexpected"),
        (
            'name "" author "" copyright "" subtune 1 orderlist restart 0 '
            "wavetable pulsetable filtertable speedtable pattern 0 ...",
            "subtune 1 where 0 is due",
        ),
        (
            'name "" author "" copyright "" subtune 0 orderlist restart 0 '
            "wavetable 2: Stop pulsetable filtertable speedtable pattern 0 ...",
            "wavetable row 2 where 1 is due",
        ),
        (
            'name "" author "" copyright "" subtune 0 orderlist restart 0 '
            "wavetable pulsetable filtertable speedtable pattern 0 H-4",
            "No terminal matches 'H'",
        ),
    ],
)
def test_malformed_text_is_a_parse_error(src, match):
    with pytest.raises(SngParseError, match=match):
        text.loads(src)


def test_cli_text_and_back(tmp_path, capsys):
    sng = tmp_path / "rich.sng"
    sng.write_bytes(build_sng(rich_song()))
    txt, out = tmp_path / "rich.txt", tmp_path / "back.sng"
    assert cli.main(["text", str(sng), str(txt)]) == 0
    assert cli.main(["fromtext", str(txt), str(out)]) == 0
    assert out.read_bytes() == sng.read_bytes()
    assert cli.main(["text", str(DATA / "gt2_minimal.sid")]) == 0
    assert "simplepulse" in capsys.readouterr().out
    assert read_sng(out) == rich_song()


def test_cli_text_rejects_a_gt1_song(tmp_path, capsys):
    path = tmp_path / "old.sng"
    path.write_bytes(b"GTS!" + bytes(2000))
    assert cli.main(["text", str(path)]) == 1
    assert "error" in capsys.readouterr().err.lower()
