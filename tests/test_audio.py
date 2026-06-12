"""Audio rendering against a fake and (when available) a real SID."""

import sys
import wave

import pytest

from pygoattracker import audio
from pygoattracker.errors import GoatTrackerError


class FakeDevice:
    """Minimal stand-in for pyresidfp's SoundInterfaceDevice."""

    sampling_frequency = 44100.0

    def __init__(self):
        self.writes = []
        self.seconds = 0.0
        self._emitted = 0

    def write_register(self, reg, val):
        self.writes.append((reg, val))

    def clock(self, delta):
        self.seconds += delta.total_seconds()
        due = round(self.seconds * self.sampling_frequency)
        samples = [0] * (due - self._emitted)
        self._emitted = due
        return samples


def test_render_samples_with_device(song):
    device = FakeDevice()
    samples, rate = audio.render_samples(song, seconds=0.5, device=device)
    assert rate == 44100.0
    assert len(samples) == round(device.seconds * rate)
    # Half a second of PAL frames (timedelta quantizes to microseconds,
    # so per-write clocking drifts by a few ppm).
    frames = round(0.5 * audio.constants.PAL_CLOCK_HZ / 19656)
    assert device.seconds == pytest.approx(frames * 19656 / 985248, abs=1e-3)
    assert device.writes[0] == (0, 0)
    assert (4, 0x41) in device.writes


def test_render_wav(song, tmp_path):
    path = tmp_path / "song.wav"
    result = audio.render_wav(song, path, seconds=0.2, device=FakeDevice())
    assert result == path
    with wave.open(str(path), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 44100
        assert wav.getnframes() == pytest.approx(0.2 * 44100, abs=441)


def test_write_wav_accepts_lists(tmp_path):
    path = tmp_path / "tiny.wav"
    audio.write_wav(path, [0, 1000, -1000], 8000)
    with wave.open(str(path), "rb") as wav:
        assert wav.getnframes() == 3
        assert wav.getframerate() == 8000


def test_bad_model(song):
    with pytest.raises(GoatTrackerError, match="model"):
        audio.render_samples(song, model="6582")


def test_missing_pyresidfp(song, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyresidfp", None)
    with pytest.raises(GoatTrackerError, match="pyresidfp"):
        audio.render_samples(song, seconds=0.1)


def test_render_with_pyresidfp(song):
    pytest.importorskip("pyresidfp")
    samples, rate = audio.render_samples(song, seconds=1.0, model="8580")
    assert rate > 8000
    assert len(samples) == pytest.approx(rate, rel=0.05)
    # The test song actually makes noise.
    assert max(abs(value) for value in samples) > 100


def test_render_with_pyresidfp_6581_and_rate(song):
    pytest.importorskip("pyresidfp")
    samples, rate = audio.render_samples(
        song, seconds=0.2, model="6581", sampling_frequency=22050
    )
    assert rate == 22050
    assert len(samples) == pytest.approx(0.2 * rate, rel=0.05)
