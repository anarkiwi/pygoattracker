"""The goattracker1/goattracker2 ``pysidtracker`` format registrations."""

import pytest
from pysidtracker import discover_formats, maincli, recognize_format

from pygoattracker.gt1 import DecompiledGt1, decompile_gt1
from pygoattracker.gt1player import Gt1Player
from pygoattracker.sid import DecompiledSid, decompile_sid
from tests.test_gt1 import make_song
from tests.test_sid import LOAD, _minimal_packed, _psid

FRAMES = 64


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
    grid = fmt.player(model).render_grid(FRAMES)
    assert grid == decompile_sid(gt2_raw, subtune=0).player().render_grid(FRAMES)


@pytest.mark.parametrize("subtune", [0, 1])
def test_gt1_model_and_player(formats, gt1_raw, subtune):
    fmt = formats["goattracker1"]
    model = fmt.parser.parse(gt1_raw, subtune=subtune)
    assert isinstance(model, DecompiledGt1)
    assert model.subtune == subtune
    grid = fmt.player(model).render_grid(FRAMES)
    want = Gt1Player(decompile_gt1(gt1_raw, subtune), subtune=subtune)
    assert grid == want.render_grid(FRAMES)


def test_gt1_subtunes_render_differently(formats, gt1_raw):
    fmt = formats["goattracker1"]
    grids = [
        fmt.player(fmt.parser.parse(gt1_raw, subtune=k)).render_grid(FRAMES)
        for k in (0, 1)
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
