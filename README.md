# pygoattracker

Pure-Python reader, writer, and player for
[GoatTracker 2](https://sourceforge.net/projects/goattracker2/) `.SNG`
songs, with SID register log output and audio rendering through an
emulated SID.

GoatTracker is a family of tracker applications for composing C64/SID
music by Lasse Öörni (Cadaver). pygoattracker implements the GTS5 song
format and the playroutine from first principles, following the
GoatTracker 2.76 format documentation and `gplay.c`.

## Install

```bash
pip install pygoattracker          # read/write/play/register logs
pip install pygoattracker[audio]   # + WAV rendering via pyresidfp
```

No required dependencies: everything except audio rendering is stdlib
only.

## Read a song

```python
from pygoattracker import read_sng

song = read_sng("tune.sng")
print(song.name, song.author, song.copyright)

for instrument in song.instruments:
    print(instrument.name, hex(instrument.attack_decay))

# Patterns are typed rows; orderlists are typed entries.
for row in song.patterns[0].rows[:4]:
    print(row)                      # e.g. "C-4 01000"
print(song.subtunes[0].channels[0].entries)
```

`read_sng` accepts a path, bytes, or a binary file object, and reads
every GoatTracker song generation: GTS5/GTS4/GTS3, GTS2 (early
3-table GoatTracker 2.xx) and GTS! (GoatTracker 1.x). Old formats are
converted on load exactly as GoatTracker 2.76 imports them --
including GT1's inline instrument wavetables, synthesized pulse
programs, filtertable conversion and 0XY-arpeggio extraction -- so
they play and write back as GTS5. The writer always emits GTS5;
GTS5 read -> write round trips are byte-identical.

## Build a song from scratch

```python
from pygoattracker import (
    Instrument, Pattern, Row, Song, write_sng,
)
from pygoattracker.constants import note_value

song = Song(name="DEMO", author="ME", copyright="2026")

# A pulse waveform program: one wavetable row + stop.
wave_ptr = song.wavetable.add(0x41, 0x00)
song.wavetable.add(0xFF, 0x00)

song.instruments.append(
    Instrument(
        attack_decay=0x09,
        sustain_release=0x00,
        wave_ptr=wave_ptr,
        gateoff_timer=2,
        first_wave=0x09,           # test+gate on the init frame
        name="LEAD",
    )
)

pattern = Pattern.empty(16)
pattern.rows[0] = Row(note=note_value("C-4"), instrument=1)
pattern.rows[8] = Row(note=note_value("G-4"), instrument=1)
song.patterns = [pattern]

write_sng(song, "demo.sng")        # loads in GoatTracker 2
```

The writer validates format limits and references (patterns,
instruments, table pointers) before emitting anything; you describe
content, not bytes.

## Play a song: SID register writes

The player ports the GoatTracker 2 playroutine tick for tick:
sequencer (transpose/repeat/restart), funktempo, wave/pulse/filter
table execution including wavetable command execution, speedtable
vibrato/portamento (including note-independent speeds), gateoff timer,
and hard restart.

```python
from pygoattracker import Player, read_sng

player = Player(read_sng("tune.sng"), subtune=0)
for _ in range(50 * 60):                  # one minute at 50 Hz
    for reg, value in player.play_frame():
        print(f"${0xD400 + reg:04X} = ${value:02X}")
```

`play_frame()` returns one PAL frame's register writes in ascending
register order (the first frame initializes all 25 registers).

Not implemented: multispeed playback and the editor's jamming /
mid-song start modes.

## Write a SID register log

```python
from pygoattracker import iter_register_writes, write_reglog, read_sng

song = read_sng("tune.sng")
writes = iter_register_writes(song, until_loop=True)
write_reglog(writes, "tune.reglog")
```

Logs are one `clock reg val` triple per line (absolute clock in PAL
CPU cycles, decimal, `#` comments). They load straight into pandas:

```python
import pandas as pd

df = pd.read_csv(
    "tune.reglog", sep=" ", comment="#", names=["clock", "reg", "val"]
)
```

`read_reglog` reads the format back as a list of `RegWrite` tuples.

## Render through an emulated SID

```python
from pygoattracker import read_sng, render_wav

render_wav(read_sng("tune.sng"), "tune.wav", seconds=60, model="8580")
```

Rendering drives [pyresidfp](https://pypi.org/project/pyresidfp/)
(reSIDfp emulation), clocking each register write individually at the
same in-frame offsets the register log uses. `render_samples` returns
raw 16-bit samples instead; pass `device=` to use any other emulator
object with `write_register`/`clock`/`sampling_frequency`.

## NinjaTracker 2

[NinjaTracker 2](http://covertbitops.c64.org) songs (the C64 editor's
`N2` work files) read and write through their own typed model:

```python
from pygoattracker import read_nt2, write_nt2

song = read_nt2("tune")            # files saved by the C64 editor
print(song.hr_param, song.first_wave)
for command in song.commands:      # NT2 commands double as instruments
    print(command.name, hex(command.attack_decay))
for row in song.patterns[0].rows[:4]:
    print(row)                     # e.g. "C-2 01 08"
write_nt2(song, "tune.out")
```

Tracks reuse the typed `PlayPattern`/`Transpose` entries (NinjaTracker
transposes are -64..+63 halftones; byte `$FF` is zero -- the format
doc's "$C0 = zero" does not match the player or the example tunes).
The writer emits canonical output: stale editor bytes after pattern
terminators are not preserved, so real files round-trip semantically
(byte-identically when they carry no stale bytes). There is no
NinjaTracker playroutine port; parsing and writing only.

## Command line

```bash
pygoattracker info tune.sng        # also detects NinjaTracker 2 files
pygoattracker reglog tune.sng tune.reglog --seconds 30
pygoattracker wav tune.sng tune.wav --seconds 30 --model 6581
```

## Tests

```bash
pip install -e ".[dev]"
./run_tests.sh        # black + pylint + pytest with coverage gate
```

CI (`.github/workflows/ci.yml`) runs black, pylint, and the test suite
with a `--cov-fail-under=85` coverage gate on Python 3.10-3.13, and
builds + smoke tests the wheel. Publishing a GitHub release uploads
sdist + wheel to PyPI via trusted publishing
(`.github/workflows/publish.yml`); Dependabot keeps dependencies and
actions current. The suite also enforces the gates from
within: `tests/test_lint.py` runs black/pylint and
`tests/test_coverage.py` re-runs the suite under coverage and fails
below the floor, so a plain `pytest` cannot pass with lint errors or
insufficient coverage.

The integration tests download three GoatTracker 2 example songs and
the NinjaTracker 2 distribution disk image (SHA-256 pinned; a minimal
1541 reader extracts the six example tunes from the .d64). They verify
byte-identical .SNG round trips plus 500 frames of playback each, and
NinjaTracker round trips against all six tunes; they skip offline.

## License

Apache 2.0 - see [`LICENSE`](LICENSE).
