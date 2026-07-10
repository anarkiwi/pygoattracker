"""Render songs through an emulated SID to samples or WAV.

The sample/WAV rendering loop and WAV writer are the shared
:mod:`pysidtracker.audio` primitives; this module is the thin
GoatTracker-facing wrapper that turns a :class:`~pygoattracker.model.Song`
into a per-frame ``(reg, val)`` write stream (via
:func:`pygoattracker.player.iter_frames`) and returns
``(samples, sampling_frequency)``.

By default the emulated SID is `pyresidfp
<https://pypi.org/project/pyresidfp/>`_ (install the ``audio`` extra).
Any object with ``write_register(reg, value)``, ``clock(timedelta) ->
samples`` and a ``sampling_frequency`` attribute can be passed as
``device`` instead, e.g. for tests or a different emulator.

Each register write is clocked individually at the same in-frame offset
the register log uses, so renders line up with
:mod:`pygoattracker.reglog` output.
"""

from pathlib import Path

from pysidtracker.audio import CHIP_MODELS, write_wav
from pysidtracker.audio import render_samples as _render_samples

from pygoattracker import constants
from pygoattracker.errors import GoatTrackerError
from pygoattracker.model import Song
from pygoattracker.player import iter_frames

__all__ = ["CHIP_MODELS", "render_samples", "render_wav", "write_wav"]


def _default_device(model: str, sampling_frequency: float | None):
    try:
        from pyresidfp import SoundInterfaceDevice
        from pyresidfp.sound_interface_device import ChipModel
    except ImportError as exc:
        raise GoatTrackerError(
            "pyresidfp is required to render audio; "
            "install with: pip install pygoattracker[audio]"
        ) from exc
    chip = {"6581": ChipModel.MOS6581, "8580": ChipModel.MOS8580}[model]
    if sampling_frequency:
        return SoundInterfaceDevice(
            model=chip, sampling_frequency=float(sampling_frequency)
        )
    return SoundInterfaceDevice(model=chip)


def render_samples(
    song: Song,
    seconds: float = 60.0,
    subtune: int = 0,
    until_loop: bool = False,
    model: str = "8580",
    sampling_frequency: float | None = None,
    device=None,
    cycles_per_frame: int = constants.PAL_CYCLES_PER_FRAME,
    clock_frequency: float = constants.PAL_CLOCK_HZ,
    **player_options,
):
    """Render ``song`` on an emulated SID.

    Returns ``(samples, sampling_frequency)`` where samples are signed
    16-bit mono. Rendering stops at ``seconds`` (or earlier when the
    song stops, or at the song loop with ``until_loop``).
    """
    if model not in CHIP_MODELS:
        raise GoatTrackerError(f"chip model must be one of {CHIP_MODELS}")
    if device is None:
        device = _default_device(model, sampling_frequency)
    frame_seconds = cycles_per_frame / clock_frequency
    max_frames = max(1, round(seconds / frame_seconds))
    frames = iter_frames(
        song,
        subtune=subtune,
        max_frames=max_frames,
        until_loop=until_loop,
        **player_options,
    )
    samples = _render_samples(
        frames,
        model=model,
        sampling_frequency=sampling_frequency,
        cycles_per_frame=cycles_per_frame,
        clock_frequency=clock_frequency,
        device=device,
    )
    return samples, float(device.sampling_frequency)


def render_wav(song: Song, dst, seconds: float = 60.0, **options) -> Path:
    """Render ``song`` to a WAV file; returns the path written.

    Keyword options are those of :func:`render_samples`.
    """
    samples, sampling_frequency = render_samples(song, seconds=seconds, **options)
    write_wav(dst, samples, sampling_frequency)
    return Path(dst)
