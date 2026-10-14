"""Byte-exact comparison of the GoatTracker player against the sidtrace oracle.

Marked ``oracle``: these tests need Docker (the ``anarkiwi/sidtrace`` image) and
network access to HVSC, so the default suite excludes them (see ``pyproject``); a
dedicated CI job runs ``pytest -m oracle``. They are never skipped -- an
unavailable tune or a failed oracle render fails the test rather than hiding a
regression. HVSC ``.sid`` files are copyright works: they are downloaded to a
cache (or a local ``$HVSC`` tree), never committed.

Each tune is a distinct GoatTracker play-routine / packed-decode variation, and
every one must render frame-for-frame identically to the deterministic
``sidplayfp`` oracle, one frame per play-routine interrupt.
"""

import os
import sys
from pathlib import Path

import pytest

from pysidtracker import make_oracle_fixtures

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import oracle_hvsc  # noqa: E402  (after sys.path tweak)

# HVSC .sid cache (shared with the corpus decompile test) and the sidtrace CSV
# cache. ``$PYGOATTRACKER_ORACLE_CACHE`` overrides the CSV location.
_HVSC = Path(__file__).parent / ".tunecache"
_CSV = Path(os.environ.get("PYGOATTRACKER_ORACLE_CACHE", ".oracle-cache/csv"))

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
    # GoatTracker V1.x: one tune per native V1.5x playroutine variant.
    "gt1_153_joke": "DEMOS/G-L/Joke.sid",
    "gt1_152_beautiful_day": "DEMOS/A-F/Beautiful_Day.sid",
    "gt1_151_h_fusion": "MUSICIANS/F/Finnr/H-Fusion.sid",
    "gt1_150b_menace47": "MUSICIANS/D/Dexter/Menace47.sid",
    "gt1_150_ann_gong": "MUSICIANS/B/Bayliss_Richard/Ann_Gong.sid",
    # GT1 4x multispeed (packer speed code before the player).
    "gt1_152_4x_assatas_song": "MUSICIANS/H/Hoffmann_Michal/Assatas_Song.sid",
    # GT1 behind an HVSC init stub (decompiled after running init).
    "gt1_152_stub_latex_sensations": "MUSICIANS/J/Jammer/Latex_Sensations.sid",
}


def _render(data, nframes):
    return oracle_hvsc.render(data, 1, nframes)[0].tolist()


tune_id, oracle_match = make_oracle_fixtures(
    TUNES,
    hvsc_cache=_HVSC,
    oracle_cache=_CSV,
    render=_render,
    frames=250,
    max_lead=0,
)


@pytest.mark.oracle
def test_render_matches_oracle(oracle_match):  # noqa: F811
    oracle_match()
