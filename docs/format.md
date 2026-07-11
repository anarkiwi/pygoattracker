# GoatTracker format

## Overview

GoatTracker is a family of tracker applications for composing C64/SID music by
Lasse Öörni (Cadaver). pygoattracker implements the GTS5 song format and the
playroutine from first principles, following the GoatTracker 2.76 format
documentation and `gplay.c`.

`read_sng` reads every GoatTracker song generation: GTS5/GTS4/GTS3, GTS2 (early
3-table GoatTracker 2.xx) and GTS! (GoatTracker 1.x). Old formats are converted
on load exactly as GoatTracker 2.76 imports them — including GT1's inline
instrument wavetables, synthesized pulse programs, filtertable conversion and
0XY-arpeggio extraction — so they play and write back as GTS5. The writer always
emits GTS5; GTS5 read → write round trips are byte-identical.

## Container and detection notes

`.sid` files (PSID/RSID) wrap a 6502 program: the GoatTracker *packed*
playroutine (as produced by `gt2reloc`) followed by the song data.
`decompile_sid` locates that data in the loaded image and inverts it back into a
`Song`. `result.info` carries the packed playroutine options (`freq_table`,
`simplepulse`) for building a matching `Player`.

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
relocated images have their init routine run in a 6502 emulator first (py65,
a required dependency). Container header fields are not trusted; this
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
