"""Import GoatTracker 1.x and early GoatTracker 2 songs.

Ports the old-format importers from GoatTracker 2.76's ``gsong.c``:

- ``GTS!`` (GoatTracker 1.x): instruments carried inline wavetables and
  raw pulse/filter parameters, patterns packed the instrument number
  into the command byte, and arpeggios/vibrato/portamento used direct
  parameters. Everything is converted to GoatTracker 2 tables exactly
  like GoatTracker 2 does on load, including synthesized pulse
  programs, the converted filter table, and arpeggio wavetable
  programs (with new instruments where needed).
- ``GTS2`` (GoatTracker 2.xx before the speedtable): vibrato,
  portamento and funktempo parameters become speedtable entries.
- Shared post-load fixes for all pre-``GTS5`` songs: pulse modulation
  speeds double for pre-v2.4 songs, and the old first-wave $00/$80
  legato/no-hardrestart conventions become gateoff timer flags.

Loading an old song therefore gives a normal
:class:`~pygoattracker.model.Song` that plays and writes back as GTS5.
"""

from pygoattracker import constants
from pygoattracker.errors import SngParseError
from pygoattracker.model import Instrument, Pattern, Row, Song, Subtune, Table
from pygoattracker.reader import (
    _Cursor,
    _decode_str,
    _parse_orderlist,
    _parse_pattern,
    _parse_table,
)

GTS1_MAGIC = b"GTS!"
GTS2_MAGIC = b"GTS2"

_GTS1_INSTRUMENTS = 31
_OLDKEYOFF = 0x5E
_OLDREST = 0x5F

# makespeedtable() conversion modes, as in gtable.c.
MST_NOFINEVIB = 0
MST_FINEVIB = 1
MST_FUNKTEMPO = 2
MST_PORTAMENTO = 3


def make_speed_entry(speedtable: Table, data: int, mode: int) -> int:
    """Convert an old-style parameter into a speedtable pointer.

    Returns the 1-based table pointer (0 when ``data`` is zero or the
    table is full), reusing an identical existing row when possible --
    a port of ``makespeedtable()``.
    """
    if not data:
        return 0
    if mode == MST_NOFINEVIB:
        left, right = (data & 0xF0) >> 4, (data & 0x0F) << 4
    elif mode == MST_FINEVIB:
        left = (data & 0x70) >> 4
        right = ((data & 0x0F) << 4) | ((data & 0x80) >> 4)
    elif mode == MST_FUNKTEMPO:
        left, right = (data & 0xF0) >> 4, data & 0x0F
    else:  # MST_PORTAMENTO
        left, right = ((data << 2) >> 8) & 0xFF, (data << 2) & 0xFF
    for idx, row in enumerate(zip(speedtable.left, speedtable.right)):
        if row == (left, right):
            return idx + 1
    for idx, row in enumerate(zip(speedtable.left, speedtable.right)):
        if row == (0, 0):
            speedtable.left[idx] = left
            speedtable.right[idx] = right
            return idx + 1
    if len(speedtable) >= constants.MAX_TABLELEN:
        return 0
    return speedtable.add(left, right)


def double_pulse_speeds(song: Song) -> None:
    """Pre-v2.4 songs: pulse modulation speed gained one bit."""
    table = song.pulsetable
    for idx, (left, right) in enumerate(zip(table.left, table.right)):
        if left < 0x80 and right:
            speed = right - 0x100 if right >= 0x80 else right
            speed = min(max(speed * 2, -128), 127)
            table.right[idx] = speed & 0xFF


def convert_legacy_instruments(song: Song) -> None:
    """Pre-v2.59 songs: first wave $80 bit / $00 become gate flags."""
    for instrument in song.instruments:
        if instrument.first_wave >= 0x80:
            instrument.gateoff_timer |= 0x80
            instrument.first_wave &= 0x7F
        if not instrument.first_wave:
            instrument.gateoff_timer |= 0x40


def apply_legacy_conversions(song: Song, magic: bytes) -> None:
    """The shared post-load conversions for pre-GTS5 songs."""
    if magic[3:] < b"4":
        double_pulse_speeds(song)
    if magic[3:] < b"5":
        convert_legacy_instruments(song)


