"""Register GoatTracker with the generic ``pysidtracker`` command-line tool.

``goattracker1`` (:class:`~pygoattracker.gt1.Gt1SidParser`) and ``goattracker2``
(:class:`~pygoattracker.sid.GoatTrackerSidParser`) on the ``pysidtracker.formats``
entry-point group make ``pysidtracker info|reglog|wav|grammar`` (and, for GT2,
``equiv``) work on packed GoatTracker tunes.
"""

from __future__ import annotations

import hashlib
import inspect
import os
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from pysidtracker import MemPlayer, SidError, SidFormat, survey
from pysidtracker.cadence import playroutine_cadence

from pygoattracker import gt1streams, packed, text
from pygoattracker.canonical import canonical
from pygoattracker.gt1 import DecompiledGt1, Gt1SidParser
from pygoattracker.gt1typed import Gt1TypedSong
from pygoattracker.packed import Build
from pygoattracker.sid import DecompiledSid, GoatTrackerSidParser
from pygoattracker.writer import build_sng

_EDITOR = asdict(Build()) | {"nowavedelay": False}
DEFAULT_SECONDS = inspect.signature(survey.run).parameters["default_seconds"].default


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


class _Sourced:
    """A parser whose model keeps the tune's bytes, which whole-song probes
    re-read every subtune of."""

    def parse(self, data, subtune=0, **kwargs):
        """The parent's model, with ``data`` as its ``source``."""
        model = super().parse(data, subtune=subtune, **kwargs)
        model.source = bytes(data)
        return model


class Gt1Parser(_Sourced, Gt1SidParser):
    """:class:`~pygoattracker.gt1.Gt1SidParser` keeping the tune's bytes."""


class Gt2Parser(_Sourced, GoatTrackerSidParser):
    """:class:`~pygoattracker.sid.GoatTrackerSidParser` keeping the tune's bytes."""


@lru_cache(maxsize=1)
def _songlengths() -> dict:
    """``md5 -> [seconds per song]`` of the ``$HVSC`` tree, empty without one."""
    root = os.environ.get("HVSC")
    if not root or not (Path(root) / "DOCUMENTS" / "Songlengths.md5").is_file():
        return {}
    return survey.songlengths(root)


def song_frames(data: bytes, subtune: int) -> int:
    """Play calls of 0-based ``subtune`` over its HVSC song length (the survey's
    default where HVSC lists none), at the tune's own cadence."""
    lens = _songlengths().get(hashlib.md5(data).hexdigest(), ())
    seconds = lens[subtune] if subtune < len(lens) else DEFAULT_SECONDS
    return int(seconds * playroutine_cadence(data, subtune=subtune).calls_per_second)


def _grid(model, parser) -> bytes:
    """Each subtune's whole-song register grid as the model's format renders it
    (a subtune that fails, its error), one digest per subtune."""
    data = model.source
    out = []
    for subtune in range(max(model.header.songs, 1)):
        try:
            song = model if subtune == model.subtune else parser.parse(data, subtune)
            grid = song.player().render_grid(song_frames(data, subtune))
            chunk = np.asarray(grid, np.int16).tobytes()
        except SidError as exc:
            chunk = f"!{type(exc).__name__}: {exc}".encode()
        out.append(hashlib.blake2b(chunk, digest_size=16).digest())
    return b"".join(out)


class Stored(bytes):
    """A stream's stored bytes, as a byte-grammar listing's model."""

    def to_bytes(self) -> bytes:
        """The bytes themselves."""
        return bytes(self)


def _stored(streams) -> list:
    """``(name, model, rule)`` of ``(name, rule, bytes)`` streams."""
    return [(name, Stored(raw), rule) for name, rule, raw in streams]


def gt2_streams(model: DecompiledSid) -> list:
    """The packed orderlists and patterns the layout points at, as stored."""
    mem = model.image.mem if model.memory is None else model.memory
    return _stored(packed.stream_trees(mem, model.info.layout))


def gt1_streams(model) -> list:
    """The orderlists and patterns of ``model.song``, a V1.x song."""
    return _stored(gt1streams.stream_trees(model.song))


GT2_PROBES = {
    "typed": lambda model: text.dumps(model.typed),
    "repack": lambda model: model.repack(),
    "sng": lambda model: build_sng(model.song),
    "grid": lambda model: _grid(model, GoatTrackerSidParser()),
}
GT1_PROBES = {
    "typed": lambda model: text.dumps(Gt1TypedSong.from_song(model.song)),
    "pack": lambda model: model.song.pack()[1],
    "grid": lambda model: _grid(model, Gt1SidParser()),
}

GOATTRACKER1 = SidFormat(
    name="goattracker1",
    parser=Gt1Parser(),
    player=_player,
    describe=_describe_gt1,
    metadata=_metadata,
    probes=GT1_PROBES,
    grammar=gt1streams.STREAMS,
    streams=gt1_streams,
)

GOATTRACKER2 = SidFormat(
    name="goattracker2",
    parser=Gt2Parser(),
    player=_player,
    describe=_describe_gt2,
    metadata=_metadata,
    probes=GT2_PROBES,
    grammar=packed.STREAMS,
    streams=gt2_streams,
    canonical=canonical,
)
