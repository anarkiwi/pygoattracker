"""Convert GoatTracker songs into NinjaTracker 2 songs.

NinjaTracker is a deliberately smaller engine, so this is a compile
step, not a transcode. The big translation is timing: GoatTracker rows
last as many frames as the running tempo dictates, while NinjaTracker
rows carry explicit durations. :class:`_TempoWalk` replays the
sequencer/tempo logic of the playroutine (including funktempo and
cross-channel FXY commands) to learn how many frames every played row
lasts; rests then merge into the previous row's duration and long
holds split into continuation rows. The song is simulated for two full
loops, and conversion fails if the second loop plays with different
timing than the first (NinjaTracker durations are baked into pattern
data).

GoatTracker instruments become NinjaTracker commands, with instrument
vibrato appended to the converted wavetable program. Realtime pattern
effects become synthesized commands: toneportamento maps to
NinjaTracker's slide-to-target wavetable step (used in legato mode),
4XY vibrato to vibrato steps, 8/9/AXY to legato commands that only
move a table pointer, and 5/6XY to ADSR commands using tracked
attack/decay state. Wavetable arpeggio bytes are nearly format
identical (relative bytes wrap the same way; absolute notes share the
``$80 + note`` encoding from C-1 up).

Inexpressible features raise :class:`ConversionError` with
``errors="strict"`` (the default) or are dropped and reported with
``errors="drop"``: free-running 1/2XY portamento, 7XY/BXY/CXY/DXY
commands, wavetable command execution, notes below C-1, waveforms
above $8F, note-independent (realtime calculated) speeds, and
per-instrument gateoff timers/first frame waveforms beyond
NinjaTracker's global hardrestart scheme (the first instrument's
first wave becomes the global one). Approximations that do not drop
data are not reported: 12-bit pulse values quantize to NinjaTracker's
mirrored 8-bit register, vibrato parameters map by analogy, filter
resonance is coupled to the passband nybble, and "keep frequency"
wavetable steps replay the base note.
"""

from dataclasses import dataclass, field

from pygoattracker import constants
from pygoattracker.errors import ConversionError
from pygoattracker.model import (
    Orderlist,
    PlayPattern,
    Repeat,
    Song,
    Subtune,
    Transpose,
)
from pygoattracker.ninja import (
    NT2_KEYOFF,
    NT2_KEYON,
    NT2_MAX_DURATION,
    NT2_MIN_DURATION,
    NinjaCommand,
    NinjaPattern,
    NinjaRow,
    NinjaSong,
    validate_nt2,
)

_MAX_SIM_FRAMES = 600_000
_EMPTY_ROWS = 64


@dataclass
class _Instance:
    """One played pattern occurrence on one channel."""

    pattern: int
    durations: list[int] = field(default_factory=list)