def _parse_header(cur: _Cursor) -> Song:
    song = Song(
        name=_decode_str(cur.take(constants.MAX_STR, "song name")),
        author=_decode_str(cur.take(constants.MAX_STR, "author name")),
        copyright=_decode_str(cur.take(constants.MAX_STR, "copyright")),
        wavetable=Table(),
        pulsetable=Table(),
        filtertable=Table(),
        speedtable=Table(),
    )
    num_subtunes = cur.u8("subtune count")
    if not 1 <= num_subtunes <= constants.MAX_SONGS:
        raise SngParseError(f"bad subtune count {num_subtunes}")
    song.subtunes = [
        Subtune(
            channels=[
                _parse_orderlist(cur, subtune, channel)
                for channel in range(constants.MAX_CHN)
            ]
        )
        for subtune in range(num_subtunes)
    ]
    return song


def parse_gts2(data: bytes, finevibrato: bool = True) -> Song:
    """Parse a GTS2 (early GoatTracker 2.xx, 3-table) song."""
    cur = _Cursor(data)
    cur.take(4, "identifier")
    song = _parse_header(cur)

    num_instruments = cur.u8("instrument count")
    if num_instruments >= constants.MAX_INSTR:
        raise SngParseError(f"bad instrument count {num_instruments}")
    vibrato_mode = MST_FINEVIB if finevibrato else MST_NOFINEVIB
    vibratos = []
    for num in range(1, num_instruments + 1):
        what = f"instrument {num}"
        params = cur.take(9, what)
        name = _decode_str(cur.take(constants.MAX_INSTRNAMELEN, f"{what} name"))
        song.instruments.append(
            Instrument(
                attack_decay=params[0],
                sustain_release=params[1],
                wave_ptr=params[2],
                pulse_ptr=params[3],
                filter_ptr=params[4],
                vibrato_delay=params[5],
                gateoff_timer=params[7],
                first_wave=params[8],
                name=name,
            )
        )
        vibratos.append(params[6])

    song.wavetable = _parse_table(cur, constants.WTBL)
    song.pulsetable = _parse_table(cur, constants.PTBL)
    song.filtertable = _parse_table(cur, constants.FTBL)

    num_patterns = cur.u8("pattern count")
    if not 1 <= num_patterns <= constants.MAX_PATT:
        raise SngParseError(f"bad pattern count {num_patterns}")
    song.patterns = [_parse_pattern(cur, num) for num in range(num_patterns)]
    if cur.pos != len(data):
        raise SngParseError(f"{len(data) - cur.pos} unexpected trailing bytes")

    for instrument, vibrato in zip(song.instruments, vibratos):
        instrument.vibrato_param = make_speed_entry(
            song.speedtable, vibrato, vibrato_mode
        )
    for pattern in song.patterns:
        for row in pattern.rows:
            if row.command == constants.CMD_FUNKTEMPO:
                row.data = make_speed_entry(song.speedtable, row.data, MST_FUNKTEMPO)
            elif row.command in (
                constants.CMD_PORTAUP,
                constants.CMD_PORTADOWN,
                constants.CMD_TONEPORTA,
            ):
                row.data = make_speed_entry(song.speedtable, row.data, MST_PORTAMENTO)
            elif row.command == constants.CMD_VIBRATO:
                row.data = make_speed_entry(song.speedtable, row.data, vibrato_mode)
    apply_legacy_conversions(song, GTS2_MAGIC)
    return song


