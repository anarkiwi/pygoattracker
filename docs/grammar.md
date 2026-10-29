# GoatTracker song grammars

Sections 1–3 give the grammar of a GoatTracker 2 song;
[GoatTracker V1.x](#goattracker-v1x) gives the V1.x one. Each is in three layers:

1. **concrete syntax**: the bytes. There are two encodings of one tree: the
   editor's `.sng` (GTS5, `reader.py`/`writer.py`) and greloc's packed player
   data (`packed.py`).
2. **abstract syntax**: the tree both encodings decode to (`typed.TypedSong`).
3. **semantics**: the playroutine state machine that turns the tree into SID
   register writes, one tick at a time (`player.Player`).

The older GT2 generations (GTS2–GTS4) are dialects that load into the same
tree. See [Dialects](#dialects). GoatTracker 1.x (`GTS!` and its packed
tunes) has a tree of its own.

## 1. Abstract syntax

The abstract syntax is the Lark grammar
[`src/pygoattracker/song.lark`](../src/pygoattracker/song.lark). Each
constructor rule builds the `typed` dataclass it names. `pygoattracker.text`
parses it (`loads`) and prints it (`dumps`), and for every `TypedSong`,
`loads(dumps(song)) == song`. The tests cover every byte of every column, and
the `.sng` and packed `.sid` fixtures.

```bash
pygoattracker text tune.sng          # or a packed tune.sid; prints the song text
pygoattracker fromtext tune.txt out.sng
```

```
name "hand" author "me" copyright "2026"
subtune 0
  orderlist Transpose(-2) Repeat(1) PlayPattern(0) restart 0
  orderlist PlayPattern(0) restart 0
  orderlist PlayPattern(0) restart 0
instrument 1 "lead" ad $09 sr 0 wave 1 pulse 0 filter 0 vibrato 0
  delay 0 gate 2 nohardrestart legato first GateOn
wavetable
  1: WaveStep(SetWave($41), none)
  2: Jump(1)
pulsetable
filtertable
speedtable
  1: FixedSpeed($0230)
pattern 0
  C#4 1 SetTempo(6, channel)       # note, instrument, command
  --- Vibrato(1) *4                # *4: a rest of four rows
  ...                              # no command: keep the held one (packed form)
```

`loads` then checks every value against its field (`TypedSong.check`): a value
fits only if its bytes read back as it, so widths are checked in context.
`SetMasterVolume($20)` reads back as `TimingMark`, wavetable `SetWave($E5)` as
`SetWave(5)`, and an out-of-byte value has no bytes at all. A value is accepted
if it fits the `.sng` or the packed form. Constraints the grammar does not
state, because they are counts, are checked when the song is written as bytes. Those are the list sizes (subtunes ≤ 32,
patterns ≤ 208, instruments ≤ 63, table rows ≤ 255, pattern rows ≤ 128,
orderlist entries ≤ 254) and the value ranges in section 2. The order the
player needs in an orderlist (`Transpose? Repeat? PlayPattern`) is not
enforced; it stops on a violation. `Repeat(n)` plays the next pattern n+1
times, shown by the editor as R(n+1)&$F.

The tree has four kinds of node:

| kind | nodes | how time runs through it |
| --- | --- | --- |
| **sequence** | Subtune → Orderlist → Pattern → Row | the sequencer steps one row per *row time* (tempo ticks) |
| **patch** | Instrument | read once, at note start: sets the envelope, starts the tables |
| **program** | wave/pulse/filter tables | run one instruction per tick, with `Jump`/`Stop` control flow |
| **constant pool** | speedtable | data only; commands and instruments refer to it by index |

Every cross-reference is an integer index. The writer validates them:

| from | to |
| --- | --- |
| `PlayPattern.num` | `patterns` |
| `Row.instrument` | `instruments` (0 or absent means unchanged) |
| `Instrument.{wave,pulse,filter}_ptr`, `Set{Wave,Pulse,Filter}Ptr`, `Jump` | rows of that table |
| `Instrument.vibrato_speed`, every `speed` argument | speedtable rows |
| `Orderlist.restart` | `entries` index |

## 2. Concrete syntax

### 2.1 `.sng` (GTS5)

```
sng          = "GTS5" cstr32 cstr32 cstr32                 -- name, author, copyright
               u8:nsub (orderlist{C})^nsub                 -- C = 3, or 6 if all 6*nsub parse
               u8:ninstr instrument^ninstr
               table^4                                     -- wave, pulse, filter, speed
               u8:npatt pattern^npatt  EOF
orderlist    = u8:L  entry^(L-1)  $FF  u8:restart          -- L counts the $FF
entry        = $00-$CF  PlayPattern(b)
             | $D0-$DF  Repeat(b - $D0)                    -- n+1 plays
             | $E0-$FE  Transpose(b - $F0)
instrument   = AD SR wave_ptr pulse_ptr filter_ptr vibparam vibdelay gatetimer firstwave cstr16
table        = u8:N  left[N]  right[N]                     -- column-major
pattern      = u8:N  row^(N-1)  $FF ?? ?? ??               -- N counts the end row; N <= 129
row          = note instr cmd data                         -- 4 bytes, cmd in $0-$F
```

Byte lexicon for each field (`typed.py` `*_row`/`*_bytes`; the round trip is
lossless):

| field | byte ranges |
| --- | --- |
| note | `$60-$BC` Note(b−$60) · `$BD` Rest · `$BE` KeyOff · `$BF` KeyOn |
| gatetimer | bits 0–5 timer · `$40` legato · `$80` **no** hard restart |
| firstwave | `$00` None · `$FE` GateOff · `$FF` GateOn · else SetWave |
| wave left | `$00` none · `$01-$0F` Delay · `$10-$DF` SetWave(b) · `$E0-$EF` SetWave(b&$0F) · `$F0-$FE` WaveCommand(cmd b&$0F, data=right) · `$FF` Jump/Stop |
| wave right | `$80` none · `$00-$5F` +n relative · `$60-$7F` −32..−1 relative · `$81-$DF` absolute C#0–B-7 (b&$7F; `$E0-$FF` read the zero-padded frequency table) |
| pulse | left `$80-$FE` PulseSet((l&$7F)<<8 \| r; the chip reads the low 12 bits) · `$01-$7F` PulseMod(l, s8 r), `$00` 256 ticks · `$FF` Jump(r)/Stop(r=0) |
| filter | left `$80-$FE` FilterSet(l&$7F, r>>4, r&$0F) · `$01-$7F` FilterMod(l, s8 r) · `$00` CutoffSet(r) · `$FF` Jump/Stop |
| speed | left bit 7 set: CalculatedSpeed(l&$7F, r) · else FixedSpeed(l<<8 \| r). A row means what its reader makes of it: funktempo reads (l, r) as two tempos whatever bit 7 holds; portamento uses only r of a calculated row |
| BXY / DXY / FXY | `B`: res=X, chans=Y · `D`: XY<$10 volume, else timing mark · `F`: bit 7 = this channel only; tempo 0/1 recalls the funk pair (that phase first), 2 plays as 3 |
| wave command | `$F0-$FE` run commands 0-E; FXY cannot be reached, so the grammar refuses `WaveCommand(SetTempo(…))` |

### 2.2 Packed player data (greloc)

This encoding writes the same tree more compactly. Columns are split across
memory, and the packed player reads them through pointer tables. Its streams are
the byte grammar [`src/pygoattracker/streams.lark`](../src/pygoattracker/streams.lark),
parsed by `pysidtracker.ByteGrammar` (one latin-1 character per byte) into
`typed` rows and orderlist entries (`packed.pattern_rows`, `packed_orderlist`).
A pattern reads as `player.s`'s `mt_getnewnote` walks it: `$00` ends it only
after a row, and after an instrument byte any byte below `$60` is a command and
any from `$60` a note. `pygoattracker grammar tune.sid` prints every stream's
parse tree.

Two extra transforms apply: a packed tempo `t >= 3` is stored as `t − 1`, and
the instrument and table columns depend on the build (NOWAVEDELAY,
SIMPLEPULSE, FIXEDPARAMS, instrument classes). `Row.command = None` and
`Row.length > 1` exist only so that this encoding round-trips. `raw_rows`
expands them back to the `.sng` form.

## 3. Semantics

### Time

```
frame (one play call, 50 Hz PAL)  ⊇  tick (calls ticks per frame, normally 1)
tick 0 of a row  : sequencer, note init, one-shot commands
tick n > 0       : continuous commands and table programs
row time         = tempo ticks (per channel), or alternating funk pair when tempo ∈ {0,1}
```

Tempo is per channel. `SetTempo(t, channel=False)` sets every channel and
`channel=True` sets only its own. `FunkTempo(s)` loads the pair from speedtable
row `s` (`FunkTempo(0)` keeps the current pair) and sets tempo 0 on every
channel. An instrument's `legato` ($40) also skips the hard restart, whatever
`hard_restart` says. The default tempo is 6; an instrument 63 with AD ≥ 2
and no wavetable sets it to AD (the editor's convention).

### State scopes

| scope | state |
| --- | --- |
| channel | orderlist ptr, transpose, repeat, pattern ptr, instrument, tempo/tick, note, freq, gate, wave, AD/SR, wave ptr + delay counter, pulse ptr + time, held command + data, vibrato phase/delay, gate timer |
| chip (3 channels) | filter ptr + time, cutoff, resonance/routing, passband, master volume |
| song | funk tempo pair, play state (running / stop) |

### Per-tick order (per channel, `Player._play_channel`)

1. Filter programs run once per chip, before the channels.
2. On tick 0 the sequencer runs if the pattern has ended: it reads
   `Transpose? Repeat? PlayPattern` from the orderlist. At `$FF` it jumps to
   `restart`.
3. On tick 0 with a pending note, note init runs from the instrument: first
   wave/gate, wave ptr, pulse ptr, filter ptr, AD, SR, and the vibrato delay
   and speed. `TonePortamento` skips all of these and only retargets the pitch.
4. On tick 0 the row's command runs. Commands 1–4 latch as the continuous
   effect. `DoNothing` latches the instrument vibrato. The others are one-shot.
   A tick that starts a note (other than `TonePortamento`) ends here: its
   registers are written and the table programs start on the next tick.
5. The wavetable runs one instruction. A note or a command row suppresses the
   continuous effect for that tick.
6. On ticks n > 0, the continuous effect runs (portamento, vibrato, or
   instrument vibrato after its delay).
7. The pulse program runs one instruction.
8. **Lookahead**: when `tick == gate_timer`, the *next* row is fetched early.
   For a new note on a non-legato instrument, the gate is cleared and, unless
   `hard_restart` is off, the hard-restart ADSR is written. The next note then
   starts with a released envelope. Pattern rows are therefore read
   `gate_timer` ticks before the row they sound on.
9. Freq, pulse and control (`wave & gate`) go to the chip.

### Table program semantics

| row | effect |
| --- | --- |
| `WaveStep` | set the waveform; set freq from the note (relative to the channel's note + transpose, or absolute); `Delay(n)` holds the row n more ticks |
| `PulseMod(t, s)` / `FilterMod(t, s)` | add `s` each tick for `t` ticks, then advance |
| `PulseSet` / `CutoffSet` | set, advance |
| `FilterSet` | set passband/resonance/routing; a following `CutoffSet` executes in the same tick |
| `Jump(r)` / `Stop` | continue at r / halt this program; a pointer that *starts* on a jump stops the song |
| `WaveCommand` | runs a pattern command for one tick (commands 0, 8 and E stop the song) |

## Dialects

| magic | relation to the tree |
| --- | --- |
| `GTS5` | the native encoding |
| `GTS4`, `GTS3` | same layout; values are converted after load (`legacy.apply_legacy_conversions`) |
| `GTS2` | three tables (no speedtable): inline instrument wavetables, synthesized pulse programs, 0XY arpeggios extracted, effect parameters turned into speedtable rows |
| `GTS!` (V0.9x, V1.25, V1.4b–1.53) | a different tree ([GoatTracker V1.x](#goattracker-v1x)); `convert=True` gives the lossy GT2 import |
| packed `.sid` | the encoding in section 2.2; build defines are measured from the player ([format.md](format.md)) |

## GoatTracker V1.x

The abstract syntax is
[`src/pygoattracker/gt1.lark`](../src/pygoattracker/gt1.lark); the byte streams
(packed and editor patterns, each family's orderlists) are the byte grammar
[`src/pygoattracker/gt1streams.lark`](../src/pygoattracker/gt1streams.lark). The tree is
`gt1typed.Gt1TypedSong`. `text.loads` reads either grammar, picking `gt1.lark`
when the text opens with `player`. For every `Gt1Song`,
`Gt1TypedSong.from_song(s).to_song(s) == s`, and the text round-trips. The
tests cover every byte of every column in each form and family, the editor
songs of every layout, and the packed songs of every playroutine.

```
player "1.53" packed params $04 $02 $0F $00   # or: editor "1.4b-V1.53" name … author … copyright …
subtune 0
  orderlist Transpose(-2) Repeat(1) PlayPattern(0) Loop(0)
  orderlist PlayPattern(0) Loop(0)
  orderlist PlayPattern(0) Loop(0)
instrument 1 "" ad $09 sr 0 pulse $08 speed $20 low $01 high $0E filter 0 wave 1
wavetable
  1: WaveStep($41, WaveNote(0, relative))
  2: Stop
filtertable
  0: FilterSet($F1, $1F, $40, 4)
  1: FilterSlide(16, 1, 0)
pattern 0
  C-4 1 Vibrato($24)
  D-4                  # packed: no command, keep the held one
  ... *4
  --- SetTempo(5)
```

Two parameters decide what a byte is:

| | decides | values |
| --- | --- | --- |
| **family** (from `player`) | command numbers; orderlist bytes `$D0-$FE`; wave bytes `$01-$07` | standard (V1.3–V1.5x; delays `Delay(n)` from V1.5): `PortamentoUp`/`PortamentoDown` at 1/2, `SetFilter` (a filtertable step) at 5, `Repeat`/`Transpose` in orderlists · musicroutine (11.x, V0.9x): `FreqSlide`/`CutoffSlide` at 1/2, `SetFilterCtrl` at 5, every orderlist byte a pattern |
| **form** (`editor` layout or `packed`) | row encoding; orderlist end; units of data | editor: three-byte rows, an `$FF` end row, data as the editor shows it · packed: also note-only rows that keep the held command and rest runs (`*n`), data as the player reads it. The F9 relocator converts editor data to packed (tempo −1, vibrato nibbles swapped, filter step ×4, arpeggio bit 7), so it is not folded into the tree |

Compared with GT2, V1.x has:

- **no command parameters table.** A command's data byte is the parameter
  itself; there is no speedtable.
- **instrument-relative wavetable jumps.** `Jump(n)` in the wavetable goes to
  row `n` of the instrument's own table.
- **a pulse program in the instrument.** The pulse is set by `pulse`/`speed`/
  `low`/`high`, not by a pulse table.
- **one filter program.** It is a chain of four-byte steps (standard family
  only; V1.4x slides subtract the speed, V1.5x add it, and V1.5x step 0 holds
  the funktempo pair); the musicroutine family has a filter byte per instrument.
- **eight commands.** Command 7 is tempo below `$80` on every channel, else on
  this one. `$C0-$FF` is a timing mark in 11.3–11.5 and V1.4x. In V1.5x, `$00`/`$01`
  is funktempo, `$EF` a timing mark and `$F0-$FF` the volume.
- **the data as stored.** Arpeggio bit 7 (half speed), tone portamento (standard
  step ×4, 0 ties; musicroutine bit 7 the direction) and vibrato (half-period
  and step nibbles, swapped by the relocator) are described on their constructors.
- **release quirks folded in.** V1.50's orderlists store transposes one higher
  (`$EF` is zero); `Transpose(n)` is the semitones it plays. `End($FE)` repeats
  the last pattern; before V1.15, `End($FF)` loops to the start.

`Gt1TypedSong.check` holds every value to the bytes of its own form and
playroutine: a musicroutine command in a standard song, a `Delay` outside
V1.5x, a long rest in an editor song, or a packed orderlist entry that is its
end mark is refused.

The player image (load address, author block, operands the tune modifies) is
not song data. `to_song(image)` takes it from a decompiled `Gt1Song`, and
`to_song()` uses the playroutine as assembled at `$1000`. `Pattern.end` keeps
the bytes after the rows, so a last pattern cut short by the end of the file
round-trips too.
