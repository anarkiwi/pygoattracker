# pygoattracker

Pure-Python reader, writer, and player for
[GoatTracker 2](https://sourceforge.net/projects/goattracker2/) `.SNG` songs,
with SID register-log output and audio rendering through an emulated SID.

Consumes `.sid` files (PSID/RSID containers) and bare `.prg` images through the
shared [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) base: the
packed GoatTracker playroutine (as produced by `gt2reloc`) is detected and
decompiled back to a `Song`, including crunched/relocated images whose init is
run in a 6502 emulator — container headers are not trusted.

GoatTracker V1.x packed tunes (the V1.50–V1.53 standard playroutine and the
V1.25 musicroutines 11.x) are
recognized by their assembled playroutine, decompiled to a `Gt1Song` and played
by `Gt1Player`, a playspec of the playroutine run on the tune's memory; see [docs/gt1.md](docs/gt1.md). V1.x editor songs
(`GTS!`) parse with `parse_gts1` for all three layouts — the V0.9x body (no
restart byte, `$00`-terminated wavetables), the V1.25 musicroutine body, and the
V1.4b–V1.53 body with its 256-byte filtertable — round-trip with `write_gts1`,
and `relocate` ports the editor's F9 relocator before playback (the V0.9x body
is native-only).

`TypedSong` states the same song as typed operations (commands, table steps, instruments) and round-trips losslessly to the `.sng` and to the packed bytes; see `docs/format.md`.

## Install

```bash
pip install pygoattracker
```

Reading, writing, playing, register logs, `.sid` decompiling (crunched/relocated
images run in a 6502 emulator) and WAV rendering (pylibresidfp) all come with the
core install via [`pysidtracker`](https://github.com/anarkiwi/pysidtracker).

## Usage

Recover a song from a `.sid` file:

```python
from pygoattracker import decompile_sid, write_sng

result = decompile_sid("tune.sid")     # path, bytes, or binary file object
song = result.song                     # the parsed Song model
write_sng(song, "tune.sng")            # a .SNG this library reads and plays
```

Read a GoatTracker `.SNG` song:

```python
from pygoattracker import read_sng

song = read_sng("tune.sng")
print(song.name, song.author, song.copyright)
```

The player is a `pysidtracker` playspec of the packed GoatTracker 2 playroutine
(`specs/goattracker.play`) run on the song packed as the relocator packs it; its render is
validated byte-exactly against a `sidplayfp` oracle across GoatTracker
play-routine variations (see [docs/oracle-testing.md](docs/oracle-testing.md)); [docs/spec-coverage.md](docs/spec-coverage.md) proves the playspecs equal their 6502 playroutines on every reachable branch direction.

See [docs/usage.md](docs/usage.md) for building songs, playback, register logs,
WAV rendering, NinjaTracker 2, and the command line,
[docs/format.md](docs/format.md) for the format specification, data model, and
`.sid` decompilation notes, [docs/spec-hook.md](docs/spec-hook.md) for playing a decompiled tune's own image
through the spec (`SidFormat.spec`, the in-place binder, standard notes), [docs/grammar.md](docs/grammar.md) for the GT2 and V1.x song grammars
(abstract syntax, byte encodings, playback semantics, canonical text), and [docs/oracle-testing.md](docs/oracle-testing.md)
for the byte-exact oracle tests.

Installed, it also registers the `goattracker1` and `goattracker2` formats with
the `pysidtracker` CLI (`pysidtracker info|reglog|wav|grammar tune.sid`, and
`pysidtracker equiv a.sid b.sid` for GT2); see
[docs/usage.md](docs/usage.md#pysidtracker-command-line).

## Development

```bash
pip install -e ".[dev]"
./run_tests.sh        # black + pylint + pytest (oracle included) with coverage gate
```

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
