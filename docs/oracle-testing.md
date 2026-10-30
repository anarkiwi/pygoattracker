# Oracle testing

`tests/test_oracle_hvsc.py` renders each tune's subtune with the survey renderer
(`scripts/oracle_hvsc.py`, i.e. `DecompiledSid.player()`) and asserts its per-frame
SID register grid matches [`sidtrace`](https://github.com/anarkiwi/sidtrace) — a
patched `sidplayfp` run in Docker — frame for frame, byte for byte, over the
subtune's whole HVSC `Songlengths.md5` duration (pinned per tune in the test). The
oracle trace, framing and comparison are `pysidtracker.survey`'s, so a test
passes exactly when the whole-HVSC survey calls that subtune `exact`.

The `Player` is a `pysidtracker.MemPlayer`, so `render_grid(nframes)` is exactly
the grid the oracle compares: one frame per play-routine interrupt, starting
with the call that only initialises the song, so the grids align exactly.

## Variations covered

Each tune is a distinct GoatTracker play-routine / packed-decode variation, all
byte-exact over the whole song:

| tune | variation |
| --- | --- |
| `Hammurabi` | stock frequency table, editor (full-mod) pulse |
| `A_Crack_in_the_Facade` | finetuned frequency table (high-column anchor), NOWAVEDELAY |
| `Cruiser-X_79_preview` | editor pulse, multi-subtune song(order)-table, all 3 subtunes |
| `10_Orbyte`, `Cab_Hustle` | greloc SIMPLEPULSE one-byte pulse optimization |
| `Halloween-Main_Title` | SIMPLEPULSE + NOWAVEDELAY combined |
| `Joke`, `Beautiful_Day`, `H-Fusion`, `Menace47`, `Ann_Gong` | GoatTracker V1.53, V1.52, V1.51, V1.50b, V1.50 playroutines |
| `Assatas_Song` | GoatTracker V1.52 4x multispeed (packer speed code) |
| `Latex_Sensations` | GoatTracker V1.52 behind an HVSC init stub |
| `Cheese_Wings` | full-ghosting build: `mt_play` flushes the previous call's ghost, so output runs one call late |
| `Puzzle_Bobble` | two players in one image; the build is measured on the decoded data's own player |
| `Metroid-Brinstar` | 3x multispeed: channels start on `DEFAULTTEMPO` |
| `Dirty_Cheater` | init stub writes voice 3 before the player |
| `Loader_Music`, `Ryan_Went_Sling`, `Galaga_Start_Theme`, `Heavy_Industrial_Machinery`, `Contra_Jungle_Theme` | hard-restart ADSR `$FF00` (AD stored first), `$FF00` (altplayer, ghost), `$1000`, `$FF0F`, `$0F0F` |
| `Goat_City` | NOWAVEDELAY build whose wavetable bytes read as delays: the define measured from the player |
| `2_Hours_NOT_Enough` | unoptimized packing keeps the wave-delay code with no delay rows; a `$00` pulse-table step (its time wraps to 256 modulations) |
| `Opin_Joskus` | SIMPLEPULSE measured by running the player, not inferred from the pulse table |
| `Lily_Lander` | ghost-register build detected from the player's 25-register flush |
| `Greaties_tune_3` | gate and vibrato columns of one width, told apart by what the player does with each |
| `Captain_Ishtar` 5, `Cruiser-X_79` 4, `Trance_Sector` 12 | the song the player's own song-table load selects: a wrapper-entered player's entry past the header's count |
| `Blast_em_All`, `An_UFO_Attack_from_Space` | one pulse-or-filter table, code-located: the player is seen to play it as pulse |
| `Peep_Show` | the pulse optimization measured from a set row landing on the gate timer's fetch call |
| `Lazy_Day` | a player doubling the pulse modulation speed (`asl` before the add): the step measured from a planted +1 modulation |
| `Hose_It_Out`, `Legend_of_Zelda_Preview` | an empty pattern at the image end: its row read from the power-on RAM past it |
| `Miner_Prophet_preview` | a hard-restart define assembled at two sites |
| `Easy_Going`, `GoatTracker_Classical_Example`, `Fuggbot`, `swamped` | GoatTracker V1.25 musicroutines 11.1, 11.3, 11.4, 11.5 (orderlists before the pattern table in 11.1/11.5) |
| `Phobos_2SID`, `Petal_Panic_preview` | vibrato time and delay the song-init call leaves as the image loaded them |
| `Hamburgers_Going_to_Switzerland`, `Soiled_Iron` 10 | two channels; a channel the play never runs |
| `Camel_Ohio_2SID`, `Cheezzy_Top_3SID` | stereo and 3SID builds, each channel on the chip and voice it writes |
| `Marble_Boy` | a play wrapper calling `mt_play` twice a frame |
| `I_Dont_Know_What_I_Am_Doing` | a play wrapper run as written: it flushes the player's ghost before calling it, with a cutoff sweep and volume eor of its own |
| `GoatTracker_example_MW1_title`, `Kaori_360`, `Disco_Fever`, `Plaah`, `Bee` | GoatTracker V0.96, V1.01, V1.1, V1.21, V1.22 standard playroutines (no jump table and packer init stub before V1.1, `$FE`/`$FF` orderlist ends before V1.15) |

## Running

```bash
pip install -e ".[dev]"       # needs Docker + the anarkiwi/sidtrace image
pytest -m oracle -n auto
```

HVSC `.sid` files are copyright works: they are resolved from a local `$HVSC`
tree or downloaded to pysidtracker's cache `.tunecache` (`$PYSID_TUNECACHE`
overrides it), and the oracle traces cached in `.tunecache/csv/oracle` — never
committed. The default
suite excludes `-m oracle`; a dedicated CI job runs it. To pre-fetch the tunes
the suite names, and the decompile test's corpus sample:

