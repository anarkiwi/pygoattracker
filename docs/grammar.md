# GoatTracker 2 tune grammar

The grammar of a GoatTracker 2 song in three layers:

1. **concrete syntax**: the bytes. There are two encodings of one tree: the
   editor's `.sng` (GTS5, `reader.py`/`writer.py`) and greloc's packed player
   data (`packed.py`).
2. **abstract syntax**: the tree both encodings decode to (`typed.TypedSong`).
3. **semantics**: the playroutine state machine that turns the tree into SID
   register writes, one tick at a time (`player.Player`).

Older generations (GTS2–GTS4, GoatTracker 1.x `GTS!`) are dialects that load
into the same tree. See [Dialects](#dialects).

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

Constraints the grammar does not state, because they are counts, are checked
when the song is written as bytes. Those are the list sizes (subtunes ≤ 32,
patterns ≤ 208, instruments ≤ 63, table rows ≤ 255, pattern rows ≤ 128) and
the value ranges in section 2.

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
             | $D0-$DF  Repeat(b - $D0)
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
| wave right | `$80` none · `$00-$5F` +n relative · `$60-$7F` −32..−1 relative · `$81-$FF` absolute (b&$7F) |
| pulse | left `$80-$FF` PulseSet((l&$7F)<<8 \| r) · `$00-$7F` PulseMod(l, s8 r) · `$FF` Jump(r)/Stop(r=0) |
| filter | left `$80-$FE` FilterSet(l&$70, r>>4, r&$0F) · `$01-$7F` FilterMod(l, s8 r) · `$00` CutoffSet(r) · `$FF` Jump/Stop |
| speed | left bit 7 set: CalculatedSpeed(l&$7F, r) · else FixedSpeed(l<<8 \| r) |
| BXY / DXY / FXY | `B`: res=X, chans=Y · `D`: XY<$10 volume, else timing mark · `F`: bit 7 = this channel only |

### 2.2 Packed player data (greloc)

This encoding writes the same tree more compactly. Columns are split across
memory, and the packed player reads them through pointer tables (`packed.py`).

```
pattern      = prow+ $00
prow         = instr? ( $C0-$FF                          -- Rest, length = $100 - b
                      | $40-$4F data? note                -- command b&$0F (data unless 0), then note
                      | $50-$5F data?                     -- command only; the note is Rest
                      | note )                            -- $60-$BF; command = held
instr        = $01-$3F
orderlist    = (pattern# Repeat? | Transpose)* $FF restart   -- Repeat follows its pattern
```

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
row `s` and sets tempo 0. The default tempo is 6; an instrument 63 with AD ≥ 2
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
| `GTS!` (V0.9x, V1.25, V1.4b–1.53) | a different tree, `Gt1Song`, played natively by `Gt1Player` ([gt1.md](gt1.md)); `convert=True` gives the lossy GT2 import |
| packed `.sid` | the encoding in section 2.2; build defines are measured from the player ([format.md](format.md)) |
