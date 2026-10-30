# Spec coverage proofs

`tests/test_spec_coverage.py` (marked `oracle`) proves each playspec equal to its
6502 playroutine on every reachable branch direction of the playroutine's code.

## Builds

`tests/_coverage.py` lists the builds:

- GoatTracker 2: each release's `player.s`/`altplayer.s` (V2.35–V2.76, stereo
  V2.59–V2.77) assembled at test time by `tests/_gt2asm.py` from the pinned release
  archives, over the data `pygoattracker.songimage.song_image` lays out
  (`tests/_gt2pack.py`), with the relocator defines of each variant: full features,
  NODIRECTWAVE, buffered writes, ghost and zero-page ghost registers, SIMPLEPULSE,
  pulse/realtime skips off, NOEFFECTS, no hard-restart or legato instrument,
  NOWAVECMD, the minimal optimized build and 3x multispeed. The assembler reproduces
  real tunes' playroutines byte for byte; a layout whose dispatched handlers span
  pages is refused, as the 6502 would jump into data.
- GoatTracker V1.x: each build of `pygoattracker._gt1_builds`, its code assembled or
  extracted at test time (`tests/_gt1_code.py`).

Nothing assembled from GoatTracker code is committed.

## Proof songs

`tests/_proof_songs.py` holds the synthetic songs, designed from the 6502 code: each
command, table row kind, orderlist command and instrument class where the player
branches on it, plus the edges a whole song reaches (a wave delay counter wrapping
past 255, toneportamentos arriving within and across a frequency page, a pulse
sweep turning at its limit, a hard-restart timer). Every song is compared over its
whole length (`pysidtracker.testing.whole_song`): RAM outside the stack page and
the SID registers equal after every call.

`tests._coverage.UNREACHABLE` names each branch direction no song can run, with the
reason (a flag a constant decides, code a define leaves without an entry) and the
builds it is in; the test asserts the gaps equal that list exactly.

## Tool

`scripts/spec_coverage.py` measures jennings coverage (`CoverageMPU`) of the
6502 while it compares, and lists each gap by source label and disassembly:

```bash
PYTHONPATH=src:. python scripts/spec_coverage.py --jobs 8 [--build gt2_276] [--context 2]
PYTHONPATH=src:. python scripts/spec_coverage.py --baseline --jobs 8   # corpus songs only
PYTHONPATH=src:. python scripts/spec_coverage.py --tunes --jobs 8      # the oracle tunes' own builds
```
