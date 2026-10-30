"""The goattracker1/goattracker2 ``pysidtracker`` format registrations."""

from pathlib import Path

import pytest
from pysidtracker import discover_formats, maincli, recognize_format

from pygoattracker import gt1streams, packed
from pygoattracker.formats import GOATTRACKER1, GOATTRACKER2, gt1_streams, gt2_streams
from pygoattracker.gt1 import DecompiledGt1, decompile_gt1
from pygoattracker.gt1player import Gt1Player
from pygoattracker.sid import DecompiledSid, decompile_sid
from tests.conftest import pass_frames
from tests.test_gt1 import make_song
from tests.test_sid import LOAD, _minimal_packed, _psid

DATA = Path(__file__).parent / "data"


@pytest.fixture(name="formats")
def formats_fixture():
    return {fmt.name: fmt for fmt in discover_formats()}


@pytest.fixture(name="gt2_raw")
def gt2_raw_fixture() -> bytes:
    return _minimal_packed()


@pytest.fixture(name="gt1_raw")
def gt1_raw_fixture() -> bytes:
    return make_song(address=0x3000).to_sid("SONG", "AUTHOR", "2026")


def test_both_formats_registered(formats):
    assert {"goattracker1", "goattracker2"} <= set(formats)


def test_recognize_format_selects_each(formats, gt1_raw, gt2_raw):
    found = list(formats.values())
    assert recognize_format(found, gt2_raw).name == "goattracker2"
    assert recognize_format(found, gt1_raw).name == "goattracker1"
    assert recognize_format(found, _psid(bytes(64))) is None


def test_gt2_model_and_player(formats, gt2_raw):
    fmt = formats["goattracker2"]
    model = fmt.parser.parse(gt2_raw, subtune=0)
    assert isinstance(model, DecompiledSid)
    frames = pass_frames(fmt.player(model))
    grid = fmt.player(model).render_grid(frames)
    assert grid == decompile_sid(gt2_raw, subtune=0).player().render_grid(frames)


@pytest.mark.parametrize("subtune", [0, 1])
def test_gt1_model_and_player(formats, gt1_raw, subtune):
    fmt = formats["goattracker1"]
    model = fmt.parser.parse(gt1_raw, subtune=subtune)
    assert isinstance(model, DecompiledGt1)
    assert model.subtune == subtune
    frames = pass_frames(fmt.player(model))
    grid = fmt.player(model).render_grid(frames)
    want = Gt1Player(decompile_gt1(gt1_raw, subtune), subtune=subtune)
    assert grid == want.render_grid(frames)


def test_gt1_subtunes_render_differently(formats, gt1_raw):
    fmt = formats["goattracker1"]
    models = [fmt.parser.parse(gt1_raw, subtune=k) for k in (0, 1)]
    grids = [
        fmt.player(model).render_grid(pass_frames(fmt.player(model)))
        for model in models
    ]
    assert grids[0] != grids[1]


def test_metadata_from_header(formats, gt1_raw, gt2_raw):
    gt2 = formats["goattracker2"]
    assert gt2.model_metadata(gt2.parser.parse(gt2_raw)) == (
        "TEST",
        "AUTH",
        "2026",
        LOAD,
        LOAD,
        LOAD + 3,
    )
    gt1 = formats["goattracker1"]
    assert gt1.model_metadata(gt1.parser.parse(gt1_raw)) == (
        "SONG",
        "AUTHOR",
        "2026",
        0x3000,
        0x3000,
        0x3003,
    )


def test_describe_lines(formats, gt1_raw, gt2_raw):
    gt2 = formats["goattracker2"]
    assert list(gt2.describe(gt2.parser.parse(gt2_raw))) == [
        "subtunes: 1",
        "patterns: 2",
        "instruments: 1",
        "options:  simplepulse live_vibrato nowavedelay",
    ]
    gt1 = formats["goattracker1"]
    song = make_song()
    assert list(gt1.describe(gt1.parser.parse(gt1_raw))) == [
        "playroutine: 1.53",
        f"subtunes: {len(song.orderlists)}",
        f"patterns: {len(song.patterns)}",
        f"instruments: {len(song.instruments)}",
    ]


def test_cli_info(tmp_path, capsys, gt2_raw):
    path = tmp_path / "tune.sid"
    path.write_bytes(gt2_raw)
    maincli.main(["info", str(path)])
    out = capsys.readouterr().out
    assert "format:   goattracker2" in out
    assert "patterns: 2" in out


def _grammar(path, capsys) -> str:
    assert maincli.main(["grammar", str(path)]) == 0
    return capsys.readouterr().out


def test_pysidtracker_grammar_lists_both_formats(tmp_path, capsys, gt1_raw):
    out = _grammar(DATA / "gt2_minimal.sid", capsys)
    assert out.startswith("orderlist 0:\n  orderlist PATTERN=$00 LOOPSONG=$ff")
    gt1 = tmp_path / "gt1.sid"
    gt1.write_bytes(gt1_raw)
    assert "subtune 0 orderlist 0:" in _grammar(gt1, capsys)


@pytest.mark.parametrize("name", ["gt2_full", "gt2_minimal"])
def test_gt2_streams_are_the_stored_streams(name):
    model = GOATTRACKER2.load((DATA / f"{name}.sid").read_bytes())
    streams = gt2_streams(model)
    assert len(streams) == len(model.info.layout.order_addrs) + len(
        model.typed.patterns
    )
    for _, stream, rule in streams:
        packed.STREAMS.parse_exact(stream.to_bytes(), rule)
    assert [s.to_bytes()[-1] for n, s, _ in streams if n.startswith("pattern")] == [
        0
    ] * len(model.typed.patterns)


def test_gt1_streams_pick_the_rule_of_a_cut_pattern(gt1_raw):
    model = GOATTRACKER1.load(gt1_raw)
    for _, stream, rule in gt1_streams(model):
        gt1streams.STREAMS.parse_prefix(stream.to_bytes(), rule)
    assert gt1streams.pattern_rule(b"\x60", False) == "pattern_cut"
    assert gt1streams.pattern_rule(b"", True) == "editor_pattern"
