"""The GoatTracker 2 player: ``specs/goattracker.play`` run on a packed song.

:class:`Player` packs the song (:mod:`pygoattracker.songimage`) as its
:class:`~pygoattracker.packed.Build` states and runs the spec at each ``mt_play``
call, routing each channel's registers to the voice the build plays it on.
"""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache

from pysidtracker.playspec import SpecPlayer
from pysidtracker.playspec.lower import elaborate
from pysidtracker.playspec.pyexec import compile_program, controls

from pygoattracker import constants as C
from pygoattracker import gt2spec
from pygoattracker.errors import GoatTrackerError
from pygoattracker.packed import Build, PlayerState
from pygoattracker.songimage import BASE, song_image
from pygoattracker.typed import TypedSong

_CHIP = 0x20
_FILTER = range(C.FC_LO_REG, C.SID_REGISTERS)


class _Reads:
    """The bus, counting each orderlist loop mark the sequencer reads."""

    def __init__(self, bus, marks: dict, loops: list):
        self._bus, self._marks, self._loops = bus, marks, loops

    def __getitem__(self, addr: int) -> int:
        chn = self._marks.get(addr)
        if chn is not None:
            self._loops[chn] += 1
        return self._bus[addr]

    def __setitem__(self, addr: int, value: int) -> None:
        self._bus[addr] = value

    def __getattr__(self, name: str):
        return getattr(self._bus, name)


@lru_cache(maxsize=256)
def _program(binding: tuple, standard: int | None) -> tuple:
    """``(program, frame)`` of the spec elaborated for the layout items ``binding``,
    reading the standard notes of clock ``standard`` (None: the image's table)."""
    program = elaborate(gt2spec.spec(), dict(binding), standard=standard)
    return program, compile_program(program)


def _home(channel: int) -> int:
    """The register offset of ``channel`` from ``$D400`` in the spec's layout."""
    return channel // C.MAX_CHN * _CHIP + channel % C.MAX_CHN * C.VOICE_REG_SIZE


def _routes(build: Build, channels: int) -> dict:
    """``{address: (address, and, or)}`` of each register store the build plays
    elsewhere (None: on no chip), from where the spec's channel and filter write."""
    overlay = build.overlay or (None,) * channels
    if build.voices is None and not any(overlay):
        return {}
    voices = build.voices or tuple(_home(c) for c in range(channels))
    out = {}
    for c, off in enumerate(voices):
        for reg in range(C.VOICE_REG_SIZE):
            mask = overlay[c] if reg == C.CONTROL_REG and overlay[c] else (0xFF, 0)
            to = None if off is None else (C.SID_BASE + off + reg, *mask)
            out[C.SID_BASE + _home(c) + reg] = to
    for group in range(0, channels, C.MAX_CHN):
        offs = [off for off in voices[group : group + C.MAX_CHN] if off is not None]
        for reg in _FILTER:
            to = (
                (C.SID_BASE + offs[0] // _CHIP * _CHIP + reg, 0xFF, 0) if offs else None
            )
            out[C.SID_BASE + _home(group) + reg] = to
    return out


class Player(SpecPlayer):
    """Play a :class:`~pygoattracker.model.Song` (or a ``TypedSong``) one call a frame.

    ``build``: the playback options (keyword ``options`` override its fields);
    ``state``: what the first call starts from; ``freq_table``: the 128 note
    frequencies; ``notes``: a clock in Hz, to answer every note-table read with the
    standard equal-tempered word at that clock; ``validate`` checks the streams
    the spec opens.
    """

    MASK_PW_HI = True

    def __init__(
        self,
        song,
        subtune: int = 0,
        build: Build | None = None,
        state: PlayerState | None = None,
        freq_table=None,
        validate: bool = False,
        notes: int | None = None,
        **options,
    ):
        build = replace(build or Build(), **options)
        if isinstance(song, TypedSong):
            build = replace(build, simplepulse=build.simplepulse or song.simplepulse)
        build = replace(
            build, adparam=build.adparam & 0xFFFF, init_regs=tuple(build.init_regs)
        )
        if not 0 <= subtune < len(song.subtunes):
            raise GoatTrackerError(f"no such subtune: {subtune}")
        self.build = build
        image = song_image(song, build, state, freq_table)
        self.image = image
        channels = len(build.voices or song.subtunes[subtune].channels)
        self._routes = _routes(build, channels)
        self._muted: dict = {}
        self._loops = None
        self._channels = channels
        super().__init__(
            gt2spec.spec(),
            image.data,
            BASE,
            image.init,
            image.play,
            subtune,
            bind=lambda view, entry: image.layout,
            entries=lambda view: {image.entry},
            validate=validate,
            notes=notes,
        )
        self.reroute(self._routes, self._muted, image.ghost)
        if image.direct:
            self.play_after = (image.direct, image.entry)

    @property
    def loops(self) -> list:
        """Each channel's orderlist loop marks read since this was first read."""
        if self._loops is None:
            self._loops = [0] * self._channels
            self._bus = _Reads(self._bus, self.image.marks, self._loops)
        return self._loops

    @property
    def simplepulse(self) -> bool:
        """Whether the packed SIMPLEPULSE pulse code runs."""
        return self.build.simplepulse

    @property
    def live_vibrato(self) -> bool:
        """Whether instrument vibrato reads the current instrument's param."""
        return self.build.live_vibrato

    def mute(self, channel: int, muted: bool = True) -> None:
        """Mute or unmute a voice: a muted one's control byte is $08 and its
        frequency and pulse are left as they are."""
        home = C.SID_BASE + _home(channel)
        for reg in range(C.CONTROL_REG + 1):
            if muted:
                self._muted[home + reg] = reg == C.CONTROL_REG
            else:
                self._muted.pop(home + reg, None)
        self.reroute(self._routes, self._muted, self.image.ghost)

    @property
    def playing(self) -> bool:
        """The packed playroutine plays on for as long as it is called."""
        return True

    def _init(self, subtune: int) -> None:
        super()._init(subtune)
        if self._loops is not None:
            self._bus = _Reads(self._bus, self.image.marks, self._loops)

    def _instance(self, entry: int) -> tuple:
        key = (self._bus.bits, entry)
        if key not in self._compiled:
            program, frame = _program(
                tuple(sorted(self.image.layout.items())), self._standard
            )
            self._compiled[key] = (program, frame, controls(program))
        return self._compiled[key]

    def tick(self, sink: int | None = None) -> dict:
        """One play call as ``{address: registers}`` blocks: the registers to
        ``sink`` (a ghost a play wrapper flushes), else each chip's at its SID."""
        self._frame()
        lay = self.image.layout
        if sink is not None:
            base = C.SID_BASE if self.image.ghost is None else self.image.ghost
            return {sink: self._mem[base : base + C.SID_REGISTERS]}
        bases = [C.SID_BASE + chip * _CHIP for chip in range(1 + lay["stereo"])]
        return {b: self._mem[b : b + C.SID_REGISTERS] for b in bases}


def iter_frames(
    song,
    subtune: int = 0,
    max_frames: int | None = None,
    until_loop: bool = False,
    **player_options,
):
    """Yield per-frame register write lists for ``song``: ``max_frames`` frames,
    or with ``until_loop`` until every channel has looped at least once."""
    player = Player(song, subtune=subtune, **player_options)
    frame = 0
    while max_frames is None or frame < max_frames:
        if until_loop and min(player.loops) > 0:
            return
        yield player.play_frame()
        frame += 1
