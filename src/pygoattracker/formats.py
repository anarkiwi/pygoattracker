"""Register GoatTracker with the generic ``pysidtracker`` command-line tool.

``goattracker1`` (:class:`~pygoattracker.gt1.Gt1SidParser`) and ``goattracker2``
(:class:`~pygoattracker.sid.GoatTrackerSidParser`) on the ``pysidtracker.formats``
entry-point group make ``pysidtracker info|reglog|wav`` play packed GoatTracker tunes.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable, Sequence

from pysidtracker import MemPlayer, SidFormat

from pygoattracker import text
from pygoattracker.gt1 import DecompiledGt1, Gt1SidParser
from pygoattracker.gt1typed import Gt1TypedSong
from pygoattracker.packed import Build
from pygoattracker.sid import DecompiledSid, GoatTrackerSidParser
from pygoattracker.writer import build_sng

_EDITOR = asdict(Build()) | {"nowavedelay": False}
_GRID_FRAMES = 250


def _player(model) -> MemPlayer:
    """A fresh player of the decompiled model's subtune."""
    return model.player()


def _metadata(model) -> Sequence:
    """name/author/released/load/init/play, read from the tune's PSID header."""
    header = model.header
    load = header.real_load_address
    init = header.init_address or load
    return (
        header.name,
        header.author,
        header.released,
        load,
        init,
        header.play_address or init,
    )


def _describe_gt2(model: DecompiledSid) -> Iterable[str]:
    """Extra ``info`` lines for a GoatTracker 2 tune."""
    song, info = model.song, model.info
    yield f"subtunes: {len(song.subtunes)}"
    yield f"patterns: {len(song.patterns)}"
    yield f"instruments: {len(song.instruments)}"
    flags = asdict(info.playback) | {"nowavedelay": info.nowavedelay}
    options = [name for name, on in flags.items() if on is True and not _EDITOR[name]]
    yield f"options:  {' '.join(options) or 'none'}"


def _describe_gt1(model: DecompiledGt1) -> Iterable[str]:
    """Extra ``info`` lines for a GoatTracker V1.x tune."""
    song = model.song
    yield f"playroutine: {song.player}"
    yield f"subtunes: {len(song.orderlists)}"
    yield f"patterns: {len(song.patterns)}"
    yield f"instruments: {len(song.instruments)}"


def _grid(model) -> bytes:
    """The first :data:`_GRID_FRAMES` frames the model's player renders."""
    return bytes(
        v & 0xFF for row in model.player().render_grid(_GRID_FRAMES) for v in row
    )


GT2_PROBES = {
    "typed": lambda model: text.dumps(model.typed),
    "repack": lambda model: model.repack(),
    "sng": lambda model: build_sng(model.song),
    "grid": _grid,
}
GT1_PROBES = {
    "typed": lambda model: text.dumps(Gt1TypedSong.from_song(model.song)),
    "pack": lambda model: model.song.pack()[1],
    "grid": _grid,
}

GOATTRACKER1 = SidFormat(
    name="goattracker1",
    parser=Gt1SidParser(),
    player=_player,
    describe=_describe_gt1,
    metadata=_metadata,
    probes=GT1_PROBES,
)

GOATTRACKER2 = SidFormat(
    name="goattracker2",
    parser=GoatTrackerSidParser(),
    player=_player,
    describe=_describe_gt2,
    metadata=_metadata,
    probes=GT2_PROBES,
)
