"""GoatTracker 2 playroutine in Python.

This is a port of the reference playroutine (``gplay.c`` in the
GoatTracker 2.76 source): the sequencer, tempo/funktempo handling,
wave/pulse/filter/speed table execution, realtime commands, gateoff
timer and hard restart all follow the original tick for tick, including
its 8-bit wraparound arithmetic.

Each :meth:`Player.play_frame` call runs one 50 Hz tick and returns the
SID register writes for that frame as ``(register, value)`` pairs, in
ascending register order. Multispeed playback and the editor's jamming
and mid-song start modes are not implemented.
"""

from dataclasses import dataclass

from pygoattracker import constants
from pygoattracker.errors import GoatTrackerError
from pygoattracker.model import Song

_PATT_END = -1
_INIT, _RUNNING, _STOP, _STOPPED = range(4)

# Default empty pattern, as left by the editor's clearsong().
_EMPTY_PATTERN = [constants.REST, 0, 0, 0] * 64 + [constants.ENDPATT, 0, 0, 0]


@dataclass
class _Instr:
    """Compiled instrument parameters (raw bytes)."""

    ad: int = 0
    sr: int = 0
    wave_ptr: int = 0
    pulse_ptr: int = 0
    filter_ptr: int = 0
    speed_ptr: int = 0
    vibdelay: int = 0
    gatetimer: int = 0
    firstwave: int = 0


@dataclass
class _Channel:
    """Per-voice playroutine state (all 8/16-bit wrapping fields)."""

    trans: int = 0
    instr: int = 1
    note: int = 0
    lastnote: int = 0
    newnote: int = 0
    pattptr: int = _PATT_END
    pattnum: int = 0
    songptr: int = 0
    repeat: int = 0
    freq: int = 0
    gate: int = 0
    wave: int = 0
    pulse: int = 0
    wave_table_ptr: int = 0
    pulse_table_ptr: int = 0
    pulsetime: int = 0
    wavetime: int = 0
    vibtime: int = 0
    vibdelay: int = 0
    command: int = 0
    cmddata: int = 0
    newcommand: int = 0
    newcmddata: int = 0
    tick: int = 0
    tempo: int = 0
    mute: bool = False
    gatetimer: int = 0


