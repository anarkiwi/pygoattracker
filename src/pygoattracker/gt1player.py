"""The GoatTracker V1.x player: the family's playspec run on the tune's memory.

The tune's own init runs on the 6502 emulator; each play call is then
``specs/gt1_<family>.play`` (:mod:`pygoattracker.gt1spec`) on that memory.
"""

from __future__ import annotations

from pysidtracker import SidImage
from pysidtracker.playspec import SpecPlayer
from pysidtracker.registers import sid_model_for_flags

from pygoattracker import gt1spec
from pygoattracker.gt1 import ORDERLIST_ENDSONG, Gt1Song, _offsets

_LOOPSONG, _ENDSONG, _REPEAT = 0xFF, 0xFE, 0xD0
_CHN = 7
_SONGPTR15 = 5


class _Sequencer:
    """A stream hook counting, per channel, the orderlist steps that loop or end:
    from the channel's position, the first entry that is not a repeat or
    transpose command (``standard`` players) is an end mark, or with
    ``after`` the pattern is followed by the loop mark."""

    def __init__(self, inner, starts: dict, songptr: int, rules: tuple, loops: dict):
        self._inner, self._starts, self._songptr = inner, starts, songptr
        self._ends, self._standard, self._after = rules
        self.loops = loops

    @property
    def results(self) -> dict:
        """The stream validator's results."""
        return self._inner.results

    def failures(self) -> dict:
        """The stream validator's failures."""
        return self._inner.failures()

    def _step(self, read, base: int, chn: int) -> None:
        pos = read(self._songptr + chn)
        entry = read((base + pos) & 0xFFFF)
        while self._standard and _REPEAT <= entry < _LOOPSONG and not self._after:
            pos = (pos + 1) & 0xFF
            entry = read((base + pos) & 0xFFFF)
        if self._after:
            while _REPEAT <= entry:
                pos = (pos + 1) & 0xFF
                entry = read((base + pos) & 0xFFFF)
            entry = read((base + ((pos + 1) & 0xFF)) & 0xFFFF)
        self.loops[chn] = self.loops.get(chn, 0) + (entry in self._ends)

    def hook(self, program, read):
        """The compiled frame's stream callback: validate, then count orderlist steps."""
        inner = self._inner.hook(program, read) if self._inner else None
        orders = [
            i for i, (ref, _) in enumerate(program.grammars) if "pattern" not in ref
        ]

        def _hook(index: int, base: int) -> None:
            if inner is not None:
                inner(index, base)
            chn = self._starts.get(base)
            if index in orders and chn is not None:
                self._step(read, base, chn)

        return _hook


class Gt1Player(SpecPlayer):
    """Play a decompiled :class:`~pygoattracker.gt1.Gt1Song` one play call per frame.

    ``sid`` is the tune's PSID/RSID (default: the song as the packer saves it); with
    ``validate`` the streams the spec opens are parsed under the song grammar.
    :attr:`loops` counts, per channel offset whose orderlist the sequencer has
    read, the loop or end marks it read.
    """

    MASK_PW_HI = True

    def __init__(
        self,
        song: Gt1Song,
        subtune: int = 0,
        sid: bytes | None = None,
        validate: bool = False,
    ):
        spec = gt1spec.spec(song.ref.family)
        image = SidImage.from_bytes(sid or song.to_sid())
        header = image.header
        self._song = song
        self.loops: dict = {}
        super().__init__(
            spec,
            image.image,
            image.load,
            header.init_address or image.load,
            header.play_address,
            subtune,
            bind=gt1spec.binding,
            entries=gt1spec.entries,
            validate=validate,
            psid=header.is_psid,
            sid_model=sid_model_for_flags(header.flags),
            ntsc=header.is_ntsc,
        )

    def _init(self, subtune: int) -> None:
        super()._init(subtune)
        song = self._song._packed()  # pylint: disable=protected-access
        orders = [o for chans in song.orderlists for o in chans]
        starts = _offsets(song.layout()["songs"], orders)
        lay = gt1spec.binding(self.view(), self.entry)
        family = song.ref.family
        rules = (
            (_LOOPSONG, _ENDSONG) if song.player in ORDERLIST_ENDSONG else (_LOOPSONG,),
            family != "11",
            bool(lay["loop_after"]),
        )
        self.validator = _Sequencer(
            self.validator,
            {addr: num % 3 * _CHN for num, addr in enumerate(starts)},
            lay.get("songptr") or lay["chn"] + _SONGPTR15,
            rules,
            self.loops,
        )
