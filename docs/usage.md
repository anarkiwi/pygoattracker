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

```python
from pygoattracker import Player, read_sng

player = Player(read_sng("tune.sng"), subtune=0)
for _ in range(50 * 60):                  # one minute at 50 Hz
    for reg, value in player.play_frame():
        print(f"${0xD400 + reg:04X} = ${value:02X}")
```

## Write a SID register log

```python
from pygoattracker import iter_register_writes, write_reglog, read_sng

song = read_sng("tune.sng")
writes = iter_register_writes(song, until_loop=True)
write_reglog(writes, "tune.reglog")
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
pygoattracker info tune.sng        # also detects NinjaTracker 2 files
pygoattracker reglog tune.sng tune.reglog --seconds 30
pygoattracker wav tune.sng tune.wav --seconds 30 --model 6581
pygoattracker nt2 tune.sng tune.nt2 --lenient
pygoattracker sid2sng tune.sid tune.sng   # decompile a packed GoatTracker .sid
```