class Player:
    """Play a :class:`~pygoattracker.model.Song` one frame at a time.

    Options match the GoatTracker editor defaults: ``adparam`` is the
    hard restart ADSR parameter, ``optimize_pulse`` and
    ``optimize_realtime`` are the pulse/realtime command skip
    optimizations (both enabled in the editor).
    """

    def __init__(
        self,
        song: Song,
        subtune: int = 0,
        adparam: int = constants.DEFAULT_ADPARAM,
        optimize_pulse: bool = True,
        optimize_realtime: bool = True,
        freq_table=None,
        simplepulse: bool = False,
    ):
        if not 0 <= subtune < len(song.subtunes):
            raise GoatTrackerError(f"no such subtune: {subtune}")
        # The editor playroutine (gplay.c) reads a fixed note-frequency table
        # zero-padded to 128 entries. The PACKED player (gt2reloc/player.s) lays
        # the table out as ``freqtbllo[firstnote..lastnote]`` followed
        # immediately by ``freqtblhi[...]`` with NO padding, then the songtable;
        # an out-of-range note (a wavetable relative step past the last note)
        # therefore overruns into the adjacent relocated memory and yields an
        # image-specific frequency. ``freq_table`` lets a caller (the packed-SID
        # decompiler) supply that exact 128-entry table read from the image so
        # the render reproduces the packed player's overrun frequencies. When
        # None the editor's zero-padded ``constants.FREQ_TABLE`` is used.
        self._freq_table = (
            constants.FREQ_TABLE if freq_table is None else tuple(freq_table)
        )
        # ``simplepulse`` selects the packed player's SIMPLEPULSE pulse code
        # path (greloc's one-byte pulse optimization), which computes pulse
        # width differently from the editor player. The pulse table must then
        # be the PACKED table (greloc's set-pulse byte + swapped speed); see
        # ``_pulse_exec_simple``. Default False = the editor's full-mod path,
        # so .SNG / editor playback is unchanged.
        self.simplepulse = simplepulse
        self.adparam = adparam & 0xFFFF
        self.optimize_pulse = optimize_pulse
        self.optimize_realtime = optimize_realtime
        self.regs = [0] * constants.SID_REGISTERS
        self.loops = [0] * constants.MAX_CHN
        self._compile(song, subtune)
        self._channels = [_Channel() for _ in range(constants.MAX_CHN)]
        self._funktable = [8, 5]
        self._masterfader = 0x0F
        self._filterctrl = 0
        self._filtertype = 0
        self._filtercutoff = 0
        self._filtertime = 0
        self._filterptr = 0
        self._state = _INIT
        self._last_regs = None

    def _compile(self, song: Song, subtune: int) -> None:
        """Flatten the typed model into playroutine data arrays."""
        self._order = []
        self._songlen = []
        for orderlist in song.subtunes[subtune].channels:
            entries = [entry.to_byte() for entry in orderlist.entries]
            self._order.append(entries + [constants.LOOPSONG, orderlist.restart & 0xFF])
            self._songlen.append(len(entries))
        self._instr = [_Instr()]
        for ins in song.instruments:
            self._instr.append(
                _Instr(
                    ad=ins.attack_decay & 0xFF,
                    sr=ins.sustain_release & 0xFF,
                    wave_ptr=ins.wave_ptr & 0xFF,
                    pulse_ptr=ins.pulse_ptr & 0xFF,
                    filter_ptr=ins.filter_ptr & 0xFF,
                    speed_ptr=ins.vibrato_param & 0xFF,
                    vibdelay=ins.vibrato_delay & 0xFF,
                    gatetimer=ins.gateoff_timer & 0xFF,
                    firstwave=ins.first_wave & 0xFF,
                )
            )
        while len(self._instr) < constants.MAX_INSTR:
            self._instr.append(_Instr())
        self._ltable = []
        self._rtable = []
        for table in song.tables():
            pad = constants.MAX_TABLELEN - len(table.left)
            self._ltable.append([v & 0xFF for v in table.left] + [0] * pad)
            self._rtable.append([v & 0xFF for v in table.right] + [0] * pad)
        self._patterns = []
        self._pattlen = []
        for pattern in song.patterns:
            flat = []
            for row in pattern.rows:
                flat += [
                    row.note & 0xFF,
                    row.instrument & 0xFF,
                    row.command & 0xFF,
                    row.data & 0xFF,
                ]
            flat += [constants.ENDPATT, 0, 0, 0]
            self._patterns.append(flat)
            self._pattlen.append(len(pattern.rows))
        while len(self._patterns) < constants.MAX_PATT:
            self._patterns.append(_EMPTY_PATTERN)
            self._pattlen.append(64)

    @property
    def playing(self) -> bool:
        """False once the playroutine has stopped (end of song or error)."""
        return self._state not in (_STOP, _STOPPED)

    def mute(self, channel: int, muted: bool = True) -> None:
        """Mute or unmute a voice (its oscillator keeps running)."""
        self._channels[channel].mute = muted

    def play_frame(self) -> list[tuple[int, int]]:
        """Run one player tick; return this frame's SID register writes.

        The first frame returns all 25 registers (the player assumes a
        reset chip); later frames return only registers that changed.
        After the song stops, returns no writes.
        """
        if self._state == _STOPPED:
            return []
        self._play_routine()
        if self._last_regs is None:
            writes = list(enumerate(self.regs))
        else:
            writes = [
                (reg, value)
                for reg, (value, last) in enumerate(zip(self.regs, self._last_regs))
                if value != last
            ]
        self._last_regs = list(self.regs)
        return writes

    # Playroutine internals. Structure and naming follow gplay.c.

    def _stop(self) -> None:
        if self._state != _STOPPED:
            self._state = _STOP

    def _lt(self, table: int, ptr: int) -> int:
        return self._ltable[table][(ptr - 1) & 0xFF]

    def _rt(self, table: int, ptr: int) -> int:
        return self._rtable[table][(ptr - 1) & 0xFF]

    def _freq(self, note: int) -> int:
        return self._freq_table[note & 0x7F]

    def _speed_value(self, idx: int, chan: _Channel) -> int:
        """16-bit speedtable value; high bit selects realtime calculation."""
        if not idx:
            return 0
        speed = (self._lt(constants.STBL, idx) << 8) | self._rt(constants.STBL, idx)
        if speed >= 0x8000:
            speed = (self._freq(chan.lastnote + 1) - self._freq(chan.lastnote)) & 0xFFFF
            speed >>= self._rt(constants.STBL, idx)
        return speed & 0xFFFF

    def _porta_up(self, chan: _Channel, idx: int) -> None:
        chan.freq = (chan.freq + self._speed_value(idx, chan)) & 0xFFFF

    def _porta_down(self, chan: _Channel, idx: int) -> None:
        chan.freq = (chan.freq - self._speed_value(idx, chan)) & 0xFFFF

    def _toneporta(self, chan: _Channel, idx: int) -> None:
        targetfreq = self._freq(chan.note)
        if not idx:
            chan.freq = targetfreq
            chan.vibtime = 0
            return
        speed = self._speed_value(idx, chan)
        if chan.freq < targetfreq:
            chan.freq = (chan.freq + speed) & 0xFFFF
            if chan.freq > targetfreq:
                chan.freq = targetfreq
                chan.vibtime = 0
        if chan.freq > targetfreq:
            chan.freq = (chan.freq - speed) & 0xFFFF
            if chan.freq < targetfreq:
                chan.freq = targetfreq
                chan.vibtime = 0

    def _vibrato(self, chan: _Channel, idx: int) -> None:
        speed = 0
        cmpvalue = 0
        if idx:
            cmpvalue = self._lt(constants.STBL, idx)
            speed = self._rt(constants.STBL, idx)
        if cmpvalue >= 0x80:
            cmpvalue &= 0x7F
            speed = (self._freq(chan.lastnote + 1) - self._freq(chan.lastnote)) & 0xFFFF
            speed >>= self._rt(constants.STBL, idx)
        if chan.vibtime < 0x80 and chan.vibtime > cmpvalue:
            chan.vibtime ^= 0xFF
        chan.vibtime = (chan.vibtime + 2) & 0xFF
        if chan.vibtime & 1:
            chan.freq = (chan.freq - speed) & 0xFFFF
        else:
            chan.freq = (chan.freq + speed) & 0xFFFF

    def _set_pulse_ptr(self, chan: _Channel, value: int) -> None:
        chan.pulse_table_ptr = value
        chan.pulsetime = 0
        if value and self._lt(constants.PTBL, value) == constants.TABLEJUMP:
            self._stop()

    def _set_filter_ptr(self, value: int) -> None:
        self._filterptr = value
        self._filtertime = 0
        if value and self._lt(constants.FTBL, value) == constants.TABLEJUMP:
            self._stop()

    def _sequencer(self, channel: int) -> None:
        chan = self._channels[channel]
        if self._state in (_STOP, _STOPPED) or chan.pattptr != _PATT_END:
            return
        chan.pattptr = 0
        order = self._order[channel]
        if order[chan.songptr] == constants.LOOPSONG:
            self.loops[channel] += 1
            chan.songptr = order[chan.songptr + 1]
            if chan.songptr >= self._songlen[channel]:
                self._stop()
                chan.songptr = 0
                return
        if constants.TRANSDOWN <= order[chan.songptr] < constants.LOOPSONG:
            chan.trans = (order[chan.songptr] - constants.TRANSUP) & 0xFF
            chan.songptr += 1
        if constants.REPEAT <= order[chan.songptr] < constants.TRANSDOWN:
            chan.repeat = order[chan.songptr] - constants.REPEAT
            chan.songptr += 1
        chan.pattnum = order[chan.songptr]
        if chan.repeat:
            chan.repeat -= 1
        else:
            chan.songptr += 1
        if chan.pattnum >= constants.MAX_PATT:
            self._stop()
            chan.pattnum = 0

    def _init_routine(self) -> None:
        self._filterctrl = 0
        self._filterptr = 0
        for channel in range(constants.MAX_CHN):
            chan = self._channels[channel]
            chan.songptr = 0
            chan.command = 0
            chan.cmddata = 0
            chan.newcommand = 0
            chan.newcmddata = 0
            chan.wave = 0
            chan.wave_table_ptr = 0
            chan.newnote = 0
            chan.repeat = 0
            # player.s mt_initchn primes the tick counter to 1 (not the default
            # tempo), so the first played frame decrements to tick 0 and runs as
            # a tick-0 frame -- which skips continuous effects. Priming it to the
            # tempo instead would run instrument 1's intro vibrato one frame
            # early, desyncing its phase from the real player.
            chan.tick = 1
            chan.gatetimer = self._instr[1].gatetimer & 0x3F
            chan.pattptr = _PATT_END
            chan.tempo = 5
            # Startup default tempo via instrument 63's AD parameter.
            last_instr = self._instr[constants.MAX_INSTR - 1]
            if last_instr.ad >= 2 and not last_instr.wave_ptr:
                chan.tempo = last_instr.ad - 1
            chan.trans = 0
            chan.instr = 1
            self._sequencer(channel)
        self._funktable = [8, 5]
        if self._state != _STOP:
            self._state = _RUNNING
        if 0 in self._songlen:
            self._state = _STOP

    def _filter_routine(self) -> None:
        if self._filterptr:
            ftbl = constants.FTBL
            if self._lt(ftbl, self._filterptr) == constants.TABLEJUMP:
                self._filterptr = self._rt(ftbl, self._filterptr)
            if self._filterptr:
                if not self._filtertime:
                    left = self._lt(ftbl, self._filterptr)
                    if left >= 0x80:
                        self._filtertype = left & 0x70
                        self._filterctrl = self._rt(ftbl, self._filterptr)
                        self._filterptr = (self._filterptr + 1) & 0xFF
                        if self._lt(ftbl, self._filterptr) == 0x00:
                            self._filtercutoff = self._rt(ftbl, self._filterptr)
                            self._filterptr = (self._filterptr + 1) & 0xFF
                    elif left:
                        self._filtertime = left
                    else:
                        self._filtercutoff = self._rt(ftbl, self._filterptr)
                        self._filterptr = (self._filterptr + 1) & 0xFF
                if self._filtertime:
                    self._filtercutoff = (
                        self._filtercutoff + self._rt(ftbl, self._filterptr)
                    ) & 0xFF
                    self._filtertime -= 1
                    if not self._filtertime:
                        self._filterptr = (self._filterptr + 1) & 0xFF
        self.regs[constants.FC_LO_REG] = 0x00
        self.regs[constants.FC_HI_REG] = self._filtercutoff
        self.regs[constants.RES_FILT_REG] = self._filterctrl
        self.regs[constants.MODE_VOL_REG] = self._filtertype | self._masterfader

    def _wave_command(self, channel: int, chan: _Channel, command: int) -> None:
        """Pattern command executed from a wavetable step ($F0-$FE)."""
        param = self._rt(constants.WTBL, chan.wave_table_ptr)
        if command in (
            constants.CMD_DONOTHING,
            constants.CMD_SETWAVEPTR,
            constants.CMD_FUNKTEMPO,
        ):
            self._stop()
        elif command == constants.CMD_PORTAUP:
            self._porta_up(chan, param)
        elif command == constants.CMD_PORTADOWN:
            self._porta_down(chan, param)
        elif command == constants.CMD_TONEPORTA:
            self._toneporta(chan, param)
        elif command == constants.CMD_VIBRATO:
            self._vibrato(chan, param)
        elif command == constants.CMD_SETAD:
            self.regs[constants.AD_REG + 7 * channel] = param
        elif command == constants.CMD_SETSR:
            self.regs[constants.SR_REG + 7 * channel] = param
        elif command == constants.CMD_SETWAVE:
            chan.wave = param
        elif command == constants.CMD_SETPULSEPTR:
            self._set_pulse_ptr(chan, param)
        elif command == constants.CMD_SETFILTERPTR:
            self._set_filter_ptr(param)
        elif command == constants.CMD_SETFILTERCTRL:
            self._filterctrl = param
            if not self._filterctrl:
                self._filterptr = 0
        elif command == constants.CMD_SETFILTERCUTOFF:
            self._filtercutoff = param
        elif command == constants.CMD_SETMASTERVOL:
            if param < 0x10:
                self._masterfader = param

    def _new_note_init(self, channel: int, chan: _Channel, iptr: _Instr) -> None:
        chan.note = (chan.newnote - constants.FIRSTNOTE) & 0xFF
        chan.command = 0
        chan.vibdelay = iptr.vibdelay
        chan.cmddata = iptr.speed_ptr
        if chan.newcommand == constants.CMD_TONEPORTA:
            return
        if iptr.firstwave:
            if iptr.firstwave >= 0xFE:
                chan.gate = iptr.firstwave
            else:
                chan.wave = iptr.firstwave
                chan.gate = 0xFF
        chan.wave_table_ptr = iptr.wave_ptr
        if chan.wave_table_ptr:
            if self._lt(constants.WTBL, chan.wave_table_ptr) == constants.TABLEJUMP:
                self._stop()
        if iptr.pulse_ptr:
            self._set_pulse_ptr(chan, iptr.pulse_ptr)
        if iptr.filter_ptr:
            self._set_filter_ptr(iptr.filter_ptr)
        self.regs[constants.AD_REG + 7 * channel] = iptr.ad
        self.regs[constants.SR_REG + 7 * channel] = iptr.sr

    def _tick0_command(self, channel: int, chan: _Channel, iptr: _Instr) -> None:
        command = chan.newcommand
        data = chan.newcmddata
        if command == constants.CMD_DONOTHING:
            chan.command = 0
            chan.cmddata = iptr.speed_ptr
        elif command in (constants.CMD_PORTAUP, constants.CMD_PORTADOWN):
            chan.vibtime = 0
            chan.command = command
            chan.cmddata = data
        elif command in (constants.CMD_TONEPORTA, constants.CMD_VIBRATO):
            chan.command = command
            chan.cmddata = data
        elif command == constants.CMD_SETAD:
            self.regs[constants.AD_REG + 7 * channel] = data
        elif command == constants.CMD_SETSR:
            self.regs[constants.SR_REG + 7 * channel] = data
        elif command == constants.CMD_SETWAVE:
            chan.wave = data
        elif command == constants.CMD_SETWAVEPTR:
            chan.wave_table_ptr = data
            chan.wavetime = 0
            if data and self._lt(constants.WTBL, data) == constants.TABLEJUMP:
                self._stop()
        elif command == constants.CMD_SETPULSEPTR:
            self._set_pulse_ptr(chan, data)
        elif command == constants.CMD_SETFILTERPTR:
            self._set_filter_ptr(data)
        elif command == constants.CMD_SETFILTERCTRL:
            self._filterctrl = data
            if not self._filterctrl:
                self._filterptr = 0
        elif command == constants.CMD_SETFILTERCUTOFF:
            self._filtercutoff = data
        elif command == constants.CMD_SETMASTERVOL:
            if data < 0x10:
                self._masterfader = data
        elif command == constants.CMD_FUNKTEMPO:
            if data:
                self._funktable[0] = (self._lt(constants.STBL, data) - 1) & 0xFF
                self._funktable[1] = (self._rt(constants.STBL, data) - 1) & 0xFF
            for other in self._channels:
                other.tempo = 0
        elif command == constants.CMD_SETTEMPO:
            newtempo = data & 0x7F
            if newtempo >= 3:
                newtempo -= 1
            if data >= 0x80:
                chan.tempo = newtempo
            else:
                for other in self._channels:
                    other.tempo = newtempo

    def _wave_exec(self, channel: int, chan: _Channel) -> bool:
        """Run one wavetable step. True skips realtime commands this tick."""
        if not chan.wave_table_ptr:
            return False
        wave = self._lt(constants.WTBL, chan.wave_table_ptr)
        note = self._rt(constants.WTBL, chan.wave_table_ptr)
        if wave > constants.WAVELASTDELAY:
            if wave < constants.WAVESILENT:
                chan.wave = wave
            elif wave <= constants.WAVELASTSILENT:
                chan.wave = wave & 0x0F
            elif wave <= constants.WAVELASTCMD:
                self._wave_command(channel, chan, wave & 0x0F)
        elif chan.wavetime != wave:
            chan.wavetime += 1
            return False
        chan.wavetime = 0
        chan.wave_table_ptr = (chan.wave_table_ptr + 1) & 0xFF
        if self._lt(constants.WTBL, chan.wave_table_ptr) == constants.TABLEJUMP:
            chan.wave_table_ptr = self._rt(constants.WTBL, chan.wave_table_ptr)
        if constants.WAVECMD <= wave <= constants.WAVELASTCMD:
            return True
        if note != 0x80:
            if note < 0x80:
                note = (note + chan.note) & 0xFF
            note &= 0x7F
            chan.freq = self._freq(note)
            chan.vibtime = 0
            chan.lastnote = note
            return True
        return False

    def _tickn_command(self, chan: _Channel) -> None:
        command = chan.command
        if command == constants.CMD_PORTAUP:
            self._porta_up(chan, chan.cmddata)
        elif command == constants.CMD_PORTADOWN:
            self._porta_down(chan, chan.cmddata)
        elif command == constants.CMD_DONOTHING:
            # Instrument vibrato (player.s mt_effect_0). Speed 0 = no vibrato.
            # Otherwise count the vibrato delay down and vibrate once it runs
            # out. The real player decrements while the delay is non-zero and
            # vibrates at zero; the channel's delay starts at zero (the reset
            # loop clears it and mt_initchn never reloads it), so an
            # instrument-1 speed-table vibrato is audible from the gate-off
            # intro frames -- the editor's gplay.c skips it instead. A note's
            # init reloads the delay (>= 1), so this stays byte-exact with the
            # editor for normal notes.
            if not chan.cmddata:
                return
            if chan.vibdelay > 1:
                chan.vibdelay -= 1
                return
            self._vibrato(chan, chan.cmddata)
        elif command == constants.CMD_VIBRATO:
            self._vibrato(chan, chan.cmddata)
        elif command == constants.CMD_TONEPORTA:
            self._toneporta(chan, chan.cmddata)

    def _pulse_exec(self, chan: _Channel) -> None:
        if self.simplepulse:
            self._pulse_exec_simple(chan)
            return
        ptbl = constants.PTBL
        if self._lt(ptbl, chan.pulse_table_ptr) == constants.TABLEJUMP:
            chan.pulse_table_ptr = self._rt(ptbl, chan.pulse_table_ptr)
            if not chan.pulse_table_ptr:
                return
        if not chan.pulsetime:
            left = self._lt(ptbl, chan.pulse_table_ptr)
            if left >= 0x80:
                chan.pulse = ((left & 0x0F) << 8) | self._rt(ptbl, chan.pulse_table_ptr)
                chan.pulse_table_ptr = (chan.pulse_table_ptr + 1) & 0xFF
            else:
                chan.pulsetime = left
        if chan.pulsetime:
            speed = self._rt(ptbl, chan.pulse_table_ptr)
            if speed >= 0x80:
                speed -= 0x100
            chan.pulse = (chan.pulse + speed) & 0xFFF
            chan.pulsetime -= 1
            if not chan.pulsetime:
                chan.pulse_table_ptr = (chan.pulse_table_ptr + 1) & 0xFF

    def _pulse_exec_simple(self, chan: _Channel) -> None:
        """SIMPLEPULSE pulse executor (player.s mt_setpulse/mt_pulsemod,
        ``.IF SIMPLEPULSE != 0``).

        greloc's SIMPLEPULSE optimization (greloc.c ~888/1302) packs the
        pulse table so a SET-PULSE step is ONE byte ``(pulsehi & 0x0f) |
        (pulselo & 0xf0)`` and the modulation speed is pre-swapped
        (``swapnybbles``). The packed player keeps a single ghost pulse byte:
        mt_setpulse stores that one packed byte to BOTH ghostpulselo and
        ghostpulsehi (no separate hi store, no ``& 0x0F``), and mt_pulsemod
        does an 8-bit ``lo = lo + speed + carry`` accumulate writing the same
        byte to both lo and hi. The SID then masks the high pulse nibble to 12
        bits. The editor player (``_pulse_exec``) instead computes
        ``pulse = (left & 0x0f) << 8 | right`` giving pulse-hi 0 where this
        packed player gives pulse-hi = the packed byte's low nibble.

        ``chan.pulse`` carries that single packed byte in its low 8 bits; the
        12-bit SID pulse is ``((byte & 0x0f) << 8) | byte``.
        """
        ptbl = constants.PTBL
        if self._lt(ptbl, chan.pulse_table_ptr) == constants.TABLEJUMP:
            chan.pulse_table_ptr = self._rt(ptbl, chan.pulse_table_ptr)
            if not chan.pulse_table_ptr:
                return
        byte = chan.pulse & 0xFF
        if not chan.pulsetime:
            left = self._lt(ptbl, chan.pulse_table_ptr)
            if left >= 0x80:
                # Set pulse: one packed byte -> both lo and hi ghost registers.
                byte = self._rt(ptbl, chan.pulse_table_ptr)
                chan.pulse = ((byte & 0x0F) << 8) | byte
                chan.pulse_table_ptr = (chan.pulse_table_ptr + 1) & 0xFF
                return
            chan.pulsetime = left
        if chan.pulsetime:
            # mt_pulsemod: lo = lo + speed + carry-out; hi = lo (same byte).
            speed = self._rt(ptbl, chan.pulse_table_ptr)
            total = byte + speed
            byte = (total + (total >> 8)) & 0xFF
            chan.pulse = ((byte & 0x0F) << 8) | byte
            chan.pulsetime -= 1
            if not chan.pulsetime:
                chan.pulse_table_ptr = (chan.pulse_table_ptr + 1) & 0xFF

    def _get_new_notes(self, channel: int, chan: _Channel) -> None:
        pattern = self._patterns[chan.pattnum]
        newnote = pattern[chan.pattptr]
        if pattern[chan.pattptr + 1]:
            chan.instr = pattern[chan.pattptr + 1]
        chan.newcommand = pattern[chan.pattptr + 2]
        chan.newcmddata = pattern[chan.pattptr + 3]
        chan.pattptr += 4
        if pattern[chan.pattptr] == constants.ENDPATT:
            chan.pattptr = _PATT_END
        if newnote == constants.KEYOFF:
            chan.gate = 0xFE
        if newnote == constants.KEYON:
            chan.gate = 0xFF
        if newnote <= constants.LASTNOTE:
            chan.newnote = (newnote + chan.trans) & 0xFF
            if chan.newcommand != constants.CMD_TONEPORTA:
                iptr = self._instr[chan.instr & 0x3F]
                if not iptr.gatetimer & 0x40:
                    chan.gate = 0xFE
                    if not iptr.gatetimer & 0x80:
                        self.regs[constants.AD_REG + 7 * channel] = self.adparam >> 8
                        self.regs[constants.SR_REG + 7 * channel] = self.adparam & 0xFF

    def _write_voice_regs(self, channel: int, chan: _Channel) -> None:
        base = constants.VOICE_REG_SIZE * channel
        if chan.mute:
            chan.wave = 0x08
            self.regs[constants.CONTROL_REG + base] = chan.wave
            return
        self.regs[constants.FREQ_LO_REG + base] = chan.freq & 0xFF
        self.regs[constants.FREQ_HI_REG + base] = chan.freq >> 8
        self.regs[constants.PULSE_LO_REG + base] = chan.pulse & 0xFF
        self.regs[constants.PULSE_HI_REG + base] = chan.pulse >> 8
        self.regs[constants.CONTROL_REG + base] = chan.wave & chan.gate

    def _play_channel(self, channel: int) -> None:
        chan = self._channels[channel]
        iptr = self._instr[chan.instr & 0x3F]
        chan.tick = (chan.tick - 1) & 0xFF
        skip_tickn = False
        if chan.tick == 0:
            # Tick 0: sequencer, new note init, one-shot commands.
            self._sequencer(channel)
            chan.gatetimer = iptr.gatetimer & 0x3F
            if chan.newnote:
                self._new_note_init(channel, chan, iptr)
            self._tick0_command(channel, chan, iptr)
            if chan.newnote:
                chan.newnote = 0
                if chan.newcommand != constants.CMD_TONEPORTA:
                    self._write_voice_regs(channel, chan)
                    return
            skip_tickn = self._wave_exec(channel, chan)
        else:
            if chan.tick >= 0x80:
                if chan.tempo >= 2:
                    chan.tick = chan.tempo
                else:
                    chan.tick = self._funktable[chan.tempo]
                    chan.tempo ^= 1
                if chan.gatetimer > chan.tick:
                    self._stop()
            skip_tickn = self._wave_exec(channel, chan)
        if not skip_tickn and (not self.optimize_realtime or chan.tick):
            self._tickn_command(chan)
        # Pulse execution, with the editor's default skip optimizations.
        # A mid-frame stop request still finishes the frame, as in the
        # original (PLAY_STOP only becomes PLAY_STOPPED next call).
        fetch_tick = chan.tick == chan.gatetimer
        if not (self.optimize_pulse and fetch_tick):
            if chan.pulse_table_ptr:
                if self.optimize_pulse and not chan.tick and not chan.pattptr:
                    self._write_voice_regs(channel, chan)
                    return
                self._pulse_exec(chan)
        if fetch_tick:
            self._get_new_notes(channel, chan)
        self._write_voice_regs(channel, chan)

    def _play_routine(self) -> None:
        if self._state in (_INIT, _STOP):
            if self._state == _STOP:
                self._state = _STOPPED
                return
            self._init_routine()
            return
        self._filter_routine()
        for channel in range(constants.MAX_CHN):
            self._play_channel(channel)


def iter_frames(
    song: Song,
    subtune: int = 0,
    max_frames: int | None = None,
    until_loop: bool = False,
    **player_options,
):
    """Yield per-frame register write lists for ``song``.

    Stops after ``max_frames`` frames, when the playroutine stops, or --
    with ``until_loop`` -- once every channel has looped at least once.
    """
    player = Player(song, subtune=subtune, **player_options)
    frame = 0
    while max_frames is None or frame < max_frames:
        if until_loop and min(player.loops) > 0:
            return
        writes = player.play_frame()
        if not player.playing and not writes:
            return
        yield writes
        frame += 1
