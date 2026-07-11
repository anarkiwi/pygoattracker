"""Byte-exact comparison of the GoatTracker player against the sidtrace oracle.

Marked ``oracle``: these tests need Docker (the ``anarkiwi/sidtrace`` image) and
network access to HVSC, so the default suite excludes them (see ``pyproject``); a
dedicated CI job runs ``pytest -m oracle``. They are never skipped -- an
unavailable tune or a failed oracle render fails the test rather than hiding a
regression. HVSC ``.sid`` files are copyright works: they are downloaded to a
cache (or a local ``$HVSC`` tree), never committed.

Each tune is a distinct GoatTracker play-routine / packed-decode variation, and
every one must render frame-for-frame identically to the deterministic
``sidplayfp`` oracle. The player has a few leading gate-off "settling" frames
that the oracle collapses into its baseline (its gap anchor discards write-free
frames), so the render carries ``_LEAD`` extra frames and ``aligned_match``
slides over them.
"""

import os
from pathlib import Path

import pytest

from pysidtracker import make_oracle_fixtures

from pygoattracker import sid
from pygoattracker.player import Player

# HVSC .sid cache (shared with the corpus decompile test) and the sidtrace CSV
# cache. ``$PYGOATTRACKER_ORACLE_CACHE`` overrides the CSV location.
_HVSC = Path(__file__).parent / ".tunecache"
_CSV = Path(os.environ.get("PYGOATTRACKER_ORACLE_CACHE", ".oracle-cache/csv"))
_LEAD = 8

# One representative HVSC tune per play-routine / packed-decode variation, each
# verified to render byte-exactly against the deterministic sidtrace oracle:
TUNES = {
    # stock frequency table, editor (full-mod) pulse, single subtune.
    "hammurabi": "GAMES/G-L/Hammurabi.sid",
    # finetuned frequency table (stock high column, retuned low): the
    # high-column anchor fallback; NOWAVEDELAY build.
    "a_crack_in_the_facade": "DEMOS/A-F/A_Crack_in_the_Facade.sid",
    # editor pulse, multi-subtune song(order)-table (5 subtunes).
    "cruiser_x_79": "MUSICIANS/B/Bayliss_Richard/Cruiser-X_79_preview.sid",
    # greloc SIMPLEPULSE one-byte pulse optimization.
    "10_orbyte": "DEMOS/0-9/10_Orbyte.sid",
    # SIMPLEPULSE, game tune.
    "cab_hustle": "GAMES/A-F/Cab_Hustle.sid",
    # SIMPLEPULSE + NOWAVEDELAY build combined.
    "halloween_main_title": "MUSICIANS/M/Mibri/Halloween-Main_Title.sid",
}


def _render(data, nframes):
    result = sid.decompile_sid(data)
    player = Player(
        result.song,
        subtune=result.subtune,
        freq_table=result.info.freq_table,
        simplepulse=result.info.simplepulse,
        live_vibrato=result.info.live_vibrato,
    )
    return player.render_grid(nframes + _LEAD)


tune_id, oracle_match = make_oracle_fixtures(
    TUNES,
    hvsc_cache=_HVSC,
    oracle_cache=_CSV,
    render=_render,
    frames=250,
    max_lead=_LEAD,
)


@pytest.mark.oracle
def test_render_matches_oracle(oracle_match):  # noqa: F811
    oracle_match()
