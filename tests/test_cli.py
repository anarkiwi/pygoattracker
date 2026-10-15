"""Command line interface."""

import wave

from pygoattracker import audio, cli, model, ninja, reglog
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


def test_reglog_frames_match_wav(tmp_path, monkeypatch):
    requested = []

    def capture(_song, max_frames=None, **_options):
        requested.append(max_frames)
        return iter(())

    monkeypatch.setattr(reglog, "iter_register_writes", capture)
    monkeypatch.setattr(audio, "iter_frames", capture)
    out = tmp_path / "song.reglog"
    assert cli.main(["reglog", song_path(tmp_path), str(out), "--seconds", "10"]) == 0
    audio.render_samples(basic_song(), seconds=10, device=FakeDevice())
    assert requested == [501, 501]


def test_wav(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(audio, "resolve_device", lambda *_: FakeDevice())
    out = tmp_path / "song.wav"
    code = cli.main(["wav", song_path(tmp_path), str(out), "--seconds", "0.2"])
    assert code == 0
    assert str(out) in capsys.readouterr().out
    with wave.open(str(out), "rb") as handle:
        assert handle.getnframes() > 0


def test_info_nt2(tmp_path, capsys):
    from tests.test_ninja import rich_song

    path = tmp_path / "song.nt2"
    ninja.write_nt2(rich_song(), path)
    assert cli.main(["info", str(path)]) == 0
    out = capsys.readouterr().out
    assert "NinjaTracker 2" in out
    assert "subtunes:    2" in out
    assert "01: lead" in out
    assert "02: soft bass" in out


def test_nt2_convert(tmp_path, capsys):
    out = tmp_path / "song.nt2"
    assert cli.main(["nt2", song_path(tmp_path), str(out)]) == 0
    assert str(out) in capsys.readouterr().out
    song = ninja.read_nt2(out)
    assert song.commands[0].name == "TEST01"


def test_nt2_convert_lenient(tmp_path, capsys):
    path = tmp_path / "song.sng"
    song = basic_song()
    song.patterns[0].rows[2] = model.Row(command=0xD, data=0x05)
    write_sng(song, path)
    out = tmp_path / "song.nt2"
    assert cli.main(["nt2", str(path), str(out)]) == 1
    assert "master volume" in capsys.readouterr().err
    assert cli.main(["nt2", str(path), str(out), "--lenient"]) == 0
    captured = capsys.readouterr().out
    assert "dropped: DXY master volume command" in captured
    assert out.exists()