class _TempoWalk:
    """Replays the playroutine's sequencer and tempo state machine.

    Only timing matters here: per-channel tick counters, tempo and
    funktempo handling, and the orderlist walk, all following gplay.c.
    Produces each channel's played pattern instances with per-row
    frame durations, over two full song loops.
    """

    def __init__(self, song: Song, subtune: int):
        self.song = song
        self.orders = []
        self.lengths = []
        for orderlist in song.subtunes[subtune].channels:
            entries = [entry.to_byte() for entry in orderlist.entries]
            self.orders.append(entries + [constants.LOOPSONG, orderlist.restart])
            self.lengths.append(len(entries))
        self.commands = [
            [(row.command, row.data) for row in pattern.rows]
            for pattern in song.patterns
        ]
        tempo = 5
        instr63 = song.instrument(constants.MAX_INSTR - 1)
        if instr63.attack_decay >= 2 and not instr63.wave_ptr:
            tempo = instr63.attack_decay - 1
        self.tempo = [tempo] * 3
        self.funktable = [8, 5]
        self.tick = [6] * 3
        self.songptr = [0] * 3
        self.pattnum = [0] * 3
        self.row = [0] * 3
        self.repeat = [0] * 3
        self.loops = [0] * 3
        self.instances: list[list[_Instance]] = [[], [], []]
        for channel in range(3):
            self._sequence(channel)

    def _pattern_rows(self, pattern: int) -> int:
        if pattern < len(self.commands):
            return len(self.commands[pattern])
        return _EMPTY_ROWS

    def _sequence(self, channel: int) -> None:
        order = self.orders[channel]
        if order[self.songptr[channel]] == constants.LOOPSONG:
            self.loops[channel] += 1
            self.songptr[channel] = order[self.songptr[channel] + 1]
            if self.songptr[channel] >= self.lengths[channel]:
                raise ConversionError("song stops instead of looping")
        if constants.TRANSDOWN <= order[self.songptr[channel]] < constants.LOOPSONG:
            self.songptr[channel] += 1
        if constants.REPEAT <= order[self.songptr[channel]] < constants.TRANSDOWN:
            self.repeat[channel] = order[self.songptr[channel]] - constants.REPEAT
            self.songptr[channel] += 1
        self.pattnum[channel] = order[self.songptr[channel]]
        if self.repeat[channel]:
            self.repeat[channel] -= 1
        else:
            self.songptr[channel] += 1
        self.row[channel] = 0
        self.instances[channel].append(_Instance(self.pattnum[channel]))

    def _row_commands(self, channel: int) -> None:
        pattern = self.pattnum[channel]
        if pattern >= len(self.commands):
            return
        command, data = self.commands[pattern][self.row[channel]]
        if command == constants.CMD_FUNKTEMPO:
            if data and data <= len(self.song.speedtable):
                self.funktable[0] = (self.song.speedtable.left[data - 1] - 1) & 0xFF
                self.funktable[1] = (self.song.speedtable.right[data - 1] - 1) & 0xFF
            self.tempo = [0, 0, 0]
        elif command == constants.CMD_SETTEMPO:
            newtempo = data & 0x7F
            if newtempo >= 3:
                newtempo -= 1
            if data >= 0x80:
                self.tempo[channel] = newtempo
            else:
                self.tempo = [newtempo] * 3

    def run(self) -> list[list[_Instance]]:
        """Simulate until every channel finished its second loop."""
        for _ in range(_MAX_SIM_FRAMES):
            if min(self.loops) >= 2:
                return self.instances
            for channel in range(3):
                self.tick[channel] -= 1
                if self.tick[channel] == 0:
                    if self.row[channel] >= self._pattern_rows(self.pattnum[channel]):
                        self._sequence(channel)
                    self._row_commands(channel)
                    self.instances[channel][-1].durations.append(0)
                    self.row[channel] += 1
                elif self.tick[channel] < 0:
                    if self.tempo[channel] >= 2:
                        self.tick[channel] = self.tempo[channel]
                    else:
                        self.tick[channel] = self.funktable[self.tempo[channel]]
                        self.tempo[channel] ^= 1
                    if self.tick[channel] < 2:
                        raise ConversionError(
                            "tempo below 3 frames per row cannot convert"
                        )
                if self.instances[channel][-1].durations:
                    self.instances[channel][-1].durations[-1] += 1
        raise ConversionError("song timing did not settle within the frame budget")


def _chunk_duration(total: int) -> list[int]:
    """Split a frame count into NinjaTracker durations (3-66 each)."""
    chunks = []
    while total > NT2_MAX_DURATION:
        take = NT2_MAX_DURATION
        if total - take < NT2_MIN_DURATION:
            take = total - NT2_MIN_DURATION
        chunks.append(take)
        total -= take
    if total < NT2_MIN_DURATION:
        raise ConversionError(f"row lasts {total} frames; NinjaTracker needs 3+")
    chunks.append(total)
    return chunks


def _inherit_durations(rows: list[NinjaRow]) -> list[NinjaRow]:
    """Blank repeated durations (NT rows inherit the previous one).

    The first row always keeps its duration so patterns stay
    independent of playback context.
    """
    previous = None
    for row in rows:
        if row.duration == previous:
            row.duration = 0
        elif row.duration:
            previous = row.duration
    return rows


@dataclass
class _ChannelState:
    """Conversion state carried along one channel's played sequence."""

    instrument: int = 1
    attack_decay: int = 0
    sustain_release: int = 0
    last_command: int = 0


