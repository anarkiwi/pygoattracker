"""pygoattracker's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv \
        --work .hvsc-survey --render oracle_hvsc:render
"""

from dataclasses import asdict

import numpy as np

from pygoattracker.gt1 import decompile_gt1, find_player
from pygoattracker.gt1player import Gt1Player
from pygoattracker.sid import _load_image, decompile_sid

PLAYER = "goattracker|^\\(gt"


def render_gt1(data, song, nframes):
    """Render 1-based ``song`` of a GoatTracker V1.x ``.sid``."""
    gt1 = decompile_gt1(data, subtune=song - 1)
    grid = Gt1Player(gt1, subtune=song - 1, sid=data).render_grid(nframes)
    return np.asarray(grid, np.int16), {"gt1": gt1.player}


def render(data, song, nframes):
    """Render 1-based ``song`` of a ``.sid`` and the playroutine build it decoded."""
    if find_player(_load_image(data).mem) is not None:
        return render_gt1(data, song, nframes)
    d = decompile_sid(data, subtune=song - 1)
    player = d.player()
    measured = asdict(d.info.build)
    shown = {k: v for k, v in measured.items() if v is not None}
    info = measured | asdict(d.info.playback) | shown
    del info["init_regs"]
    info.update(nowavedelay=d.info.nowavedelay, state=d.info.state is not None)
    return np.asarray(player.render_grid(nframes), np.int16), info


def player(data, song):
    """The ``MemPlayer`` of 1-based ``song`` (``python -m pysidtracker.whydiff``'s hook)."""
    if find_player(_load_image(data).mem) is not None:
        gt1 = decompile_gt1(data, subtune=song - 1)
        return Gt1Player(gt1, subtune=song - 1, sid=data)
    return decompile_sid(data, subtune=song - 1).player()
