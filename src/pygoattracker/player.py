"""GoatTracker 2 playroutine in Python.

:class:`Player` is a :class:`pysidtracker.MemPlayer`: it inherits the
flat memory image, the post-init snapshot, the diffing :meth:`play_frame` and the
:meth:`render_grid`/:meth:`iter_frames` drivers, and implements only the two
tracker-specific hooks -- :meth:`_init` (build the playroutine state and run the
editor's reset) and :meth:`_frame` (advance one play call, writing this frame's
SID registers into ``$D400``). Everything else here is a private transcription of
the reference playroutine (``gplay.c`` / ``player.s`` in the GoatTracker 2.76
source): the sequencer, tempo/funktempo handling, wave/pulse/filter/speed table
execution, realtime commands, gateoff timer and hard restart, all following the
original tick for tick with its 8-bit wraparound arithmetic, over the channels and
voices the :class:`~pygoattracker.packed.Build` lays out. The editor's
jamming/mid-song-start modes are not implemented.
"""

from dataclasses import dataclass, replace

from pysidtracker import MemPlayer

from pygoattracker import constants
from pygoattracker.errors import GoatTrackerError
from pygoattracker.model import Song
from pygoattracker.packed import Build, PlayerState, default_voices
from pygoattracker.typed import TypedSong

_PATT_END = -1
_CHIP = 0x20
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
    pulse_init: int | None = None  # the width variable init left, not yet on the chip
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


@dataclass
class _Filter:
    """One chip's filter state, which the channels it sounds share."""

    ctrl: int = 0
    time: int = 0
    ptr: int = 0
    cutoff: int = 0
    type: int = 0


