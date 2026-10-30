# Playing a tune's own image through the spec

`SidFormat.spec` of both registered formats returns a
`pysidtracker.formats.SpecBinding` for a decompiled model's own tune image, so

```python
from pysidtracker import discover_formats

fmt = {f.name: f for f in discover_formats()}["goattracker2"]
model = fmt.load(open("tune.sid", "rb").read())
grid = fmt.spec_player(model).render_grid(500)                 # the tune's note table
std = fmt.spec_player(model, notes=985248).render_grid(500)    # standard notes at the clock
```

runs the tune's init on the 6502 and each play call as the spec, bound in place to the
playroutine in the tune's code. `GOATTRACKER1.spec` binds the V1.x family spec
(`gt1spec.spec_binding(model)`); `GOATTRACKER2.spec` binds `specs/goattracker.play`
(`gt2spec.spec_binding(model)`). Build differences are layout flags and variants of the
spec, never `native` hooks. A tune the player must host for its own interrupts takes
`sid=` (its PSID/RSID bytes) among the `spec_player` keywords; `sid_model=` and `ntsc=`
come from the header the same way.

## GoatTracker 2 binder

`DecompiledSid.start` is the 0-based PSID song the init is called with
(`DecompiledSid.subtune` is the song of the packed song table it selects).

| API | |
| --- | --- |
| `gt2spec.binder(model)` | the `Binder` of a `DecompiledSid` |
| `Binder(code, idle=())` | `code` is the playroutine's `(start, end)` in the image, `idle` the channels its play skips |
| `Binder.match(view)` | the `gt2match.Match` of the playroutine source found in `view`: labels, relocator defines, operand values |
| `Binder(view, entry)` | the spec layout (`gt2spec.layout(match, ...)`), the `bind` of a `SpecPlayer` |
| `Binder.entries(view)` | `{mt_play}`, the `entries` of a `SpecPlayer` |
| `gt2spec.spec_binding(model)` | the `SpecBinding` of the model's own image |

`gt2match.match(view, start, end)` walks the playroutine source trees depth first over the
image's bytes, taking each conditional block the bytes and the defines chosen so far
allow. The trees are `gt2sources.sources()`, parsed from the pinned GoatTracker 2 release
archives (`gt2sources.ARCHIVES`), which are downloaded on first use into
`pysidtracker`'s download cache, verified by SHA-256 and never shipped.

## Standard notes

The note-table cells of the specs declare which note each index means
(`notes(part, K)`, see pysidtracker's `docs/playspec.md`); `notes=clock` answers every
read of them with `pysidtracker.notes.frequency(note, clock)`.

| spec | cell | declaration | K |
| --- | --- | --- | --- |
| `goattracker.play` | `flo`, `fhi` | `notes(lo, 0)`, `notes(hi, 0)` | 0 |
| `goattracker.play` | `flo1`, `fhi1` | `notes(lo, 1)`, `notes(hi, 1)` | 1 |
| `gt1_11.play`, `gt1_14.play`, `gt1_15.play` | `freqlo`, `freqhi` | `notes(lo, 0)`, `notes(hi, 0)` | 0 |

Index `i` of a table is the note `i` semitones above C0: the spec's GoatTracker 2 note
is the editor's note byte less `constants.FIRSTNOTE`, and the V1.x pattern note byte is
the index. `flo1` and `fhi1` are the same table read one entry on, for the distance to
the next note a calculated speed shifts.

`tests/test_spec_hook.py` checks the declarations against the standard formula (relative
wavetable notes to the table end and past it, a calculated portamento speed), and the
tunes' own code run on a copy whose note table holds the standard words must play what
the spec plays with `notes=clock` (`tests/test_gt2_inplace.py`,
`tests/test_gt1_playspec.py`, marked `oracle`).
