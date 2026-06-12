"""Command line interface."""

import wave

from pygoattracker import audio, cli, reglog
from pygoattracker.writer import write_sng

from tests.conftest import basic_song
from tests.test_audio import FakeDevice


def song_path(tmp_path):
    path = tmp_path / "song.sng"
    write_sng(basic_song(), path)
    return str(path)


def test_info(tmp_path, capsys):
    assert cli.main(["info", song_path(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "name:        TEST" in out
    assert "author:      PYGOATTRACKER" in out
    assert "subtunes:    1" in out
    assert "01: TEST01" in out


def test_info_missing_file(tmp_path, capsys):
    assert cli.main(["info", str(tmp_path / "nope.sng")]) == 1
    assert "error:" in capsys.readouterr().err


def test_info_bad_file(tmp_path, capsys):
    bad = tmp_path / "bad.sng"
    bad.write_bytes(b"not a song")
    assert cli.main(["info", str(bad)]) == 1
    assert "error:" in capsys.readouterr().err


def test_reglog(tmp_path, capsys):
    out = tmp_path / "song.reglog"
    code = cli.main(["reglog", song_path(tmp_path), str(out), "--seconds", "1"])
    assert code == 0
    assert str(out) in capsys.readouterr().out
    writes = reglog.read_reglog(out)
    assert len(writes) > 25
    assert writes[0] == reglog.RegWrite(0, 0, 0)


def fake_device(_model, _rate):
    return FakeDevice()


def test_wav(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(audio, "_default_device", fake_device)
    out = tmp_path / "song.wav"
    code = cli.main(["wav", song_path(tmp_path), str(out), "--seconds", "0.2"])
    assert code == 0
    assert str(out) in capsys.readouterr().out
    with wave.open(str(out), "rb") as handle:
        assert handle.getnframes() > 0


def test_info_nt2(tmp_path, capsys):
    from pygoattracker import ninja
    from tests.test_ninja import rich_song

    path = tmp_path / "song.nt2"
    ninja.write_nt2(rich_song(), path)
    assert cli.main(["info", str(path)]) == 0
    out = capsys.readouterr().out
    assert "NinjaTracker 2" in out
    assert "subtunes:    2" in out
    assert "01: lead" in out
    assert "02: soft bass" in out
