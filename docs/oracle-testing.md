# Oracle testing

`tests/test_oracle_hvsc.py` renders each tune with `Player.render_grid` and
asserts the per-frame SID register grid matches
[`sidtrace`](https://github.com/anarkiwi/sidtrace) — a patched `sidplayfp` run in
Docker — frame for frame, byte for byte. It reuses `pysidtracker`'s
[`make_oracle_fixtures`](https://github.com/anarkiwi/pysidtracker/blob/main/docs/oracle-testing.md);
the flow (resolve HVSC tune → render the oracle CSV → frame it → `aligned_match`)
is documented there.

The `Player` is a `pysidtracker.MemPlayer`, so `render_grid(nframes)` is exactly
the grid the oracle compares: one frame per play-routine interrupt, starting
with the call that only initialises the song, so the grids align exactly.

## Variations covered

Each tune is a distinct GoatTracker play-routine / packed-decode variation, all
byte-exact:

| tune | variation |
| --- | --- |
| `Hammurabi` | stock frequency table, editor (full-mod) pulse |
| `A_Crack_in_the_Facade` | finetuned frequency table (high-column anchor), NOWAVEDELAY |
| `Cruiser-X_79_preview` | editor pulse, multi-subtune song(order)-table (5 subtunes) |
| `10_Orbyte`, `Cab_Hustle` | greloc SIMPLEPULSE one-byte pulse optimization |
| `Halloween-Main_Title` | SIMPLEPULSE + NOWAVEDELAY combined |
| `Joke`, `Beautiful_Day`, `H-Fusion`, `Menace47`, `Ann_Gong` | GoatTracker V1.53, V1.52, V1.51, V1.50b, V1.50 playroutines |
| `Assatas_Song` | GoatTracker V1.52 4x multispeed (packer speed code) |
| `Latex_Sensations` | GoatTracker V1.52 behind an HVSC init stub |
| `Easy_Going`, `GoatTracker_Classical_Example`, `Fuggbot`, `swamped` | GoatTracker V1.25 musicroutines 11.1, 11.3, 11.4, 11.5 (orderlists before the pattern table in 11.1/11.5) |

## Running

```bash
pip install -e ".[dev]"       # needs Docker + the anarkiwi/sidtrace image
pytest -m oracle -n auto
```

HVSC `.sid` files are copyright works: they are resolved from a local `$HVSC`
tree or downloaded to `tests/.tunecache`, and the oracle CSVs cached in
`.oracle-cache/csv` — never committed. The default suite excludes `-m oracle`; a
dedicated CI job runs it.

## Whole-HVSC survey

[`pysidtracker.survey`](https://github.com/anarkiwi/pysidtracker/blob/main/docs/survey.md)
(`pip install "pysidtracker[survey]>=0.12.0"`) compares every GoatTracker tune
in an HVSC tree against the oracle over each subtune's full HVSC song length.
`scripts/oracle_hvsc.py` holds this library's `PLAYER` SIDId regex and
`render`, which the oracle tests above share; its `info` is the decoded
`PlayerBuild` plus SIMPLEPULSE / NOWAVEDELAY / live-vibrato, so `triage` shows
the exact rate per decoded playroutine option.

```bash
python -m pysidtracker.survey find $HVSC --player 'goattracker|^\(gt' --out tunes.csv
PYTHONPATH=scripts python -m pysidtracker.survey run $HVSC tunes.csv --work .hvsc-survey --render oracle_hvsc:render
python -m pysidtracker.survey triage .hvsc-survey
PYTHONPATH=scripts python -m pysidtracker.survey diff $HVSC tunes.csv <md5>_<song> --work .hvsc-survey --render oracle_hvsc:render
```

`scripts/gt1_versions.py $HVSC tunes.csv` tabulates the `GoatTracker_V1.x`
tunes by embedded playroutine and can write a per-version sample `tunes.csv`.

`.hvsc-survey/` is gitignored; its CSVs derive from copyright tunes.