```bash
pysid-tune-fetch --tests tests --tests tests/data/hvsc_goattracker_sample.txt --tests tests/data/pathdiff.json
```

## Whole-HVSC survey

[`pysidtracker.survey`](https://github.com/anarkiwi/pysidtracker/blob/main/docs/survey.md)
(`pip install "pysidtracker[survey]>=0.41.0"`) compares every GoatTracker tune
in an HVSC tree against the oracle over each subtune's full HVSC song length.
`scripts/oracle_hvsc.py` holds this library's `PLAYER` SIDId regex and
`render` (`DecompiledSid.player()`), which the oracle tests above share; its `info` is the decoded
`PlayerBuild`, the `Build` it resolves to and NOWAVEDELAY, so `triage` shows
the exact rate per decoded playroutine option.

```bash
python -m pysidtracker.survey find $HVSC --player 'goattracker|^\(gt' --out tunes.csv
PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv --work .hvsc-survey --render oracle_hvsc:render
python -m pysidtracker.survey triage .hvsc-survey
python -m pysidtracker.survey compare .hvsc-survey/results.csv --base baseline/results.csv
PYTHONPATH=scripts python -m pysidtracker.survey diff $HVSC tunes.csv <md5>_<song> --work .hvsc-survey --render oracle_hvsc:render
```

`python -m pysidtracker.whydiff` explains a subtune's first difference against the
6502 player, line by line, with `oracle_hvsc:player` as the player hook:

```bash
PYTHONPATH=scripts python -m pysidtracker.whydiff $HVSC/<path>.sid oracle_hvsc:player --song 1 --frames 1000
```

## Pathdiff xfails

`tests/test_pathdiff.py` (marked `oracle`) runs `tests/data/pathdiff.json`: one
strict xfail per top fix of [`pysidtracker.pathdiff`](https://github.com/anarkiwi/pysidtracker/blob/main/docs/pathdiff.md),
the corpus-wide localisation of the survey's divergences to the fewest fixes.
Each case is the fix's representative subtune, failing with whydiff's
explanation until the fix lands. Cases name the player as `format:auto`, which
picks the same GoatTracker V1.x or V2 player as `oracle_hvsc:player`. After a
survey `run`, regenerate (existing ids are kept) or prune fixed cases:

```bash
python -m pysidtracker.pathdiff build $HVSC tunes.csv --work .hvsc-survey \
    --right scripts/oracle_hvsc.py:player --jobs 28 --force
python -m pysidtracker.pathdiff rank --work .hvsc-survey \
    --right scripts/oracle_hvsc.py:player --explain 10
python -m pysidtracker.pathdiff xfails --work .hvsc-survey \
    --right scripts/oracle_hvsc.py:player --as-right format:auto \
    --manifest tests/data/pathdiff.json --top 15 --prune
```

`scripts/build_census.py $HVSC tunes.csv` tallies which `PlayerBuild` defines the
probe measures (or cannot show) across the GoatTracker 2 tunes.

`scripts/gt1_versions.py $HVSC tunes.csv` tabulates the `GoatTracker_V1.x`
tunes by embedded playroutine and can write a per-version sample `tunes.csv`.

Python edge keys are digests of the branch statement (not line numbers), so they
survive edits to `player.py`; `xfails` re-keys any case whose key goes stale.

To diff a single tune against the oracle, use `pysidtracker`'s `whydiff` / `survey diff`
(`gridcmp.lead_report` / `window_report`).

`.hvsc-survey/` is gitignored; its CSVs derive from copyright tunes.
