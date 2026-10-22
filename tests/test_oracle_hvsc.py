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
    # the build's pulse/realtime skips off: the editor's skips must not apply.
    "burning_madness": ("MUSICIANS/N/Narciso/Burning_Madness.sid", 1, 108.61),
    "monkey_island_2_bone_song": (
        "MUSICIANS/N/Nordischsound/Monkey_Island_2-Bone_Song.sid",
        1,
        53.587,
    ),
    "terranigma_prime_blue": (
        "MUSICIANS/N/Nordischsound/Terranigma-Prime_Blue.sid",
        1,
        33.528,
    ),
    # NOWAVEDELAY build whose wavetable bytes read as delays.
    "goat_city": ("DEMOS/G-L/Goat_City.sid", 1, 45.986),
    # wave-delay code without delay rows; a $00 pulse step wraps to 256 steps.
    "2_hours_not_enough": ("DEMOS/0-9/2_Hours_NOT_Enough.sid", 1, 59.0),
    # SIMPLEPULSE measured from the player, not inferred from the pulse table.
    "opin_joskus": ("DEMOS/M-R/Opin_Joskus.sid", 1, 123.0),
    # a ghost-register build seen flushing all 25 registers each call.
    "lily_lander": ("GAMES/G-L/Lily_Lander.sid", 1, 53.906),
    # gate and vibrato columns of one width, told apart by what the player does.
    "greaties_tune_3": ("MUSICIANS/R/Roly/Greaties_tune_3.sid", 1, 34.475),
    # the song the player's own song-table load selects: a wrapper-entered
    # player's entry, past the header's count.
    "captain_ishtar_5": ("MUSICIANS/B/Bayliss_Richard/Captain_Ishtar.sid", 5, 6.744),
    "cruiser_x_79_4": ("MUSICIANS/B/Bayliss_Richard/Cruiser-X_79.sid", 4, 7.745),
    "trance_sector_12": ("MUSICIANS/B/Bayliss_Richard/Trance_Sector.sid", 12, 9),
    # a lone pulse table the code-located layout reads as the player plays it.
    "blast_em_all": ("MUSICIANS/J/Jammer/Blast_em_All.sid", 1, 155.413),
    "an_ufo_attack": ("MUSICIANS/M/Mutetus/An_UFO_Attack_from_Space.sid", 1, 144.62),
    # the pulse optimization measured from a set row on the gate timer's fetch.
    "peep_show": ("MUSICIANS/M/MCH/Peep_Show.sid", 1, 40.52),
    # an empty pattern past the image end, read from the power-on RAM there.
    "hose_it_out": ("MUSICIANS/N/Narciso/Hose_It_Out.sid", 1, 38.804),
    "legend_of_zelda_preview": ("GAMES/G-L/Legend_of_Zelda_Preview.sid", 1, 18.534),
    # tables the executed-row tiling could not segment.
    "aristillus": ("DEMOS/A-F/Aristillus.sid", 1, 104.002),
    # song tables away from the frequency table.
    "shortcut": ("DEMOS/S-Z/Shortcut.sid", 1, 137.259),
    # high song/pattern columns first, instrument columns reordered.
    "z303r": ("MUSICIANS/X/Xiny6581/Z303R.sid", 1, 30.664),
    # the vibrato delay column apart from its parameter.
    "mannschaftsairbus": ("MUSICIANS/D/Dalezy/Mannschaftsairbus.sid", 1, 199.524),
    # greloc's speed zero rows with no speed effect seen.
    "howdy_hackers": ("MUSICIANS/D/Dalezy/Howdy_Hackers.sid", 1, 40.859),
    # a song table its init writes.
    "joelbitar_3": ("DEMOS/UNKNOWN/Joelbitar.sid", 3, 50.0),
    # orderlists outside the loaded image.
    "deathflood_5": ("GAMES/A-F/Deathflood.sid", 5, 126.406),
    # instrument 1 has no wave program.
    "zelda_great_sea": (
        "MUSICIANS/N/Nordischsound/Zelda_Wind_Waker-Great_Sea.sid",
        1,
        67.035,
    ),
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
    # GT1 standard playroutines before V1.25: V0.96 (no jump table, packer init
    # stub, pre-V1.01 frequency table), V1.01 ($FE repeats the last pattern),
    # V1.1 (jump table, $FF loops to the start), V1.21, V1.22.
    "gt1_096_mw1_title": (
        "MUSICIANS/C/Cadaver/GoatTracker_example_MW1_title.sid",
        1,
        39,
    ),
    "gt1_101_kaori_360": ("DEMOS/G-L/Kaori_360.sid", 1, 80),
    "gt1_110_disco_fever": ("MUSICIANS/H/Hoffmann_Michal/Disco_Fever.sid", 1, 80),
    "gt1_121_plaah": ("MUSICIANS/H/Hukka/Plaah.sid", 1, 62),
    "gt1_122_bee": ("MUSICIANS/H/Hukka/Bee.sid", 1, 154),
    # a retuned table whose low column the code indexes apart from the
    # high-column anchor: the note table read at the code's own bases.
    "frogger_rags": ("MUSICIANS/D/DJ_Space/Frogger_Rags.sid", 1, 158.366),
    # GT1 V1.4b playroutines: standard, 1-frame hard restart, multispeed.
    "gt1_14b_asynkro_theme": ("DEMOS/A-F/Asynkro_Theme.sid", 1, 54),
    "gt1_14b_hr1_sprogs": ("MUSICIANS/S/Sack/Sprogs.sid", 1, 45.967),
    "gt1_14b_multi_suomen_kunnia": (
        "MUSICIANS/M/Mankeli/Suomen_Kunnia_on_Kyseessae.sid",
        1,
        26,
    ),
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
