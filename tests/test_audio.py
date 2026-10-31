"""Audio rendering against a fake and a real SID."""

import wave

import numpy as np
import pytest

from pygoattracker import audio
from pygoattracker.errors import GoatTrackerError


class FakeDevice:
    """Minimal device: records the write array and returns silence."""

    sampling_hz = 44100.0
    clock_hz = 985248.0

    def __init__(self):
        self.runs = []

    def run(self, writes, until):
        self.runs.append((np.array(writes), until))
        first = self.runs[-2][1] if len(self.runs) > 1 else 0
        return np.zeros(
            round((until - first) * self.sampling_hz / self.clock_hz), "<i2"
        )


def test_render_samples_with_device(song):
    device = FakeDevice()
    samples, rate = audio.render_samples(song, seconds=0.5, device=device)
    assert rate == 44100.0
    frames = round(0.5 * audio.constants.PAL_CLOCK_HZ / 19656)
    assert device.runs[-1][1] == frames * 19656
    assert len(samples) == pytest.approx(
        frames * 19656 / 985248 * rate, abs=len(device.runs)
    )
    writes = np.concatenate([w for w, _ in device.runs])
    assert tuple(writes[0]) == (0, 0, 0)
    assert (writes[:, 1:] == (4, 0x41)).all(axis=1).any()
    assert (np.diff(writes[:, 0]) >= 0).all()


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


def test_render_with_pylibresidfp(song):
    samples, rate = audio.render_samples(song, seconds=1.0, model="8580")
    assert rate > 8000
    assert len(samples) == pytest.approx(rate, rel=0.05)
    # The test song actually makes noise.
    assert max(abs(value) for value in samples) > 100


def test_render_with_pylibresidfp_6581_and_rate(song):
    samples, rate = audio.render_samples(
        song, seconds=0.2, model="6581", sampling_frequency=22050
    )
    assert rate == 22050
    assert len(samples) == pytest.approx(0.2 * rate, rel=0.05)
