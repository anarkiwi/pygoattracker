"""The GoatTracker 2 spec run in place on a tune's own playroutine.

:class:`InPlace` runs the tune's init on the 6502 and each ``mt_play`` call as
``specs/goattracker.play``, bound to the source :mod:`tests._gt2match` matches in the
tune's code, its registers where the tune's own code writes them.
"""

from __future__ import annotations

from pysidtracker import SidImage
from pysidtracker.playspec import SpecPlayer
from pysidtracker.registers import sid_model_for_flags

from pygoattracker import gt2spec
from pygoattracker.errors import SidParseError
from pygoattracker.sid import decompile_sid
from tests import _gt2match


class InPlace(SpecPlayer):
    """Subtune ``subtune`` of the ``.sid`` ``data``, its play calls run as the spec."""

    MASK_PW_HI = True

    def __init__(self, data: bytes, subtune: int = 0, validate: bool = False):
        info = decompile_sid(data, subtune=subtune).info
        self.code, self._idle = info.code, tuple(info.playback.idle)
        self._found = None
        image = SidImage.from_bytes(data)
        header = image.header
        super().__init__(
            gt2spec.spec(),
            image.image,
            image.load,
            header.init_address or image.load,
            header.play_address,
            subtune,
            bind=self.binding,
            entries=lambda view: {self.found(view)["mt_play"]},
            validate=validate,
            psid=header.is_psid,
            sid=None if header.play_address else data,
            sid_model=sid_model_for_flags(header.flags),
            ntsc=header.is_ntsc,
        )

    def found(self, view) -> gt2spec.Match:
        """The playroutine source matched in ``view``."""
        if self._found is None:
            self._found = _gt2match.match(view, *self.code)
        if self._found is None:
            raise SidParseError("no GoatTracker 2 playroutine source matches the code")
        return self._found

    def binding(self, view, entry: int) -> dict:  # pylint: disable=unused-argument
        """The spec layout of the playroutine in ``view``."""
        found = self.found(view)
        nchan = found.get("NUMCHANNELS", 3)
        return gt2spec.layout(found, [c for c in range(nchan) if c not in self._idle])
