"""Byte-exact comparison of the GoatTracker player against the sidtrace oracle.

Marked ``oracle``: these tests need Docker (the ``anarkiwi/sidtrace`` image) and
network access to HVSC, so the default suite excludes them (see ``pyproject``); a
dedicated CI job runs ``pytest -m oracle``. They are never skipped -- an
unavailable tune or a failed oracle render fails the test rather than hiding a
regression. HVSC ``.sid`` files are copyright works: they are downloaded to a
cache (or a local ``$HVSC`` tree), never committed.

Each tune is a distinct GoatTracker play-routine / packed-decode variation, and
every one must render frame-for-frame identically to the deterministic
``sidplayfp`` oracle over its whole HVSC song length, compared exactly as the
whole-HVSC survey compares (``pysidtracker.survey``).
"""

import os
import sys
from pathlib import Path

import pytest

from pysidtracker import TuneFetchError, is_ntsc, resolve_tune, survey

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import oracle_hvsc  # noqa: E402  (after sys.path tweak)

# HVSC .sid cache (shared with the corpus decompile test) and the sidtrace trace
# cache (``<dir>/oracle``). ``$PYGOATTRACKER_ORACLE_CACHE`` overrides the latter.
_HVSC = Path(__file__).parent / ".tunecache"
_WORK = Path(os.environ.get("PYGOATTRACKER_ORACLE_CACHE", ".oracle-cache"))

# ``(HVSC path, song, Songlengths.md5 seconds)`` per play-routine / packed-decode
# variation, each byte-exact against the sidtrace oracle over the whole song:
TUNES = {
    # stock frequency table, editor (full-mod) pulse, single subtune.
    "hammurabi": ("GAMES/G-L/Hammurabi.sid", 1, 123.074),
    # finetuned frequency table (stock high column, retuned low): the
    # high-column anchor fallback; NOWAVEDELAY build.
    "a_crack_in_the_facade": ("DEMOS/A-F/A_Crack_in_the_Facade.sid", 1, 217.379),
    # editor pulse, multi-subtune song(order)-table, every subtune.
    "cruiser_x_79_1": (
        "MUSICIANS/B/Bayliss_Richard/Cruiser-X_79_preview.sid",
        1,
        199.185,
    ),
    "cruiser_x_79_2": (
        "MUSICIANS/B/Bayliss_Richard/Cruiser-X_79_preview.sid",
        2,
        1.563,
    ),
    "cruiser_x_79_3": (
        "MUSICIANS/B/Bayliss_Richard/Cruiser-X_79_preview.sid",
        3,
        6.904,
    ),
    # greloc SIMPLEPULSE one-byte pulse optimization.
    "10_orbyte": ("DEMOS/0-9/10_Orbyte.sid", 1, 77),
    # SIMPLEPULSE, game tune.
    "cab_hustle": ("GAMES/A-F/Cab_Hustle.sid", 1, 156.111),
    # SIMPLEPULSE + NOWAVEDELAY build combined.
    "halloween_main_title": ("MUSICIANS/M/Mibri/Halloween-Main_Title.sid", 1, 73.836),
    # full-ghosting build: mt_play flushes the previous call's ghost first, so
    # the init call writes the image ghost ($D418=0) and output runs one call late.
    "cheese_wings": ("MUSICIANS/R/Radiantx/Cheese_Wings.sid", 1, 219.154),
    # two players in one image: the build is read from the decoded data's own.
    "puzzle_bobble": ("MUSICIANS/N/NM156/Puzzle_Bobble.sid", 1, 51.412),
    # 3x multispeed: every channel starts on DEFAULTTEMPO (multiplier * 6 - 1).
    "metroid_brinstar": ("MUSICIANS/N/Nordischsound/Metroid-Brinstar.sid", 1, 56.848),
    # init stub writes voice 3 before the player, which holds until overwritten.
    "dirty_cheater": ("MUSICIANS/S/Spider_Jerusalem/Dirty_Cheater.sid", 1, 47),
    # hard-restart ADSR from the build, AD told from SR by store target:
    # AD stored first by player.s ($FF00), altplayer + ghost ($FF00), $1000,
    # $FF0F, $0F0F.
    "hr_ad_first": ("MUSICIANS/M/Mythus/Loader_Music.sid", 1, 15.3),
    "hr_altplayer_ghost": ("MUSICIANS/J/Jammer/Ryan_Went_Sling.sid", 1, 15.2),
    "hr_1000": ("MUSICIANS/P/Patfield_Kent/Galaga_Start_Theme.sid", 1, 7.601),
    "hr_ff0f": ("MUSICIANS/J/Joeli_Jani/Heavy_Industrial_Machinery.sid", 1, 8.147),
    "hr_0f0f": ("MUSICIANS/D/DJ_Space/Contra_Jungle_Theme.sid", 1, 109.707),
    # one define at two sites voids only that define, not the hard restart.
    "hr_ambiguous_define": ("GAMES/M-R/Miner_Prophet_preview.sid", 1, 40.24),
    # GoatTracker V1.x: one tune per native V1.5x playroutine variant.
    "gt1_153_joke": ("DEMOS/G-L/Joke.sid", 1, 254),
    "gt1_152_beautiful_day": ("DEMOS/A-F/Beautiful_Day.sid", 1, 150),
    "gt1_151_h_fusion": ("MUSICIANS/F/Finnr/H-Fusion.sid", 1, 213),
    "gt1_150b_menace47": ("MUSICIANS/D/Dexter/Menace47.sid", 1, 71),
    "gt1_150_ann_gong": ("MUSICIANS/B/Bayliss_Richard/Ann_Gong.sid", 1, 222),
    # GT1 4x multispeed (packer speed code before the player).
    "gt1_152_4x_assatas_song": ("MUSICIANS/H/Hoffmann_Michal/Assatas_Song.sid", 1, 179),
    # GT1 behind an HVSC init stub (decompiled after running init).
    "gt1_152_stub_latex_sensations": (
        "MUSICIANS/J/Jammer/Latex_Sensations.sid",
        1,
        195,
    ),
    # GT1 V1.25 musicroutines 11.x; 11.1 and 11.5 with orderlists packed before
    # the pattern table.
    "gt1_111_easy_going": ("MUSICIANS/J/Jammer/Easy_Going.sid", 1, 198),
    "gt1_113_classical_example": (
        "MUSICIANS/C/Cadaver/GoatTracker_Classical_Example.sid",
        1,
        38,
    ),
    "gt1_114_fuggbot": ("MUSICIANS/J/Josstintimberlake/Fuggbot.sid", 1, 85),
    "gt1_115_swamped": ("MUSICIANS/D/Dalezy/swamped.sid", 1, 43),
}


@pytest.mark.oracle
@pytest.mark.parametrize("tune", TUNES)
def test_render_matches_oracle_over_the_whole_song(tune):
    relpath, song, seconds = TUNES[tune]
    path = resolve_tune(relpath, cache_dir=_HVSC)
    if path is None:
        raise TuneFetchError(f"tune {relpath} unavailable (offline, not cached)")
    data = Path(path).read_bytes()
    ocsv = survey.oracle_cache(_WORK) / f"{Path(relpath).stem}_{song}.csv.zst"
    _, oracle = survey.oracle_frames(
        survey.oracle_rows(path, ocsv, seconds, song),
        seconds,
        "NTSC" if is_ntsc(data) else "PAL",
    )
    row = survey.library_row({}, oracle, oracle_hvsc.render, data, song, lead=0)
    assert row["verdict"] == "exact", {k: row.get(k) for k in survey.RESULT_FIELDS}
