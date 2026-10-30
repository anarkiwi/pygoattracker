"""The GoatTracker 2 spec run in place on a tune's own playroutine.

:class:`InPlace` runs the tune's init on the 6502 and each ``mt_play`` call as
``specs/goattracker.play``, bound by :func:`pygoattracker.gt2spec.binder` to the
source matched in the tune's code, its registers where the tune's own code writes
them.
"""

from __future__ import annotations

from pysidtracker import SidImage
from pysidtracker.playspec import SpecPlayer
from pysidtracker.registers import sid_model_for_flags

from pygoattracker import gt2spec
from pygoattracker.sid import decompile_sid


class InPlace(SpecPlayer):
    """Subtune ``subtune`` of the ``.sid`` ``data``, its play calls run as the spec."""

    MASK_PW_HI = True

    def __init__(self, data: bytes, subtune: int = 0, validate: bool = False):
        model = decompile_sid(data, subtune=subtune)
        self.binder = gt2spec.binder(model)
        image = SidImage.from_bytes(data)
        header = image.header
        super().__init__(
            gt2spec.spec(),
            image.image,
            image.load,
            header.init_address or image.load,
            header.play_address,
            subtune,
            bind=self.binder,
            entries=self.binder.entries,
            validate=validate,
            psid=header.is_psid,
            sid=None if header.play_address else data,
            sid_model=sid_model_for_flags(header.flags),
            ntsc=header.is_ntsc,
        )
