# Oracle testing

`tests/test_oracle_hvsc.py` renders each tune with `Player.render_grid` and
asserts the per-frame SID register grid matches
[`sidtrace`](https://github.com/anarkiwi/sidtrace) — a patched `sidplayfp` run in
Docker — frame for frame, byte for byte. It reuses `pysidtracker`'s
[`make_oracle_fixtures`](https://github.com/anarkiwi/pysidtracker/blob/main/docs/oracle-testing.md);
the flow (resolve HVSC tune → render the oracle CSV → frame it → `aligned_match`)
is documented there.

The `Player` is a `pysidtracker.MemPlayer`, so `render_grid(nframes)` is exactly
the grid the oracle compares. The player has a few leading gate-off "settling"
frames the oracle collapses into its baseline (its gap anchor discards
write-free frames), so the render carries a small `_LEAD` margin and
`aligned_match` slides over it.

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

## Running

```bash
pip install -e ".[dev]"       # needs Docker + the anarkiwi/sidtrace image
pytest -m oracle -n auto
```

HVSC `.sid` files are copyright works: they are resolved from a local `$HVSC`
tree or downloaded to `tests/.tunecache`, and the oracle CSVs cached in
`.oracle-cache/csv` — never committed. The default suite excludes `-m oracle`; a
dedicated CI job runs it.
