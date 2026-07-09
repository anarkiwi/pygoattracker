# pygoattracker

Pure-Python reader, writer, and player for
[GoatTracker 2](https://sourceforge.net/projects/goattracker2/) `.SNG` songs,
with SID register-log output and audio rendering through an emulated SID.

Consumes `.sid` files (PSID/RSID containers) and bare `.prg` images through the
shared [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) base: the
packed GoatTracker playroutine (as produced by `gt2reloc`) is detected and
decompiled back to a `Song`, including crunched/relocated images whose init is
run in a 6502 emulator — container headers are not trusted.

## Install

```bash
pip install pygoattracker          # read/write/play/register logs
pip install pygoattracker[audio]   # + WAV rendering via pyresidfp
pip install pygoattracker[sid]     # + py65 for crunched/relocated .sid files
```

Everything except audio rendering and crunched-`.sid` emulation is stdlib only.

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

See [docs/usage.md](docs/usage.md) for building songs, playback, register logs,
WAV rendering, NinjaTracker 2, and the command line, and
[docs/format.md](docs/format.md) for the format specification, data model, and
`.sid` decompilation notes.

## Development

```bash
pip install -e ".[dev]"
./run_tests.sh        # black + pylint + pytest with coverage gate
```

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
