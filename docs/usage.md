# Usage

For reading `.SNG` songs and recovering songs from `.sid` files, see the
[README](../README.md). This document covers the rest of the API. For the format
and data model, see [format.md](format.md).

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

`read_sng` accepts a path, bytes, or a binary file object.

## GoatTracker V1.x packed tunes

```python
from pygoattracker import Gt1Player, decompile_gt1

gt1 = decompile_gt1(open("tune.sid", "rb").read(), subtune=0)
print(gt1.player, len(gt1.patterns), gt1.orderlists[0])
grid = Gt1Player(gt1, subtune=0).render_grid(500)   # 25 SID registers per frame
address, data = gt1.pack()                          # the packed bytes again
```

See [gt1.md](gt1.md) for the supported playroutines.

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

The writer validates format limits and references before emitting anything.

## Play a song: SID register writes

`Player` is a single class that transcribes the whole GoatTracker 2 playroutine
and derives from [`pysidtracker.MemPlayer`](https://github.com/anarkiwi/pysidtracker),
so it inherits the base per-frame machinery — `play_frame` (this frame's changed
`(reg, value)` writes), `render_grid` (forward-filled 25-register per-frame grid,
`$D400..$D418`) and `iter_frames`:

```python
from pygoattracker import Player, read_sng

player = Player(read_sng("tune.sng"), subtune=0)
for _ in range(50 * 60):                  # one minute at 50 Hz
    for reg, value in player.play_frame():
        print(f"${0xD400 + reg:04X} = ${value:02X}")

grid = Player(read_sng("tune.sng")).render_grid(250)   # 250 x 25 register grid
```

Playback options default to the editor's; a `Build` (or its field names as
keywords, which override it) selects others:

```python
from pygoattracker import Build

Player(song, build=Build(adparam=0x0F00, simplepulse=True))
Player(song, adparam=0x0F00, simplepulse=True)          # the same player
```

A decompiled `.sid` plays as its own player build (hard-restart ADSR, default
tempo, init state, frequency table, pulse code):

```python
from pygoattracker import decompile_sid

grid = decompile_sid("tune.sid", subtune=0).player().render_grid(250)
```

The render is validated byte-exactly against a `sidplayfp` oracle — see
[oracle-testing.md](oracle-testing.md).

## Write a SID register log

```python
from pygoattracker import iter_register_writes, write_reglog, read_sng
from pygoattracker.reglog import REGLOG_HEADER

song = read_sng("tune.sng")
writes = iter_register_writes(song, until_loop=True)
write_reglog(writes, "tune.reglog", header=REGLOG_HEADER)
```

Logs load straight into pandas:

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

## NinjaTracker 2

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

### Convert GoatTracker songs to NinjaTracker 2

```python
from pygoattracker import gt_to_nt2, read_sng, write_nt2

song = read_sng("tune.sng")
report = []
converted = gt_to_nt2(song, errors="drop", report=report)
write_nt2(converted, "tune.nt2")
print(report)   # one line per feature NinjaTracker cannot express
```

See [format.md](format.md) for the conversion's mapping rules and limits.

## Command line

```bash
pygoattracker info tune.sng        # also detects NinjaTracker 2 and GTS! (GoatTracker 1.x) files
pygoattracker reglog tune.sng tune.reglog --seconds 30
pygoattracker wav tune.sng tune.wav --seconds 30 --model 6581
pygoattracker nt2 tune.sng tune.nt2 --lenient
pygoattracker sid2sng tune.sid tune.sng   # decompile a packed GoatTracker .sid
pygoattracker sng2sid tune.sid tune.sng out.sid  # a .sng or song text into tune.sid's player
pygoattracker text tune.sng tune.txt     # the song in its grammar, GT2 or V1.x (docs/grammar.md)
pygoattracker fromtext tune.txt tune.sng # and back
pygoattracker grammar tune.sid           # pysidtracker grammar, plus V1.x GTS! songs
```

## pysidtracker command line

Installing pygoattracker registers two formats with the generic
[`pysidtracker`](https://github.com/anarkiwi/pysidtracker) tools:
`goattracker2` (GoatTracker 2 packed `.sid`) and `goattracker1` (GoatTracker
V1.x packed `.sid`). The format is picked by recognizing the tune.

```bash
pysidtracker info tune.sid                     # metadata, format, subtunes/patterns/instruments
pysidtracker reglog tune.sid tune.reglog --seconds 30
pysidtracker wav tune.sid tune.wav --seconds 30
pysidtracker grammar tune.sid                  # every packed stream's byte-grammar parse tree
pysidtracker equiv tune.sid out.sid            # exit 0 if both play the same (GT2; docs/grammar.md)
python -m pysidtracker.whydiff tune.sid --format auto   # first divergence vs the 6502 player
python -m pysidtracker.survey run HVSC TUNES --work DIR --format goattracker2
```