class Player(MemPlayer):
    """Play a :class:`~pygoattracker.model.Song` (or a ``TypedSong``) one frame at a time.

    A :class:`pysidtracker.MemPlayer`: :meth:`~pysidtracker.MemPlayer.play_frame`,
    :meth:`~pysidtracker.MemPlayer.render_grid` and
    :meth:`~pysidtracker.MemPlayer.iter_frames` come from the base; this class
    only builds the playroutine state and runs the per-frame tick.

    ``build`` holds the playback options (:class:`~pygoattracker.packed.Build`, the
    editor's by default); keyword ``options`` override its fields.
    """

    MASK_PW_HI = True

    def __init__(
        self,
        song: Song | TypedSong,
        subtune: int = 0,
        build: Build | None = None,
        state: PlayerState | None = None,
        freq_table=None,
        **options,
    ):
        build = replace(build or Build(), **options)
        if isinstance(song, TypedSong):
            build = replace(build, simplepulse=build.simplepulse or song.simplepulse)
            song = song.to_song()
        if not 0 <= subtune < len(song.subtunes):
            raise GoatTrackerError(f"no such subtune: {subtune}")
        self._song = song
        self._voices = build.voices or default_voices(
            len(song.subtunes[subtune].channels)
        )
        self._nchan = len(self._voices)
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
        self.build = replace(
            build, adparam=build.adparam & 0xFFFF, init_regs=tuple(build.init_regs)
        )
        self._init_state = state
        self.loops = [0] * self._nchan
        # MemPlayer has no code image to mount (this is a model-driven, not a
        # 6502-driven, transcription); it seeds ``$D418`` to max volume, runs
        # ``_init`` (below), then snapshots the register file so ``render_grid``
        # emits the same forward-filled 25-register grid the oracle compares.
        super().__init__(b"", 0, subtune)

    @property
    def simplepulse(self) -> bool:
        """Whether the packed SIMPLEPULSE pulse code runs."""
        return self.build.simplepulse

    @property
    def live_vibrato(self) -> bool:
        """Whether instrument vibrato reads the current instrument's param."""
        return self.build.live_vibrato

    def _init(self, subtune: int) -> None:
        """Build the playroutine data + state; the first play call inits the song.

        A ``state`` is the full-ghosting build (greloc ``GHOSTREGS``): the code
        writes a 25-byte ghost of the SID that ``mt_play`` copies to the chip
        before it runs, so each call outputs the previous call's registers. The
        ghost's voice bytes are the channels' frequency and pulse variables, and
        init keeps them and the filter/volume immediates. A chip-writing build's
        ``state`` seeds the same variables, which init leaves as the image loaded,
        and the envelopes a build writing them every tick outputs from its first;
        its song-init call outputs the registers ``state.init`` loads from them.
        Either seeds the channel variables ``state.channels`` init keeps.
        """
        for reg, value in self.build.init_regs:
            self._mem[self.SID_BASE + reg] = value
        self._compile(self._song, subtune)
        self._channels = [_Channel() for _ in range(self._nchan)]
        self._filters = [_Filter() for _ in range(0, self._nchan, constants.MAX_CHN)]
        self._banks = {}
        state = self._init_state
        self._funktable = [8, 5]
        self._masterfader = 0x0F
        self._ghost = None
        self._envelopes = ()
        self._init_writes = ()
        if state is not None:
            self._init_writes = state.init
            regs = state.registers
            home = [
                (channel, off)
                for channel, off in enumerate(self._voices)
                if off is not None and off < _CHIP
            ]
            if state.ghost:
                self._ghost = list(regs)
            else:
                self._envelopes = tuple(
                    (off + reg, regs[off + reg])
                    for _, off in home
                    for reg in (constants.AD_REG, constants.SR_REG)
                )
            self._funktable = list(state.funk)
            self._masterfader = state.volume & 0x0F
            filt = self._filters[home[0][0] // constants.MAX_CHN if home else 0]
            filt.cutoff, filt.type = state.cutoff, state.filttype
            for channel, off in home:
                chan = self._channels[channel]
                chan.freq = regs[off + 1] << 8 | regs[off]
                chan.pulse = regs[off + 3] << 8 | regs[off + 2]
            for name, values in state.channels:
                for chan, value in zip(self._channels, values):
                    setattr(chan, name, value)
        self._wrapper_first = self.build.wrapper_flush
        self._effects = {
            constants.CMD_PORTAUP: self._porta_up,
            constants.CMD_PORTADOWN: self._porta_down,
            constants.CMD_TONEPORTA: self._toneporta,
            constants.CMD_VIBRATO: self._vibrato,
        }
        self._pulse_step = (
            self._pulse_step_simple if self.build.simplepulse else self._pulse_step_full
        )
        self._state = _INIT

    def _frame(self) -> None:
        """Advance one play call, writing what it outputs to the SID memory.

        A play call runs :attr:`~pygoattracker.packed.Build.calls` player ticks. A
        full-ghosting build outputs the ghost the previous tick left, then updates
        it; a play-wrapper flush build starts from its all-zero ghost, so its first
        frame carries a muted $D418.
        """
        out = self.regs
        for _ in range(self.build.calls):
            if self._ghost is not None:
                out = list(self._ghost)
                self.regs = self._ghost
            self._play_call()
        if self._wrapper_first:
            out = list(out)
            out[constants.MODE_VOL_REG] &= 0xF0
            self._wrapper_first = False
        for chip, bank in ((0, out), *self._banks.items()):
            base = self.SID_BASE + chip * _CHIP
            self._mem[base : base + constants.SID_REGISTERS] = bytes(
                v & 0xFF for v in bank
            )

    def _bank(self, chip: int) -> list:
        """The register file of ``chip`` (``$20`` apart from ``$D400``)."""
        if not chip:
            return self.regs
        return self._banks.setdefault(chip, [0] * constants.SID_REGISTERS)

    def _reg(self, channel: int, reg: int, value: int) -> None:
        """Write voice register ``reg`` of ``channel``, where its writes reach a chip."""
        off = self._voices[channel]
        if off is None:
            return
        mask = self.build.overlay and self.build.overlay[channel]
        if mask and reg == constants.CONTROL_REG:
            value = value & mask[0] | mask[1]
        self._bank(off // _CHIP)[off % _CHIP + reg] = value

    def _filter_bank(self, group: int):
        """The register file the filter of channel ``group`` writes, None if silent."""
        offs = self._voices[group * constants.MAX_CHN : (group + 1) * constants.MAX_CHN]
        chip = next((off // _CHIP for off in offs if off is not None), None)
        return None if chip is None else self._bank(chip)

    def _play_call(self) -> None:
        """One ``mt_play``: song init on the first call, else one player tick.

        The song's stop request only takes effect one frame later (as in the
        original: ``PLAY_STOP`` becomes ``PLAY_STOPPED`` on the next call), so a
        mid-frame stop still finishes the frame.
        """
        if self._state == _INIT:
            self._init_routine()
            for reg, value in self._init_writes:
                self.regs[reg] = value
            return
        for reg, value in self._envelopes:
            self.regs[reg] = value
        self._envelopes = ()
        if self._state == _STOPPED:
            return
        if self._state == _STOP:
            self._state = _STOPPED
            return
        for group in range(len(self._filters)):
            self._filter_routine(group)
        for channel in range(self._nchan):
            if channel not in self.build.idle:
                self._play_channel(channel)

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
                    # delay 0 = no instrument vibrato (editor gplay.c, packer greloc)
                    speed_ptr=(ins.vibrato_param & 0xFF) if ins.vibrato_delay else 0,
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
            pad = 0x100 - len(table.left)
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
            self._toneporta_reached(chan)
            return
        speed = self._speed_value(idx, chan)
        if chan.freq < targetfreq:
            chan.freq = (chan.freq + speed) & 0xFFFF
            if chan.freq > targetfreq:
                self._toneporta_reached(chan)
        if chan.freq > targetfreq:
            chan.freq = (chan.freq - speed) & 0xFFFF
            if chan.freq < targetfreq:
                self._toneporta_reached(chan)

    def _toneporta_reached(self, chan: _Channel) -> None:
        """Toneporta has reached/crossed the target note (player.s
        ``mt_effect_3_found``): snap freq to the target, reset the vibrato phase,
        and -- for the calculated-speed build (``mt_wavenoteabs``) -- latch the
        channel ``lastnote`` to the target note. Without the lastnote latch, a
        following calculated-speed (instrument or pattern) vibrato keeps deriving
        its step from the PRE-porta note interval, so its depth is off by the
        note-distance the porta covered (the dominant GoatTracker FREQ-class
        mismatch). ``mt_chnlastnote`` is only written under ``NOCALCULATEDSPEED``,
        so we mirror that by only latching it here."""
        chan.freq = self._freq(chan.note)
        chan.vibtime = 0
        chan.lastnote = chan.note

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

    def _stop_at_jump(self, table: int, ptr: int) -> None:
        """Stop the song if ``ptr`` starts on a jump of ``table``."""
        if ptr and self._lt(table, ptr) == constants.TABLEJUMP:
            self._stop()

    def _set_pulse_ptr(self, chan: _Channel, value: int) -> None:
        chan.pulse_table_ptr = value
        chan.pulsetime = 0
        self._stop_at_jump(constants.PTBL, value)

    def _set_filter_ptr(self, channel: int, value: int) -> None:
        filt = self._filters[channel // constants.MAX_CHN]
        filt.ptr = value
        filt.time = 0
        self._stop_at_jump(constants.FTBL, value)

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
        for filt in self._filters:
            filt.ctrl = 0
            filt.ptr = 0
        for channel in range(self._nchan):
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
            chan.tempo = self._default_tempo()
            chan.trans = 0
            chan.instr = 1
            self._sequencer(channel)
            self._reg(channel, constants.CONTROL_REG, chan.wave & chan.gate)
        for group in range(len(self._filters)):
            bank = self._filter_bank(group)
            if bank is not None:
                bank[constants.FC_LO_REG] = 0
        if self._state != _STOP:
            self._state = _RUNNING
        if 0 in self._songlen[: self._nchan]:
            self._state = _STOP

    def _default_tempo(self) -> int:
        if self.build.default_tempo is not None:
            return self.build.default_tempo
        last_instr = self._instr[constants.MAX_INSTR - 1]
        if last_instr.ad >= 2 and not last_instr.wave_ptr:
            return last_instr.ad - 1
        return 5

    def _filter_routine(self, group: int) -> None:
        filt = self._filters[group]
        if filt.ptr:
            ftbl = constants.FTBL
            if self._lt(ftbl, filt.ptr) == constants.TABLEJUMP:
                filt.ptr = self._rt(ftbl, filt.ptr)
            if filt.ptr:
                if not filt.time:
                    left = self._lt(ftbl, filt.ptr)
                    if left >= 0x80:
                        filt.type = left & 0x70
                        filt.ctrl = self._rt(ftbl, filt.ptr)
                        filt.ptr = (filt.ptr + 1) & 0xFF
                        if self._lt(ftbl, filt.ptr) == 0x00:
                            filt.cutoff = self._rt(ftbl, filt.ptr)
                            filt.ptr = (filt.ptr + 1) & 0xFF
                    elif left:
                        filt.time = left
                    else:
                        filt.cutoff = self._rt(ftbl, filt.ptr)
                        filt.ptr = (filt.ptr + 1) & 0xFF
                if filt.time:
                    filt.cutoff = (filt.cutoff + self._rt(ftbl, filt.ptr)) & 0xFF
                    filt.time -= 1
                    if not filt.time:
                        filt.ptr = (filt.ptr + 1) & 0xFF
        bank = self._filter_bank(group)
        if bank is not None:
            bank[constants.FC_LO_REG] = 0x00
            bank[constants.FC_HI_REG] = filt.cutoff
            bank[constants.RES_FILT_REG] = filt.ctrl
            bank[constants.MODE_VOL_REG] = filt.type | self._masterfader

    def _set_command(
        self, channel: int, chan: _Channel, command: int, param: int
    ) -> None:
        """The one-shot commands the wavetable and pattern tick 0 share."""
        filt = self._filters[channel // constants.MAX_CHN]
        if command == constants.CMD_SETAD:
            self._reg(channel, constants.AD_REG, param)
        elif command == constants.CMD_SETSR:
            self._reg(channel, constants.SR_REG, param)
        elif command == constants.CMD_SETWAVE:
            chan.wave = param
        elif command == constants.CMD_SETPULSEPTR:
            self._set_pulse_ptr(chan, param)
        elif command == constants.CMD_SETFILTERPTR:
            self._set_filter_ptr(channel, param)
        elif command == constants.CMD_SETFILTERCTRL:
            filt.ctrl = param
            if not filt.ctrl:
                filt.ptr = 0
        elif command == constants.CMD_SETFILTERCUTOFF:
            filt.cutoff = param
        elif command == constants.CMD_SETMASTERVOL:
            if param < 0x10:
                self._masterfader = param

    def _wave_command(self, channel: int, chan: _Channel, command: int) -> None:
        """Pattern command executed from a wavetable step ($F0-$FE)."""
        param = self._rt(constants.WTBL, chan.wave_table_ptr)
        if command in (
            constants.CMD_DONOTHING,
            constants.CMD_SETWAVEPTR,
            constants.CMD_FUNKTEMPO,
        ):
            self._stop()
        elif command in self._effects:
            self._effects[command](chan, param)
        else:
            self._set_command(channel, chan, command, param)

    def _new_note_init(self, channel: int, chan: _Channel, iptr: _Instr) -> None:
        chan.note = (chan.newnote - constants.FIRSTNOTE) & 0xFF
        chan.command = 0
        chan.vibdelay = iptr.vibdelay
        if self.build.vibinit:
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
        self._stop_at_jump(constants.WTBL, chan.wave_table_ptr)
        if iptr.pulse_ptr:
            self._set_pulse_ptr(chan, iptr.pulse_ptr)
        if iptr.filter_ptr:
            self._set_filter_ptr(channel, iptr.filter_ptr)
        self._reg(channel, constants.AD_REG, iptr.ad)
        self._reg(channel, constants.SR_REG, iptr.sr)

    def _tick0_command(self, channel: int, chan: _Channel, iptr: _Instr) -> None:
        command = chan.newcommand
        data = chan.newcmddata
        if command == constants.CMD_DONOTHING:
            chan.command = 0
            chan.cmddata = iptr.speed_ptr
        elif command in self._effects:
            if command in (constants.CMD_PORTAUP, constants.CMD_PORTADOWN):
                chan.vibtime = 0
            chan.command = command
            chan.cmddata = data
        elif command == constants.CMD_SETWAVEPTR:
            chan.wave_table_ptr = data
            chan.wavetime = 0
            self._stop_at_jump(constants.WTBL, data)
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
        else:
            self._set_command(channel, chan, command, data)

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
        if command in self._effects:
            self._effects[command](chan, chan.cmddata)
        elif command == constants.CMD_DONOTHING:
            # Instrument vibrato (player.s mt_effect_0). Speed 0 = no vibrato AND
            # no vibdelay decrement (mt_effect_0's leading ``beq`` is before the
            # dec). vibdelay is channel-retained, reloaded only at note-init
            # (_new_note_init), matching mt_chnvibdelay; the channel delay starts
            # as init leaves it (neither the reset loop nor mt_initchn loads it),
            # so an instrument-1 speed-table vibrato can sound from the gate-off
            # intro frames -- the editor's gplay.c skips it instead.
            #
            # ``param`` source is build-dependent. The packed instrument-vibrato-
            # only build (NOEFFECTS != 0, ``live_vibrato``) reads it LIVE from
            # the channel's CURRENT instrument every tick (mt_chninstr ->
            # mt_insvibparam-1), so at a gate-off boundary -- where mt_getnewnote
            # flips mt_chninstr to the new note's instrument 1-2 frames before
            # the note inits -- it vibrates the held (old) frequency with the NEW
            # instrument's param. The full-FX build (NOEFFECTS == 0) and the
            # editor instead use ``cmddata``, the param LATCHED at note-init.
            if self.build.live_vibrato:
                param = self._instr[chan.instr & 0x3F].speed_ptr
            else:
                param = chan.cmddata
            if not (chan.vibdelay if self.build.vibstop else param):
                return
            if chan.vibdelay > 1:
                chan.vibdelay -= 1
                return
            self._vibrato(chan, param)

    def _pulse_exec(self, chan: _Channel) -> None:
        if chan.pulse_init is not None:
            chan.pulse, chan.pulse_init = chan.pulse_init, None
        ptbl = constants.PTBL
        if self._lt(ptbl, chan.pulse_table_ptr) == constants.TABLEJUMP:
            chan.pulse_table_ptr = self._rt(ptbl, chan.pulse_table_ptr)
            if not chan.pulse_table_ptr:
                return
        self._pulse_step(chan)

    def _pulse_step_full(self, chan: _Channel) -> None:
        ptbl = constants.PTBL
        if not chan.pulsetime:
            left = self._lt(ptbl, chan.pulse_table_ptr)
            if left >= 0x80:
                chan.pulse = ((left & 0x0F) << 8) | self._rt(ptbl, chan.pulse_table_ptr)
                chan.pulse_table_ptr = (chan.pulse_table_ptr + 1) & 0xFF
                return
            chan.pulsetime = left
        # mt_newpulsemod: a $00 step modulates and its dec wraps to 256 steps.
        speed = self._rt(ptbl, chan.pulse_table_ptr)
        if speed >= 0x80:
            speed -= 0x100
        chan.pulse = (chan.pulse + speed * self.build.pulse_step) & 0xFFF
        chan.pulsetime = (chan.pulsetime - 1) & 0xFF
        if not chan.pulsetime:
            chan.pulse_table_ptr = (chan.pulse_table_ptr + 1) & 0xFF

    def _pulse_step_simple(self, chan: _Channel) -> None:
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
        bits. The editor player (``_pulse_step_full``) instead computes
        ``pulse = (left & 0x0f) << 8 | right`` giving pulse-hi 0 where this
        packed player gives pulse-hi = the packed byte's low nibble.

        ``chan.pulse`` carries that single packed byte in its low 8 bits; the
        12-bit SID pulse is ``((byte & 0x0f) << 8) | byte``.
        """
        ptbl = constants.PTBL
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
        if chan.pattptr == 4 * self._pattlen[chan.pattnum]:
            chan.pattptr = _PATT_END
        if newnote > constants.REST:
            chan.gate = newnote | 0xF0
        if newnote <= constants.LASTNOTE:
            chan.newnote = (newnote + chan.trans) & 0xFF
            if chan.newcommand != constants.CMD_TONEPORTA:
                iptr = self._instr[chan.instr & 0x3F]
                if not iptr.gatetimer & 0x40:
                    chan.gate = 0xFE
                    if not iptr.gatetimer & 0x80:
                        hr = self.build.adparam
                        self._reg(channel, constants.AD_REG, hr >> 8)
                        self._reg(channel, constants.SR_REG, hr & 0xFF)

    def _write_voice_regs(self, channel: int, chan: _Channel) -> None:
        if chan.mute:
            chan.wave = 0x08
            self._reg(channel, constants.CONTROL_REG, chan.wave)
            return
        for reg, value in (
            (constants.FREQ_LO_REG, chan.freq & 0xFF),
            (constants.FREQ_HI_REG, chan.freq >> 8),
            (constants.PULSE_LO_REG, chan.pulse & 0xFF),
            (constants.PULSE_HI_REG, chan.pulse >> 8),
            (constants.CONTROL_REG, chan.wave & chan.gate),
        ):
            self._reg(channel, reg, value)

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
        if not skip_tickn and (not self.build.optimize_realtime or chan.tick):
            self._tickn_command(chan)
        # Pulse execution, with the editor's default skip optimizations.
        # A mid-frame stop request still finishes the frame, as in the
        # original (PLAY_STOP only becomes PLAY_STOPPED next call).
        fetch_tick = chan.tick == chan.gatetimer
        if not (self.build.optimize_pulse and fetch_tick):
            if chan.pulse_table_ptr:
                if self.build.optimize_pulse and not chan.tick and not chan.pattptr:
                    self._write_voice_regs(channel, chan)
                    return
                self._pulse_exec(chan)
        if fetch_tick:
            self._get_new_notes(channel, chan)
        self._write_voice_regs(channel, chan)


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
