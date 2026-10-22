# GoatTracker format

## Overview

GoatTracker is a family of tracker applications for composing C64/SID music by
Lasse Öörni (Cadaver). pygoattracker implements the GTS5 song format and the
playroutine from first principles, following the GoatTracker 2.76 format
documentation and `gplay.c`.

`read_sng` reads every GoatTracker song generation: GTS5/GTS4/GTS3 and GTS2
(early 3-table GoatTracker 2.xx) as the GT2 model, converted on load exactly
as GoatTracker 2.76 imports them, and GTS! (GoatTracker 1.x) natively as a
`Gt1Song`. The GTS2/GTS3 bodies keep the GT2 import path (inline instrument
wavetables, synthesized pulse programs, 0XY-arpeggio extraction) so they play
and write back as GTS5. The three `GTS!` layouts — the V0.9x body (no
restart byte, `$00`-terminated wavetables), the V1.25 musicroutine body, and
the V1.4b–V1.53 body with its 256-byte filtertable — all round-trip byte for
byte with `parse_gts1`/`write_gts1`; `relocate` packs the V1.25 and
V1.4b–V1.53 bodies for `Gt1Player` exactly as the editor's F9 relocator does
(V0.9x is native-only). See [gt1.md](gt1.md#v1x-editor-songs).
`read_sng(data, convert=True)` keeps GoatTracker 2.76's lossy GT2 import of
the V1.4b–V1.53 layout as a `Song`; the V0.9x and V1.25 bodies are native
only. The writer always emits GTS5; GTS5 read → write round trips are
byte-identical.

## Container and detection notes

`.sid` files (PSID/RSID) wrap a 6502 program: the GoatTracker *packed*
playroutine (as produced by `gt2reloc`) followed by the song data.
`decompile_sid` locates that data in the loaded image and inverts it back into a
`Song`. `result.info` carries the packed playroutine options (`freq_table`,
`simplepulse`) for building a matching `Player`.

`GoatTrackerSidParser` (the `goattracker2` [pysidtracker format](usage.md#pysidtracker-command-line))
wraps this: `parse(data, subtune=k)` returns the `DecompiledSid` of subtune `k`,
whose `player()` plays it, and `recognize` returns the frequency-table anchor
only when the image also holds the packed player's pattern-fetch idiom (the
pattern-pointer idiom below), since the standard frequency table alone appears
in most trackers' tunes. `Gt1SidParser` (`goattracker1`) does the same for
GoatTracker V1.x tunes, returning a `DecompiledGt1`.

The decompiler reads the frequency table, orderlists, packed patterns,
per-field instrument arrays and the four wave/pulse/filter/speed tables,
reversing greloc's byte transforms to editor form. The build flags that live in
the player *code* rather than the song data (which instrument fields are
present, table lengths, `nowavedelay`, `simplepulse`) are recovered by
re-deriving greloc's deterministic rules and by an exact-fit tiling of the
instrument/table region.

### Locating tables from the player code

gt2reloc shipped many packer/player revisions whose table geometry differs, and
several do not lay the song data out immediately after the frequency table. The
absolute base of every table is baked into the playroutine as an `LDA table,Y`
(`$B9`) operand, and the player relocates as one block, so those operands are
version-tolerant anchors. `decompile_sid` reads them directly with
[`pysidtracker`](https://github.com/anarkiwi/pysidtracker)'s masked-opcode
code-scan (`CodePattern` / `find_code_all`):

- **song(order)-table** — the sequencer idiom `LDA songtbllo,Y; STA zp;
  LDA songtblhi,Y; STA zp; LDY chn,X; LDA (zp),Y; CMP #$FF` gives the base and
  subtune count (`songs = (songtblhi - songtbllo) / 3`).
- **pattern-pointer table** — the same idiom ending `CMP #$40` (new-note fetch).
- **wave/pulse/filter tables** — the shared "nextstep" idiom `LDA lefttbl,Y;
  CMP #$FF; INY; TYA; BCC; ...; LDA righttbl-1,Y`. Because the tables abut, the
  captured pairs form a self-validating chain (wave, then pulse and/or filter);
  the instrument region size then fixes `K = 3 + pulse + filt + 2·vib + 2·gate`,
  with gate read from the `insgatetimer` load idiom.

The stock "right after the frequency table" layout and exact-fit tiling are
tried first (unchanged for tunes that already resolve); the code-scan song-table
and instrument/table location are the recovery path for revisions they miss.
A code-located instrument/table layout is accepted only after the same
executable-consistency checks the tiling uses (jump-terminated tables, in-range
instrument/pattern pointers).

**Out of scope.** GoatTracker **V1.x** (a distinct earlier format) and a
residual minority of **V2.x** tunes whose packer stores the song(order)-table in
a different column geometry (its high column does not hold orderlist-pointer high
bytes) are not decoded and raise a clean `SidParseError`.

Direct-load images decompile with the standard library only. Crunched or
relocated images have their init routine run in a 6502 emulator first (jennings,
via pysidtracker). Container header fields are not trusted; this
detection is provided by the shared
[`pysidtracker`](https://github.com/anarkiwi/pysidtracker) base.

## Data model

- `Song` — name/author/copyright, `instruments`, `patterns`, `subtunes`, and the
  `wavetable`/`pulsetable`/`filtertable`/`speedtable` `Table`s.
- `Instrument` — ADSR (`attack_decay`, `sustain_release`), `wave_ptr`,
  `gateoff_timer`, `first_wave`, `name`.
- `Pattern` — typed `Row`s (note/instrument/command), e.g. `"C-4 01000"`.
- `Subtune` — per-channel orderlists of typed entries (`PlayPattern`, `Repeat`,
  `Transpose`).

The writer validates format limits and references (patterns, instruments, table
pointers) before emitting anything; callers describe content, not bytes.

### Typed model

`TypedSong` (`pygoattracker.typed`) states every byte as the operation
GoatTracker 2.76's readme and `player.s` give it, all frozen dataclasses;
`TypedSong.from_song(song).to_song() == song`, exhaustively over every column's
byte space.

| column | variants |
| --- | --- |
| note | `Note(pitch)`, `Rest`, `KeyOff`, `KeyOn` |
| command (pattern 0XY-FXY, wavetable `$F0-$FE`) | `DoNothing(data)`, `PortamentoUp(speed)`, `PortamentoDown(speed)`, `TonePortamento(speed)`, `Vibrato(speed)`, `SetAD(value)`, `SetSR(value)`, `SetWave(wave)`, `SetWavePtr(row)`, `SetPulsePtr(row)`, `SetFilterPtr(row)`, `SetFilterCtrl(resonance, channels)`, `SetCutoff(cutoff)`, `SetMasterVolume(volume)`, `TimingMark(value)`, `FunkTempo(speed)`, `SetTempo(tempo, channel)` |
| pattern row | `Row(note, instrument, command, length)`: `instrument`/`command` None keep the voice's, `length` the rows a rest spans |
| wavetable | `WaveStep(wave: SetWave \| Delay \| None, note: WaveNote(value, relative) \| None)`, `WaveCommand(command)`, `Jump(row)`, `Stop` |
| pulsetable | `PulseSet(width)`, `PulseMod(time, speed)`; SIMPLEPULSE builds `SimplePulseSet(value)`, `SimplePulseMod(time, step)`; `Jump`, `Stop` |
| filtertable | `FilterSet(passband, resonance, channels)`, `FilterMod(time, speed)`, `CutoffSet(cutoff)`, `Jump`, `Stop` |
| speedtable | `FixedSpeed(value)` (`rate`, `depth`), `CalculatedSpeed(rate, shift)`; `TypedSong.speed(ref)` resolves a command's or an instrument's row, row 0 to `speed0` |
| instrument | `Instrument(…, gate_timer, hard_restart, legato, first_wave: SetWave \| GateOff \| GateOn \| None)` |

`decompile_sid` reads the packed bytes into a `TypedSong` directly
(`DecompiledSid.typed`), folding greloc's transforms into their meaning: the
packed tempo (`t - 1` from 3), `vibdelay - 1`, the wave and filter column
transforms, NOWAVEDELAY, SIMPLEPULSE, the speedtable's row 0, and the
instrument classes (`FIRSTNOHRINSTR`, `FIRSTLEGATOINSTR`) or FIXEDPARAMS'
immediates read from player code (`PackedInfo.build`, a `PlayerBuild`).
`DecompiledSid.repack()` re-encodes the typed song with `packed.pack` and lists
every byte that differs from the image: none, for every HVSC tune `decompile_sid`
reads. `PackedInfo.freq_table` is the tuning greloc packed
(`firstnote..lastnote`); `PackedInfo.note_read` is what the packed player reads
for note indexes 0-127, which a `Player` takes as its `freq_table`.
`PackedInfo.state` is the ghost image and the globals the first play call
starts from, in a full-ghosting build (`GHOSTREGS`), whose `mt_play` copies the
previous call's ghost to the SID before it runs: a `Player` given a `state`
outputs each call one call late. `PackedInfo.init_regs` are the SID writes of
the tune's own init routine, which the chip holds until the player overwrites
them. In an image holding several players, `PlayerBuild` is read from the code
between the decoded data's jump table and its frequency table (`player_span`).
`PlayerBuild.deftempo` (greloc `DEFAULTTEMPO`) is the `Player`'s
`default_tempo`.

### NinjaTracker 2

[NinjaTracker 2](http://covertbitops.c64.org) songs (the C64 editor's `N2` work
files) read and write through their own typed model (`NinjaSong`,
`NinjaCommand`, `NinjaPattern`, `NinjaRow`). NT2 commands double as instruments.
Tracks reuse the typed `PlayPattern`/`Transpose` entries. NinjaTracker
transposes are -64..+63 halftones; byte `$FF` is zero — the format doc's
"$C0 = zero" does not match the player or the example tunes. The writer emits
canonical output: stale editor bytes after pattern terminators are not
preserved, so real files round-trip semantically (byte-identically when they
carry no stale bytes). There is no NinjaTracker playroutine port; parsing and
writing only.

`gt_to_nt2` converts GoatTracker songs to NinjaTracker 2. NinjaTracker has no
tempo, so conversion replays the playroutine's sequencer/tempo logic (funktempo
and FXY commands included) to bake every row's frame count into NinjaTracker
durations; rests merge into the previous row and long holds split into
continuation rows. The song is simulated for two full loops and must play both
identically. Instruments become commands (vibrato folds into the wavetable),
toneportamento becomes NinjaTracker's slide-to-target, 4XY vibrato and 8/9/AXY
pointers become synthesized legato commands, and 5/6XY become ADSR commands.
`errors="strict"` (default) raises `ConversionError` on anything inexpressible
(free 1/2XY portamento, 7XY/BXY/CXY/DXY, wavetable command execution, notes
below C-1, non-uniform hardrestart setups); `errors="drop"` drops and reports
them instead. Some mappings are inherently approximate: pulse widths quantize to
NinjaTracker's mirrored 8-bit register, filter resonance couples to the
passband, and vibrato parameters map by analogy.

## Player and playback notes

The player is one class (`Player`) deriving from `pysidtracker.MemPlayer`; it
ports the GoatTracker 2 playroutine tick for tick: sequencer
(transpose/repeat/restart), funktempo, wave/pulse/filter table execution
including wavetable command execution, speedtable vibrato/portamento (including
note-independent speeds), gateoff timer, and hard restart. Its render is
validated byte-exactly against a `sidplayfp` oracle
([oracle-testing.md](oracle-testing.md)).

`play_frame()` returns one PAL frame's changed register writes in ascending
register order (the first frame returns all 25 registers, since the base
`MemPlayer` diff has no prior snapshot); `render_grid(nframes)` returns the
forward-filled 25-register per-frame grid. Not implemented: multispeed playback,
stereo/2SID, and the editor's jamming / mid-song start modes.

Register logs are one `clock reg val` triple per line (absolute clock in PAL CPU
cycles, decimal, `#` comments). `write_reglog` / `read_reglog` write and read
them; `iter_register_writes` yields the writes. Rendering drives
[pyresidfp](https://pypi.org/project/pyresidfp/) (reSIDfp emulation), clocking
each register write individually at the same in-frame offsets the register log
uses; `render_samples` returns raw 16-bit samples and accepts a `device=` for
any other emulator object with `write_register` / `clock` /
`sampling_frequency`.

## References

- GoatTracker 2.76 format documentation and `gplay.c`
  ([GoatTracker 2](https://sourceforge.net/projects/goattracker2/)).
- [NinjaTracker 2](http://covertbitops.c64.org).
- [pyresidfp](https://pypi.org/project/pyresidfp/) — reSIDfp SID emulation.
- [`pysidtracker`](https://github.com/anarkiwi/pysidtracker) — shared
  container/image/detection base.