class _Converter:
    """Builds the NinjaSong; memoizes commands and table programs."""

    def __init__(self, song: Song, errors: str):
        self.song = song
        self.strict = errors == "strict"
        self.dropped: list[str] = []
        self.result = NinjaSong(subtunes=[], patterns=[], commands=[], first_wave=0)
        self.command_keys: dict = {}
        self.wave_programs: dict = {}
        self.pulse_programs: dict = {}
        self.filter_programs: dict = {}
        self.pattern_keys: dict = {}

    def _unsupported(self, message: str) -> None:
        if self.strict:
            raise ConversionError(message)
        if message not in self.dropped:
            self.dropped.append(message)

    def _speed_value(self, index: int, what: str) -> int:
        if not index or index > len(self.song.speedtable):
            return 0
        left = self.song.speedtable.left[index - 1]
        right = self.song.speedtable.right[index - 1]
        if left >= 0x80:
            self._unsupported(f"{what}: note-independent speed")
            return 0
        return (left << 8) | right

    # Table program conversion. Each GoatTracker program (a walk from a
    # 1-based start position to its stop or loop) becomes one
    # contiguous NinjaTracker program; jumps linearize, loops map to
    # absolute positions in the converted table.

    def _walk_table(self, table, start: int, convert_step):
        rows = []
        position_map = {}
        loop_target = None
        pos = start
        while 0 < pos <= len(table):
            if pos in position_map:
                loop_target = position_map[pos]
                break
            position_map[pos] = len(rows)
            left = table.left[pos - 1]
            right = table.right[pos - 1]
            if left == constants.TABLEJUMP:
                if right == 0:
                    break
                pos = right
                continue
            pos = convert_step(rows, position_map, pos, left, right)
        return rows, loop_target

    def _store_program(self, target, rows, loop_target, key, programs) -> int:
        base = len(target) + 1
        if loop_target is not None:
            rows.append((0xFF, base + loop_target))
        else:
            rows.append((0xFF, 0))
        for left, right in rows:
            if len(target.left) >= 255:
                raise ConversionError("converted table overflows 255 rows")
            target.add(left, right)
        programs[key] = base
        return base

    def _wave_program(self, start: int, vibrato=(0, 0)) -> int:
        key = (start, vibrato)
        if key in self.wave_programs:
            return self.wave_programs[key]

        def step(rows, _position_map, pos, left, right):
            rows.append(self._wave_step(left, right))
            return pos + 1

        rows, loop_target = self._walk_table(self.song.wavetable, start, step)
        if vibrato != (0, 0):
            steps = self._vibrato_steps(*vibrato)
            if steps and loop_target is not None:
                self._unsupported("instrument vibrato on a looping wavetable")
            else:
                rows += steps
        return self._store_program(
            self.result.wavetable, rows, loop_target, key, self.wave_programs
        )

    def _wave_step(self, left: int, right: int) -> tuple[int, int]:
        if constants.WAVECMD <= left <= constants.WAVELASTCMD:
            self._unsupported("wavetable command execution")
            left, right = 0x00, 0x80
        arp = self._wave_arp(right)
        if left <= constants.WAVELASTDELAY:
            # No change / delay: no waveform, delayed arpeggio.
            return (0x90 + min(left, 0x2F), arp)
        if constants.WAVESILENT <= left <= constants.WAVELASTSILENT:
            return (left & 0x0F, arp)
        if left > 0x8F:
            self._unsupported(f"waveform ${left:02X} above $8F")
            left = 0x8F
        return (left, arp)

    def _wave_arp(self, right: int) -> int:
        if right == 0x80:
            return 0x00  # "keep frequency" replays the base note
        if right < 0x80:
            return right  # relative notes wrap identically
        if right < 0x8C:
            self._unsupported("absolute wavetable note below C-1")
            return 0x8C
        if right > 0xDF:
            self._unsupported("absolute wavetable note above B-7")
            return 0xDF
        return right  # absolute notes share the $80+note encoding

    def _vibrato_steps(self, delay: int, param: int) -> list[tuple[int, int]]:
        speed = self._speed_value(param, "vibrato")
        if not speed:
            return []
        steps = []
        if delay:
            steps.append((0x90 + min(delay, 0x2F), 0x00))
        steps.append((0xC0 + min(speed >> 8, 0x1F), speed & 0xFF))
        return steps

    def _pulse_program(self, start: int) -> int:
        if start in self.pulse_programs:
            return self.pulse_programs[start]

        def step(rows, _position_map, pos, left, right):
            if left >= 0x80:
                # 12-bit $XYY quantizes to the mirrored 8-bit register.
                value = ((left & 0x0F) << 8) | right
                byte = (value & 0xF0) | (value >> 8)
                rows.append((0x80, byte))
            else:
                rows.append((left, right))
            return pos + 1

        rows, loop_target = self._walk_table(self.song.pulsetable, start, step)
        return self._store_program(
            self.result.pulsetable, rows, loop_target, start, self.pulse_programs
        )

    def _filter_program(self, start: int) -> int:
        if start in self.filter_programs:
            return self.filter_programs[start]
        state = {"passband": 0x10, "mask": 0x07}

        def step(rows, position_map, pos, left, right):
            table = self.song.filtertable
            if left >= 0x80:
                state["passband"] = left & 0x70
                state["mask"] = right & 0x0F
                cutoff = 0
                if pos < len(table) and table.left[pos] == 0x00:
                    pos += 1
                    position_map[pos] = len(rows)
                    cutoff = table.right[pos - 1]
                rows.append((self._filter_set(state), cutoff))
            elif left == 0x00:
                rows.append((self._filter_set(state), right))
            else:
                rows.append((left, right))
            return pos + 1

        rows, loop_target = self._walk_table(self.song.filtertable, start, step)
        return self._store_program(
            self.result.filtertable, rows, loop_target, start, self.filter_programs
        )

    @staticmethod
    def _filter_set(state) -> int:
        # NT couples resonance to the passband nybble (+8).
        return (((state["passband"] >> 4) + 8) << 4) | state["mask"]

    # Command synthesis.

    def _command(self, key, builder) -> int:
        if key in self.command_keys:
            return self.command_keys[key]
        command = builder()
        if len(self.result.commands) >= 127:
            raise ConversionError("conversion needs more than 127 commands")
        self.result.commands.append(command)
        number = len(self.result.commands)
        self.command_keys[key] = number
        return number

    def _instrument_command(
        self, num: int, wave_override: int | None = None, vibrato_override=None
    ) -> int:
        instrument = self.song.instrument(num)
        vibrato = (instrument.vibrato_delay, instrument.vibrato_param)
        if vibrato_override is not None:
            vibrato = vibrato_override
        key = ("instrument", num, wave_override, vibrato)

        def build():
            if instrument.gateoff_timer & 0x3F > 2:
                self._unsupported(
                    f"instrument {num}: gateoff timer beyond NT's fixed 2 frames"
                )
            if instrument.first_wave >= 0xFE:
                self._unsupported(f"instrument {num}: gate-only first wave")
            elif not self.result.first_wave:
                self.result.first_wave = instrument.first_wave
            elif instrument.first_wave and (
                instrument.first_wave != self.result.first_wave
            ):
                self._unsupported(f"instrument {num}: differing first frame wave")
            wave_ptr = instrument.wave_ptr if wave_override is None else wave_override
            return NinjaCommand(
                attack_decay=instrument.attack_decay,
                sustain_release=instrument.sustain_release,
                wave_ptr=self._wave_program(wave_ptr, vibrato) if wave_ptr else 0,
                pulse_ptr=(
                    self._pulse_program(instrument.pulse_ptr)
                    if instrument.pulse_ptr
                    else 0
                ),
                filter_ptr=(
                    self._filter_program(instrument.filter_ptr)
                    if instrument.filter_ptr
                    else 0
                ),
                name=instrument.name[:9].rstrip(),
            )

        return self._command(key, build)

    def _pointer_command(self, kind: str, pointer: int) -> int:
        def build():
            command = NinjaCommand(name=f"{kind}>{pointer:02X}")
            if pointer:
                if kind == "wave":
                    command.wave_ptr = self._wave_program(pointer)
                elif kind == "pulse":
                    command.pulse_ptr = self._pulse_program(pointer)
                else:
                    command.filter_ptr = self._filter_program(pointer)
            return command

        return self._command((kind, pointer), build)

    def _adsr_command(self, attack_decay: int, sustain_release: int) -> int:
        return self._command(
            ("adsr", attack_decay, sustain_release),
            lambda: NinjaCommand(
                attack_decay=attack_decay,
                sustain_release=sustain_release,
                name=f"adsr{attack_decay:02X}{sustain_release:02X}",
            ),
        )

    def _slide_command(self, speed: int) -> int:
        speed = min(speed, 0x1EFF)

        def build():
            rows = [(0xE0 + (speed >> 8), speed & 0xFF)]
            position = self._store_program(
                self.result.wavetable,
                rows,
                None,
                ("slide", speed),
                self.wave_programs,
            )
            return NinjaCommand(wave_ptr=position, name=f"slid{speed:04X}")

        return self._command(("slide", speed), build)

    def _vibrato_command(self, param: int) -> int:
        def build():
            rows = self._vibrato_steps(0, param)
            position = self._store_program(
                self.result.wavetable,
                rows,
                None,
                ("vib", param),
                self.wave_programs,
            )
            return NinjaCommand(wave_ptr=position, name=f"vib {param:02X}")

        return self._command(("vibrato", param), build)

    # Row conversion.

    def _continuous(self, state: _ChannelState, command: int) -> int:
        """Suppress re-emitting a running effect (NT would restart it)."""
        return 0 if command == state.last_command else command

    def _row_command(self, row, state: _ChannelState, has_note: bool) -> int:
        """The NT command byte to emit for one GT row (0 = none)."""
        instrument = self.song.instrument(state.instrument)
        legato = 0x80 if instrument.gateoff_timer & 0x40 else 0
        if row.command == constants.CMD_TONEPORTA and has_note:
            speed = self._speed_value(row.data, "toneportamento") or 0x1EFF
            return 0x80 | self._slide_command(speed)
        if row.command == constants.CMD_VIBRATO and row.data:
            if has_note:
                return legato | self._instrument_command(
                    state.instrument, vibrato_override=(0, row.data)
                )
            return self._continuous(state, 0x80 | self._vibrato_command(row.data))
        if row.command in (constants.CMD_SETAD, constants.CMD_SETSR):
            if row.command == constants.CMD_SETAD:
                state.attack_decay = row.data
            else:
                state.sustain_release = row.data
            return self._continuous(
                state,
                self._adsr_command(state.attack_decay, state.sustain_release),
            )
        if row.command == constants.CMD_SETWAVEPTR:
            if has_note:
                return legato | self._instrument_command(
                    state.instrument, wave_override=row.data
                )
            return 0x80 | self._pointer_command("wave", row.data)
        if row.command == constants.CMD_SETPULSEPTR:
            return 0x80 | self._pointer_command("pulse", row.data)
        if row.command == constants.CMD_SETFILTERPTR:
            return 0x80 | self._pointer_command("filter", row.data)
        if row.command in (constants.CMD_PORTAUP, constants.CMD_PORTADOWN):
            self._unsupported("free-running 1XY/2XY portamento")
        elif row.command == constants.CMD_SETWAVE:
            self._unsupported("7XY waveform command")
        elif row.command == constants.CMD_SETFILTERCTRL:
            self._unsupported("BXY filter control command")
        elif row.command == constants.CMD_SETFILTERCUTOFF:
            self._unsupported("CXY filter cutoff command")
        elif row.command == constants.CMD_SETMASTERVOL and row.data < 0x10:
            self._unsupported("DXY master volume command")
        if has_note:
            command = legato | self._instrument_command(state.instrument)
            if command != state.last_command:
                return command
        return 0

    def _convert_instance(
        self, instance: _Instance, state: _ChannelState
    ) -> list[NinjaRow]:
        if instance.pattern < len(self.song.patterns):
            gt_rows = self.song.patterns[instance.pattern].rows
        else:
            gt_rows = []
        rows: list[NinjaRow] = []
        for index, duration in enumerate(instance.durations):
            row = gt_rows[index] if index < len(gt_rows) else None
            note = None
            command = 0
            if row is not None:
                if row.instrument:
                    state.instrument = row.instrument
                    instrument = self.song.instrument(row.instrument)
                    state.attack_decay = instrument.attack_decay
                    state.sustain_release = instrument.sustain_release
                has_note = constants.FIRSTNOTE <= row.note <= constants.LASTNOTE
                command = self._row_command(row, state, has_note)
                if has_note:
                    note = row.note - constants.FIRSTNOTE
                    if note < 12:
                        self._unsupported("note below C-1")
                        note = 12
                elif row.note == constants.KEYOFF:
                    note = NT2_KEYOFF
                elif row.note == constants.KEYON:
                    note = NT2_KEYON
                if command:
                    state.last_command = command
            if note is None and not command and rows:
                # Rest: extend the previous row, splitting overflow
                # into continuation rows.
                chunks = _chunk_duration(rows[-1].duration + duration)
                rows[-1].duration = chunks[0]
                rows += [NinjaRow(duration=chunk) for chunk in chunks[1:]]
                continue
            chunks = _chunk_duration(duration)
            rows.append(NinjaRow(note=note or 0, command=command, duration=chunks[0]))
            rows += [NinjaRow(duration=chunk) for chunk in chunks[1:]]
        return _inherit_durations(rows)

    def _pattern_number(self, rows: list[NinjaRow]) -> int:
        key = tuple((row.note, row.command, row.duration) for row in rows)
        if key in self.pattern_keys:
            return self.pattern_keys[key]
        if len(self.result.patterns) >= 127:
            raise ConversionError("conversion needs more than 127 patterns")
        self.result.patterns.append(NinjaPattern(rows=rows))
        number = len(self.result.patterns)
        self.pattern_keys[key] = number
        return number

    def convert_subtune(self, subtune: int) -> Subtune:
        instances = _TempoWalk(self.song, subtune).run()
        tracks = []
        for channel in range(3):
            gt_orderlist = self.song.subtunes[subtune].channels[channel]
            played = instances[channel]
            self._check_loop_stability(gt_orderlist, played, channel)
            state = _ChannelState()
            entries = []
            restart = 0
            play_index = 0
            pending_plays = 1
            for index, entry in enumerate(gt_orderlist.entries):
                if isinstance(entry, Transpose):
                    if index == gt_orderlist.restart:
                        restart = len(entries)
                    entries.append(Transpose(entry.semitones))
                elif isinstance(entry, Repeat):
                    if index == gt_orderlist.restart:
                        restart = len(entries)
                    pending_plays = entry.count + 1
                else:
                    if index == gt_orderlist.restart:
                        # The playroutine does not re-arm a consumed
                        # repeat on restart: loop to the last copy.
                        restart = len(entries) + pending_plays - 1
                    for _ in range(pending_plays):
                        rows = self._convert_instance(played[play_index], state)
                        entries.append(PlayPattern(self._pattern_number(rows)))
                        play_index += 1
                    pending_plays = 1
            tracks.append(Orderlist(entries=entries, restart=restart))
        return Subtune(channels=tracks)

    def _check_loop_stability(self, gt_orderlist, played, channel) -> None:
        """Durations must repeat identically from the second loop on."""
        plays = []
        pending = 1
        restart_play = 0
        for index, entry in enumerate(gt_orderlist.entries):
            if isinstance(entry, Repeat):
                if index == gt_orderlist.restart:
                    restart_play = len(plays)
                pending = entry.count + 1
            elif isinstance(entry, PlayPattern):
                if index == gt_orderlist.restart:
                    restart_play = len(plays) + pending - 1
                plays += [entry.num] * pending
                pending = 1
            elif index == gt_orderlist.restart:
                restart_play = len(plays)
        total = len(plays)
        if total > len(played):
            raise ConversionError(f"channel {channel}: simulation diverged")
        for offset in range(total - restart_play):
            first = played[restart_play + offset]
            if total + offset >= len(played):
                break
            second = played[total + offset]
            if (first.pattern, first.durations) != (
                second.pattern,
                second.durations[: len(first.durations)],
            ):
                self._unsupported(
                    f"channel {channel}: tempo differs between song loops"
                )
                break


def gt_to_nt2(song: Song, errors: str = "strict", report: list | None = None):
    """Convert a GoatTracker :class:`Song` to a :class:`NinjaSong`.

    ``errors="strict"`` raises :class:`ConversionError` on any feature
    NinjaTracker cannot express; ``errors="drop"`` drops such features
    and appends one message per dropped feature to ``report`` (when a
    list is given).
    """
    if errors not in ("strict", "drop"):
        raise ValueError("errors must be 'strict' or 'drop'")
    if len(song.subtunes) > 16:
        raise ConversionError("more than 16 subtunes")
    converter = _Converter(song, errors)
    converter.result.subtunes = [
        converter.convert_subtune(subtune) for subtune in range(len(song.subtunes))
    ]
    if not converter.result.patterns:
        converter.result.patterns = [NinjaPattern()]
    if not converter.result.first_wave:
        converter.result.first_wave = 0x09
    validate_nt2(converter.result)
    if report is not None:
        report.extend(converter.dropped)
    return converter.result
