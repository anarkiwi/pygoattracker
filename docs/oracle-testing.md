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
| `Miner_Prophet_preview` | a hard-restart define assembled at two sites |
| `Easy_Going`, `GoatTracker_Classical_Example`, `Fuggbot`, `swamped` | GoatTracker V1.25 musicroutines 11.1, 11.3, 11.4, 11.5 (orderlists before the pattern table in 11.1/11.5) |

## Running

```bash
pip install -e ".[dev]"       # needs Docker + the anarkiwi/sidtrace image
pytest -m oracle -n auto
```

HVSC `.sid` files are copyright works: they are resolved from a local `$HVSC`
tree or downloaded to `tests/.tunecache`, and the oracle CSVs cached in
`.oracle-cache/oracle` — never committed. The default suite excludes `-m oracle`; a
dedicated CI job runs it.

## Whole-HVSC survey

[`pysidtracker.survey`](https://github.com/anarkiwi/pysidtracker/blob/main/docs/survey.md)
(`pip install "pysidtracker[survey]>=0.12.0"`) compares every GoatTracker tune
in an HVSC tree against the oracle over each subtune's full HVSC song length.
`scripts/oracle_hvsc.py` holds this library's `PLAYER` SIDId regex and
`render` (`DecompiledSid.player()`), which the oracle tests above share; its `info` is the decoded
`PlayerBuild` plus SIMPLEPULSE / NOWAVEDELAY / live-vibrato, so `triage` shows
the exact rate per decoded playroutine option.

```bash
python -m pysidtracker.survey find $HVSC --player 'goattracker|^\(gt' --out tunes.csv
PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv --work .hvsc-survey --render oracle_hvsc:render
python -m pysidtracker.survey triage .hvsc-survey
PYTHONPATH=scripts python -m pysidtracker.survey diff $HVSC tunes.csv <md5>_<song> --work .hvsc-survey --render oracle_hvsc:render
```

`python -m pysidtracker.whydiff` explains a subtune's first difference against the
6502 player, line by line, with `oracle_hvsc:player` as the player hook:

```bash
PYTHONPATH=scripts python -m pysidtracker.whydiff $HVSC/<path>.sid oracle_hvsc:player --song 1 --frames 1000
```

`scripts/build_census.py $HVSC tunes.csv` tallies which `PlayerBuild` defines the
probe measures (or cannot show) across the GoatTracker 2 tunes.

`scripts/gt1_versions.py $HVSC tunes.csv` tabulates the `GoatTracker_V1.x`
tunes by embedded playroutine and can write a per-version sample `tunes.csv`.

`.hvsc-survey/` is gitignored; its CSVs derive from copyright tunes.
