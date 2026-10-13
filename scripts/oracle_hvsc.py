"""pygoattracker's renderer for the ``pysidtracker.survey`` whole-song HVSC oracle survey.

    PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv \
        --work .hvsc-survey --render oracle_hvsc:render
"""

from dataclasses import asdict

import numpy as np

from pygoattracker.player import Player
from pygoattracker.sid import decompile_sid

PLAYER = "goattracker|^\\(gt"


def render(data, song, nframes):
    """Render 1-based ``song`` of a ``.sid`` and the playroutine build it decoded."""
    d = decompile_sid(data, subtune=song - 1)
    player = Player(
        d.typed,
        subtune=d.subtune,
        freq_table=d.info.note_read,
        simplepulse=d.info.simplepulse,
        live_vibrato=d.info.live_vibrato,
    )
    info = asdict(d.info.build)
    info.update(
        simplepulse=d.info.simplepulse,
        nowavedelay=d.info.nowavedelay,
        live_vibrato=d.info.live_vibrato,
        state=d.info.state is not None,
    )
    return np.asarray(player.render_grid(nframes), np.int16), info