class _Gts1Converter:
    """State for the GoatTracker 1.x conversion (a port of gsong.c)."""

    def __init__(self, song: Song):
        self.song = song
        self.instruments = [Instrument() for _ in range(constants.MAX_INSTR)]
        self.raw_patterns: list[list[Row]] = []
        self.numfilter = 0
        self.filtermap = [0] * 64
        self.funkdata = 0
        # GT1 per-instrument pulse parameters, for program synthesis.
        self.pulse = [0] * 32
        self.pulseadd = [0] * 32
        self.pulselimitlow = [0] * 32
        self.pulselimithigh = [0] * 32

    def read_instrument(self, num: int, cur: _Cursor) -> None:
        instrument = self.instruments[num]
        what = f"instrument {num}"
        params = cur.take(8, what)
        instrument.attack_decay = params[0]
        instrument.sustain_release = params[1]
        self.pulse[num] = params[2]
        self.pulseadd[num] = params[3]
        self.pulselimitlow[num] = params[4]
        self.pulselimithigh[num] = params[5]
        instrument.filter_ptr = params[6]  # remapped in finish()
        self.numfilter = max(self.numfilter, instrument.filter_ptr)
        if self.pulse[num] & 1:  # GT1 "no hardrestart" flag
            instrument.gateoff_timer |= 0x80
        self.pulse[num] &= 0xFE
        wave_rows = params[7] // 2
        instrument.name = _decode_str(
            cur.take(constants.MAX_INSTRNAMELEN, f"{what} name")
        )
        self._convert_wavetable(instrument, wave_rows, cur)
        self._convert_pulse(instrument, num)

    def _convert_wavetable(
        self, instrument: Instrument, rows: int, cur: _Cursor
    ) -> None:
        # Unlike the C importer, leave the pointer unset for empty
        # wavetables instead of dangling past the table end.
        if not rows:
            return
        table = self.song.wavetable
        instrument.wave_ptr = len(table) + 1
        for _ in range(rows):
            left = cur.u8("wavetable left")
            right = cur.u8("wavetable right")
            if len(table) >= constants.MAX_TABLELEN:
                continue
            if left == constants.TABLEJUMP and right:
                right = (right + instrument.wave_ptr - 1) & 0xFF
            if 0x8 <= left <= 0xF:  # GT1 silent waveforms
                left |= 0xE0
            table.add(left, right)
        # Remove a do-nothing wavetable (lone zero step + endmark).
        if (
            rows == 2
            and len(table) >= instrument.wave_ptr + 1
            and table.left[instrument.wave_ptr - 1] == 0
            and table.right[instrument.wave_ptr - 1] == 0
        ):
            del table.left[instrument.wave_ptr - 1 :]
            del table.right[instrument.wave_ptr - 1 :]
            instrument.wave_ptr = 0
        if instrument.wave_ptr > len(table):  # table was already full
            instrument.wave_ptr = 0

    def _pulse_run(self, time: int, speed: int) -> bool:
        """Append modulation steps; False once the pulsetable is full."""
        table = self.song.pulsetable
        while time:
            step = min(time, 127)
            if len(table) >= constants.MAX_TABLELEN:
                return False
            table.add(step, speed & 0xFF)
            time -= step
        return True

    def _convert_pulse(self, instrument: Instrument, num: int) -> None:
        if not self.pulse[num]:
            return
        for other in range(1, num):
            if (
                self.pulse[other] == self.pulse[num]
                and self.pulseadd[other] == self.pulseadd[num]
                and self.pulselimitlow[other] == self.pulselimitlow[num]
                and self.pulselimithigh[other] == self.pulselimithigh[num]
            ):
                instrument.pulse_ptr = self.instruments[other].pulse_ptr
                return
        table = self.song.pulsetable
        if len(table) >= constants.MAX_TABLELEN:
            return
        instrument.pulse_ptr = len(table) + 1
        table.add(0x80 | (self.pulse[num] >> 4), (self.pulse[num] << 4) & 0xFF)
        if not self.pulseadd[num]:
            if len(table) < constants.MAX_TABLELEN:
                table.add(constants.TABLEJUMP, 0)
            return
        startpulse = currentpulse = self.pulse[num] * 16
        speed = self.pulseadd[num]
        # Phase 1: from the start position to the high limit.
        pulsedist = self.pulselimithigh[num] * 16 - currentpulse
        if pulsedist > 0:
            pulsetime = pulsedist // speed
            currentpulse += pulsetime * speed
            if not self._pulse_run(pulsetime, speed // 2):
                return
        hlpos = len(table)
        # Phase 2: from the high limit to the low limit.
        pulsedist = currentpulse - self.pulselimitlow[num] * 16
        if pulsedist > 0:
            pulsetime = pulsedist // speed
            currentpulse -= pulsetime * speed
            if not self._pulse_run(pulsetime, -(speed // 2)):
                return
        # Phase 3: back up to the start position or high limit.
        if startpulse < self.pulselimithigh[num] * 16 and startpulse > currentpulse:
            pulsedist = startpulse - currentpulse
            if pulsedist > 0:
                if not self._pulse_run(pulsedist // speed, speed // 2):
                    return
            jump_target = instrument.pulse_ptr + 1
        else:
            pulsedist = self.pulselimithigh[num] * 16 - currentpulse
            if pulsedist > 0:
                if not self._pulse_run(pulsedist // speed, speed // 2):
                    return
            jump_target = hlpos + 1
        if len(table) < constants.MAX_TABLELEN:
            table.add(constants.TABLEJUMP, jump_target)

    def read_pattern(self, cur: _Cursor, num: int) -> None:
        length = cur.u8(f"pattern {num} length")
        rows = []
        for _ in range(length // 3):
            note = cur.u8("note")
            command = cur.u8("command")
            data = cur.u8("data")
            instrument = command >> 3
            command &= 7
            if note == _OLDKEYOFF:
                note = constants.KEYOFF
            elif note == _OLDREST:
                note = constants.REST
            elif note != constants.ENDPATT:
                note += constants.FIRSTNOTE
                if note > constants.LASTNOTE:
                    note = constants.REST
            if command == 5:
                command = constants.CMD_SETFILTERPTR
                self.numfilter = max(self.numfilter, data)
            elif command == 7:
                if data < 0xF0:
                    command = constants.CMD_SETTEMPO
                else:
                    command = constants.CMD_SETMASTERVOL
                    data &= 0x0F
            rows.append(Row(note, instrument, command, data))
        self.raw_patterns.append(rows)

    def _highest_used_instrument(self) -> int:
        highest = 0
        for rows in self.raw_patterns:
            for row in rows:
                if row.note == constants.ENDPATT:
                    break
                highest = max(highest, row.instrument)
        return highest

    def convert_filtertable(self, filtertable: bytes) -> None:
        table = self.song.filtertable

        def step(num: int, offset: int) -> int:
            return filtertable[num * 4 + offset]

        for num in range(64):
            self.numfilter = max(self.numfilter, step(num, 3))
        self.numfilter = min(self.numfilter, 63)
        jumppos = {}
        for num in range(1, self.numfilter + 1):
            self.filtermap[num] = len(table) + 1
            if not any(step(num, offset) for offset in range(4)):
                continue
            if step(num, 0):
                table.add(0x80 + (step(num, 1) & 0x70), step(num, 0))
                if step(num, 2):
                    table.add(0x00, step(num, 2))
            else:
                time = step(num, 1)
                while time:
                    chunk = min(time, 127)
                    table.add(chunk, step(num, 2))
                    time -= chunk
            if step(num, 3) != num + 1:
                jumppos[num] = len(table)  # 0-based index of the jump row
                table.add(constants.TABLEJUMP, step(num, 3))
        for pos in jumppos.values():
            target = table.right[pos]
            table.right[pos] = self.filtermap[target] if target < 64 else 0
        for instrument in self.instruments:
            if instrument.filter_ptr < 64:
                instrument.filter_ptr = self.filtermap[instrument.filter_ptr]
            else:
                instrument.filter_ptr = 0

    def _convert_arpeggio(self, row: Row, instrument_num: int, arpmap, next_instr):
        """Old 0XY arpeggios become wavetable programs (or instruments)."""
        param = row.data
        table = self.song.wavetable
        if not arpmap.get((instrument_num, param)):
            source = self.instruments[instrument_num]
            arpstart = len(table) + 1
            # Copy the instrument's waveforms up to its loop/end point.
            if source.wave_ptr:
                for pos in range(source.wave_ptr - 1, constants.MAX_TABLELEN):
                    if pos >= len(table) or table.left[pos] == constants.TABLEJUMP:
                        break
                    if len(table) < constants.MAX_TABLELEN:
                        table.add(table.left[pos], 0)
            arploop = len(table) + 1
            if len(table) < constants.MAX_TABLELEN - 3:
                delay = (param & 0x80) >> 7
                table.add(delay, (param & 0x70) >> 4)
                table.add(delay, param & 0x0F)
                table.add(delay, 0)
                table.add(constants.TABLEJUMP, arploop)
                if next_instr[0] < constants.MAX_INSTR:
                    new_num = next_instr[0]
                    clone = Instrument(**vars(source))
                    clone.wave_ptr = arpstart
                    if len(clone.name) < constants.MAX_INSTRNAMELEN - 3:
                        clone.name += f"0{param & 0x7F:02X}"
                    self.instruments[new_num] = clone
                    arpmap[(instrument_num, param)] = new_num + 256
                    next_instr[0] += 1
                else:
                    arpmap[(instrument_num, param)] = arpstart
        mapped = arpmap.get((instrument_num, param), 0)
        if mapped:
            if mapped < 256:
                row.command = constants.CMD_SETWAVEPTR
                row.data = mapped
            else:
                row.instrument = mapped - 256
                row.data = 0

    def fix_patterns(self) -> None:
        speedtable = self.song.speedtable
        next_instr = [self._highest_used_instrument() + 1]
        arpmap = {}
        for rows in self.raw_patterns:
            instrument_num = 0
            for row in rows:
                if row.instrument:
                    instrument_num = row.instrument
                if row.command in (
                    constants.CMD_PORTAUP,
                    constants.CMD_PORTADOWN,
                    constants.CMD_TONEPORTA,
                ):
                    row.data = make_speed_entry(speedtable, row.data, MST_PORTAMENTO)
                elif row.command == constants.CMD_VIBRATO:
                    row.data = make_speed_entry(speedtable, row.data, MST_NOFINEVIB)
                elif row.command == constants.CMD_SETFILTERPTR:
                    row.data = self.filtermap[row.data] if row.data < 64 else 0
                elif row.command == constants.CMD_SETTEMPO and not row.data:
                    row.command = constants.CMD_FUNKTEMPO
                    row.data = make_speed_entry(
                        speedtable, self.funkdata, MST_FUNKTEMPO
                    )
                elif row.command == constants.CMD_DONOTHING and row.data:
                    if (
                        constants.FIRSTNOTE <= row.note <= constants.LASTNOTE
                        and instrument_num
                    ):
                        self._convert_arpeggio(row, instrument_num, arpmap, next_instr)
                    if row.command == constants.CMD_DONOTHING:
                        row.data = 0

    def finish(self, filtertable: bytes) -> Song:
        # GT1 kept the funktempo values in the filtertable's step 0.
        self.funkdata = ((filtertable[2] << 4) | (filtertable[3] & 0x0F)) & 0xFF
        self.convert_filtertable(filtertable)
        self.fix_patterns()
        instruments = self.instruments[1:]
        _trim_instruments(instruments, self._highest_used_instrument())
        self.song.instruments = instruments
        self.song.patterns = [_strip_end_marker(rows) for rows in self.raw_patterns]
        apply_legacy_conversions(self.song, GTS1_MAGIC)
        return self.song


def _trim_instruments(instruments: list[Instrument], lowest: int) -> None:
    """Drop trailing empty instruments (but keep at least ``lowest``)."""
    while len(instruments) > max(lowest, 0) and instruments[-1] == Instrument():
        instruments.pop()


def _strip_end_marker(rows: list[Row]) -> Pattern:
    for idx, row in enumerate(rows):
        if row.note == constants.ENDPATT:
            return Pattern(rows=rows[:idx])
    return Pattern(rows=rows)


def parse_gts1(data: bytes) -> Song:
    """Parse and convert a GoatTracker 1.x (GTS!) song."""
    cur = _Cursor(data)
    cur.take(4, "identifier")
    song = _parse_header(cur)
    converter = _Gts1Converter(song)
    for num in range(1, _GTS1_INSTRUMENTS + 1):
        converter.read_instrument(num, cur)
    num_patterns = cur.u8("pattern count")
    if not 1 <= num_patterns <= constants.MAX_PATT:
        raise SngParseError(f"bad pattern count {num_patterns}")
    for num in range(num_patterns):
        converter.read_pattern(cur, num)
    filtertable = cur.take(256, "filtertable")
    if cur.pos != len(data):
        raise SngParseError(f"{len(data) - cur.pos} unexpected trailing bytes")
    return converter.finish(filtertable)
