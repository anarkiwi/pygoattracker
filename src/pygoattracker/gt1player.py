"""Native players for GoatTracker V1.x packed tunes.

Transcriptions of the V1.5x standard playroutine and the V1.25 musicroutines over
the packed memory image: channel variables and self-modified operands live at
their packed addresses, and every 8-bit carry and wraparound follows the 6502 code.
"""

from __future__ import annotations

from pysidtracker import MemPlayer
from pysidtracker.registers import PW_HI_REGS

from pygoattracker.errors import GoatTrackerError
from pygoattracker.gt1 import (
    FILTER_STEP0,
    MUSICROUTINE11_SFX,
    MUSICROUTINE11_TIMING,
    ORDERLIST_LOOP_AFTER,
    V15,
    Gt1Song,
)

_SID = 0xD400
_KEYOFF = 0x5E
_NOCMD = 0x60
_PACKEDREST = 0xC0
_ENDPATT = 0xFF
_REPEAT = 0xD0
_TRANSDOWN = 0xE0
_LOOPSONG = 0xFF
_CMD_TONEPORTA = 3
_CMD_SETSUSTAIN = 6
_FIRST_WAVE = 0x09
_SEQ_GUARD = 0x10000
_IO = range(0xD000, 0xE000)

# libsidplayfp's RAM power-on pattern (``SystemRAMBank::reset``).
_STRIPE = bytes((0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00))
_EVEN = _STRIPE * 0x800
_ODD = bytes(b ^ 0xFF for b in _STRIPE) * 0x800
POWERUP_RAM = (_EVEN + _ODD) * 2

# V1.5x channel variable offsets from ``mt_chnwave`` (three 7-byte blocks).
WAVE, WAVEPTR, PACKREST, REPEAT, TRANS, SONGPTR, PATTPTR = range(7)
FREQLO, FREQHI, PULSE, PULSEDIR, GATE = 21, 22, 23, 24, 27
NEWFX, NEWFXPARAM, FX, FXPARAM, NOTE, NEWNOTE, INSTNUM = range(42, 49)
ARPCOUNT, SONGNUM, PATTNUM, TICK, TEMPO = range(63, 68)


class Gt1Player(MemPlayer):
    """Play a decompiled :class:`~pygoattracker.gt1.Gt1Song` one play call per frame.

    Instantiating ``Gt1Player`` picks the transcription of the song's playroutine.
    """

    def __new__(cls, song: Gt1Song, subtune: int = 0):
        if cls is Gt1Player:
            cls = _PLAYERS[song.ref.family]
        return super().__new__(cls)

    def __init__(self, song: Gt1Song, subtune: int = 0):
        address, image = song.pack()
        self._song = song
        self._image_len = len(image)
        self._lab = {name: address + off for name, off in song.ref.labels.items()}
        tables = song.layout()
        self._freqlo = self._lab["mt_freqtbllo"]
        self._freqhi = self._lab["mt_freqtblhi"]
        self._ins = tables["instad"]
        self._wavetbl = tables["wavetbl"]
        self._notetbl = tables["notetbl"]
        self._songlo = tables["songtbllo"]
        self._songhi = tables["songtblhi"]
        self._pattlo = tables["patttbllo"]
        self._patthi = tables["patttblhi"]
        self._setup(tables)
        super().__init__(image, address, subtune)

    def _setup(self, tables: dict) -> None:
        """Resolve the playroutine-specific addresses."""

    def _song_init(self, subtune: int) -> None:
        """Run the packed ``init`` entry for ``subtune``."""

    def snapshot(self) -> list:
        """The SID register file, pulse-width-high registers masked to their nibble."""
        regs = super().snapshot()
        for reg in PW_HI_REGS:
            regs[reg] &= 0x0F
        return regs

    def _sid(self, reg: int, value: int) -> None:
        self._mem[_SID + reg] = value & 0xFF

    def _init(self, subtune: int) -> None:
        mem, start = self._mem, self._load
        image = bytes(mem[start : start + self._image_len])
        io = bytes(mem[_IO.start : _IO.stop])
        mem[:] = POWERUP_RAM
        mem[_IO.start : _IO.stop] = io
        mem[start : start + len(image)] = image
        if not self._song.initialized:
            self._song_init(subtune & 0xFF)


class _Player15(Gt1Player):
    """The V1.5x standard playroutine (``player1.s``)."""

    def _setup(self, tables: dict) -> None:
        lab, song = self._lab, self._song
        self._chn = lab["mt_chnwave"]
        self._chnloop = lab["mt_chnloop"] + 1
        self._init_adc = lab["init"] + 5
        self._filttime = lab["mt_filttime"] + 1
        self._filtstep = lab["mt_filtstep"] + 1
        self._cutoffadd = lab["mt_filtcutoffadd"] + 1
        self._d416 = lab["mt_d416reg"] + 1
        self._d417 = lab["mt_d417reg"] + 1
        self._d418 = lab["mt_d418reg"] + 1
        self._volume = lab["mt_volume"] + 1
        self._timecounter = lab["mt_timecounter"]
        self._funk = lab.get("mt_funktempo", tables["filttbl"] + 2)
        self._filt = tables["filttbl"]
        self._tick_init, self._gatetimer, self._hr_ad, self._hr_sr = song.params
        self._filter_step0 = song.player in FILTER_STEP0
        self._loop_after = song.player in ORDERLIST_LOOP_AFTER
        self._temp1 = self._temp2 = 0

    def _v(self, var: int, x: int) -> int:
        return self._mem[self._chn + var + x]

    def _set(self, var: int, x: int, value: int) -> None:
        self._mem[self._chn + var + x] = value & 0xFF

    def _song_init(self, subtune: int) -> None:
        self._wr(self._init_adc, subtune)
        self._wr(self._chnloop, (subtune << 1) + subtune + (subtune >> 7))

    def _frame(self) -> None:
        mem = self._mem
        if mem[self._filttime]:
            mem[self._filttime] -= 1
            mem[self._d416] = (mem[self._d416] + mem[self._cutoffadd]) & 0xFF
        elif mem[self._filtstep]:
            self._setfilter(mem[self._filtstep])
        self._sid(0x16, mem[self._d416])
        self._sid(0x17, mem[self._d417])
        self._sid(0x18, mem[self._d418] & mem[self._volume])
        for x in (0, 7, 14):
            acc = mem[self._chnloop]
            if acc < 0x80:
                self._channel_init(x, acc)
            else:
                self._channel(x)
        mem[self._chnloop] = 0xFF

    def _channel_init(self, x: int, acc: int) -> None:
        self._set(SONGNUM, x, acc)
        self._mem[self._chnloop] = (acc + 1) & 0xFF
        if not x:
            for var in range(21):
                self._set(var, 0, 0)
            self._sid(0x15, 0)
            self._setfilter(0)
        self._set(TEMPO, x, 5)
        self._set(TICK, x, self._tick_init)
        self._set(PATTPTR, x, _ENDPATT)
        self._set(NEWNOTE, x, 0xFF)
        self._loadregs(x)

    def _channel(self, x: int) -> None:
        tick = (self._v(TICK, x) - 1) & 0xFF
        self._set(TICK, x, tick)
        if not tick:
            self._tick0(x)
            return
        if tick >= 0x80:
            acc = self._v(TEMPO, x)
            if acc < 2:
                self._set(TEMPO, x, acc ^ 1)
                acc = self._rd(self._funk + acc)
            self._set(TICK, x, acc)
        ptr = self._v(WAVEPTR, x)
        if ptr:
            self._waveexec(x, ptr)
            return
        self._temp2 = 0
        fx, param = self._v(FX, x), self._v(FXPARAM, x)
        if fx == 0:
            if param:
                self._arpeggio(x, param)
            else:
                self._pulseexec(x)
        elif fx in (1, 2):
            self._makespeed(param)
            self._freqadd(x) if fx == 1 else self._freqsub(x, 1)
        elif fx == _CMD_TONEPORTA:
            self._tickn_toneporta(x, param)
        elif fx == 4:
            self._vibrato(x, param)
        elif fx < 8:
            self._pulseexec(x)
        else:
            raise GoatTrackerError(f"GT1 effect index {fx} outside the jump table")

    def _tick0(self, x: int) -> None:
        newfx = self._v(NEWFX, x)
        if newfx & 0xF8:
            self._set(INSTNUM, x, newfx & 0xF8)
        fx = newfx & 7
        self._set(FX, x, fx)
        param = self._v(NEWFXPARAM, x)
        self._set(FXPARAM, x, param)
        mem = self._mem
        if fx == 0:
            newnote = self._v(NEWNOTE, x)
            if param and newnote >= 0x80 and not self._v(WAVEPTR, x):
                self._arpeggio(x, newnote)
                return
        elif fx == _CMD_TONEPORTA:
            newnote = self._v(NEWNOTE, x)
            if newnote < 0x80:
                self._set(NOTE, x, newnote)
                self._set(NEWNOTE, x, 0xFF)
        elif fx == 5:
            self._setfilter(param)
        elif fx == _CMD_SETSUSTAIN:
            self._sid(0x06 + x, param)
        elif fx == 7:
            if param < 0x80:
                for chn in (0, 7, 14):
                    self._set(TEMPO, chn, param)
            elif param == 0xEF:
                mem[self._timecounter] = (mem[self._timecounter] + 1) & 0xFF
            elif param > 0xEF:
                mem[self._volume] = param
            else:
                self._set(TEMPO, x, param & 0x7F)
        newnote = self._v(NEWNOTE, x)
        if newnote < 0x80:
            self._newnote(x, newnote)
            return
        ptr = self._v(WAVEPTR, x)
        if ptr:
            self._waveexec(x, ptr)
        else:
            self._pulseexec(x)

    def _newnote(self, x: int, note: int) -> None:
        self._set(NOTE, x, note)
        self._set(NEWNOTE, x, 0xFF)
        self._set(GATE, x, 0xFF)
        ins = self._ins + self._v(INSTNUM, x)
        pulse = self._rd(ins + 2)
        if pulse:
            self._set(PULSEDIR, x, pulse & 0xF0)
            self._sid(0x02 + x, pulse & 0xF0)
            self._set(PULSE, x, pulse & 0x0F)
            self._sid(0x03 + x, pulse & 0x0F)
        self._set(WAVEPTR, x, self._rd(ins + 7))
        self._sid(0x05 + x, self._rd(ins))
        if self._v(FX, x) != _CMD_SETSUSTAIN:
            self._sid(0x06 + x, self._rd(ins + 1))
        self._set(WAVE, x, _FIRST_WAVE)
        self._sid(0x04 + x, _FIRST_WAVE)
        filt = self._rd(ins + 6)
        if filt:
            self._setfilter(filt)

    def _waveexec(self, x: int, ptr: int) -> None:
        wave = self._rd(self._wavetbl + ptr)
        if wave < 8:
            if wave != self._v(ARPCOUNT, x):
                count = (self._v(ARPCOUNT, x) + 1) & 0xFF
                self._set(ARPCOUNT, x, count)
                if count:
                    self._pulseexec(x)
                    return
                self._set(WAVE, x, wave)
        else:
            self._set(WAVE, x, wave)
        note = self._rd(self._notetbl + ptr)
        if note < 0x80:
            note += self._v(NOTE, x)
        note &= 0x7F
        if self._rd(self._wavetbl + ptr + 1) == 0xFF:
            loop = self._rd(self._notetbl + ptr + 1)
            if loop:
                instnum = self._v(INSTNUM, x)
                total = loop + self._rd(self._ins + instnum + 7) + 1
                loop = ((total & 0xFF) + 0xFE + (total >> 8)) & 0xFF
                if not loop:
                    loop = (instnum + 1) & 0xFF
        else:
            loop = (ptr + 1) & 0xFF
        self._set(WAVEPTR, x, loop)
        self._arpfreq_reset(x, note)

    def _arpfreq_reset(self, x: int, note: int) -> None:
        self._set(ARPCOUNT, x, 0)
        self._arpfreq(x, note)

    def _arpfreq(self, x: int, note: int) -> None:
        self._set(FREQLO, x, self._rd(self._freqlo + note))
        self._set(FREQHI, x, self._rd(self._freqhi + note))
        self._pulseexec(x)

    def _arpeggio(self, x: int, acc: int) -> None:
        count = self._v(ARPCOUNT, x)
        step = (count + 1 + (acc >> 7)) & 0xFF
        self._set(ARPCOUNT, x, 0 if step >= 6 else step)
        phase = count >> 1
        param = self._v(FXPARAM, x)
        if phase < 1:
            offset = (param & 0x70) >> 4
        elif phase == 1:
            offset = param & 0x0F
        else:
            offset = 0
        self._arpfreq(x, (offset + self._v(NOTE, x)) & 0xFF)

    def _makespeed(self, param: int) -> None:
        self._temp1 = (param << 2) & 0xFF
        self._temp2 = param >> 6

    def _freqadd(self, x: int, carry: int = 0) -> None:
        low = self._v(FREQLO, x) + self._temp1 + carry
        self._set(FREQLO, x, low)
        self._set(FREQHI, x, self._v(FREQHI, x) + self._temp2 + (low >> 8))
        self._pulseexec(x)

    def _freqsub(self, x: int, carry: int) -> None:
        low = self._v(FREQLO, x) - self._temp1 - (1 - carry)
        self._set(FREQLO, x, low)
        self._set(FREQHI, x, self._v(FREQHI, x) - self._temp2 - (low < 0))
        self._pulseexec(x)

    def _tickn_toneporta(self, x: int, param: int) -> None:
        note = self._v(NOTE, x)
        if param:
            self._makespeed(param)
            low = self._rd(self._freqlo + note) - self._v(FREQLO, x)
            high = self._rd(self._freqhi + note) - self._v(FREQHI, x) - (low < 0)
            carry, low, high = int(high >= 0), low & 0xFF, high & 0xFF
            if high < 0x80:
                if self._temp2 != high:
                    reached = self._temp2 >= high
                else:
                    reached = self._temp1 >= low
                if not reached:
                    self._freqadd(x)
                    return
            elif (high + self._temp2 + ((low + self._temp1 + carry) >> 8)) & 0x80:
                self._freqsub(x, 1)
                return
        self._set(FXPARAM, x, 0)
        self._arpfreq_reset(x, note)

    def _vibrato(self, x: int, param: int) -> None:
        self._temp1 = param & 0xF0
        limit = param & 0x0F
        count = self._v(ARPCOUNT, x)
        if count < 0x80 and count > limit:
            count ^= 0xFF
        count = (count + 2) & 0xFF
        self._set(ARPCOUNT, x, count)
        if count & 1:
            self._freqsub(x, 1)
        else:
            self._freqadd(x)

    def _pulseexec(self, x: int) -> None:
        if self._v(TICK, x) == self._gatetimer:
            self._getnewnotes(x)
            return
        if self._v(PATTPTR, x) == _ENDPATT:
            self._sequencer(x)
            return
        ins = self._ins + self._v(INSTNUM, x)
        speed = self._rd(ins + 3) & 0xFE
        if speed:
            direction = self._v(PULSEDIR, x)
            low = direction & 0xFE
            if direction & 1:
                low -= speed
                pulse = self._v(PULSE, x) - (low < 0)
                self._set(PULSE, x, pulse)
                flip = (pulse & 0xFF) >= self._rd(ins + 4)
            else:
                low += speed
                pulse = self._v(PULSE, x) + (low >> 8)
                self._set(PULSE, x, pulse)
                flip = (pulse & 0xFF) >= self._rd(ins + 5)
            self._sid(0x03 + x, pulse)
            low = (low & 0xFF) + flip
            self._set(PULSEDIR, x, low)
            self._sid(0x02 + x, low)
        self._loadregs(x)

    def _getnewnotes(self, x: int) -> None:
        patt = self._v(PATTNUM, x)
        base = self._rd(self._pattlo + patt) | self._rd(self._patthi + patt) << 8
        pos = self._v(PATTPTR, x)
        acc = self._rd(base + pos)
        pos = (pos + 1) & 0xFF
        if acc >= _PACKEDREST:
            if not self._v(PACKREST, x):
                self._set(PACKREST, x, acc)
            count = (self._v(PACKREST, x) + 1) & 0xFF
            self._set(PACKREST, x, count)
            if not count:
                self._set(PATTPTR, x, self._v(PATTPTR, x) + 1)
            self._loadregs(x)
            return
        if acc < _NOCMD:
            note = acc
            self._set(NEWFX, x, self._rd(base + pos))
            pos = (pos + 1) & 0xFF
            self._set(NEWFXPARAM, x, self._rd(base + pos))
            pos = (pos + 1) & 0xFF
        else:
            note = acc - _NOCMD
        self._set(PATTPTR, x, _ENDPATT if self._rd(base + pos) == _ENDPATT else pos)
        if note == _KEYOFF:
            self._set(GATE, x, 0xFE)
        elif note < _KEYOFF:
            self._set(NEWNOTE, x, note + self._v(TRANS, x))
            if self._v(NEWFX, x) & 7 != _CMD_TONEPORTA:
                if not self._rd(self._ins + self._v(INSTNUM, x) + 3) & 1:
                    self._sid(0x05 + x, self._hr_ad)
                    self._sid(0x06 + x, self._hr_sr)
                self._set(GATE, x, 0xFE)
        self._loadregs(x)

    def _sequencer(self, x: int) -> None:
        song = self._v(SONGNUM, x)
        base = self._rd(self._songlo + song) | self._rd(self._songhi + song) << 8
        if self._v(REPEAT, x):
            self._set(REPEAT, x, self._v(REPEAT, x) - 1)
        else:
            pos = self._v(SONGPTR, x)
            for _ in range(_SEQ_GUARD):
                acc = self._rd(base + pos)
                pos = (pos + 1) & 0xFF
                if acc < _REPEAT:
                    break
                if acc < _TRANSDOWN:
                    self._set(REPEAT, x, acc - _REPEAT)
                elif self._loop_after:
                    self._set(TRANS, x, acc - 0xEF)
                elif acc == _LOOPSONG:
                    pos = self._rd(base + pos)
                else:
                    self._set(TRANS, x, acc - 0xF0)
            else:
                raise GoatTrackerError("GT1 orderlist never reaches a pattern")
            self._set(PATTNUM, x, acc)
            if self._loop_after and self._rd(base + pos) == _LOOPSONG:
                pos = self._rd(base + ((pos + 1) & 0xFF))
            self._set(SONGPTR, x, pos)
        self._set(PATTPTR, x, self._v(PATTPTR, x) + 1)
        self._loadregs(x)

    def _setfilter(self, step: int) -> None:
        mem = self._mem
        ctrl = self._rd(self._filt + step)
        if ctrl:
            mem[self._d417] = ctrl
            mem[self._d418] = self._rd(self._filt + step + 1)
            cutoff = self._rd(self._filt + step + 2)
            if cutoff:
                mem[self._d416] = cutoff
            time = 0
        else:
            mem[self._cutoffadd] = self._rd(self._filt + step + 2)
            time = self._rd(self._filt + step + 1)
        mem[self._filttime] = time
        if step or self._filter_step0:
            mem[self._filtstep] = self._rd(self._filt + step + 3)
        else:
            mem[self._filtstep] = 0

    def _loadregs(self, x: int) -> None:
        self._sid(0x00 + x, self._v(FREQLO, x))
        self._sid(0x01 + x, self._v(FREQHI, x))
        self._sid(0x04 + x, self._v(WAVE, x) & self._v(GATE, x))


# Musicroutine 11.x channel blocks: ``mt_chnnote``, ``mt_chnwave``, ``mt_chnsongadrlo``.
M_NOTE, M_FREQLO, M_FREQHI, M_NEWNOTE, M_COMMAND, M_CMDDATA, M_INSTNUM = range(7)
M_WAVE, M_WAVETBL, M_PULSE, M_PULSEDIR, M_ARPCOUNT, M_VIBCOUNT, M_SONGPTR = range(7)
M_SONGLO, M_SONGHI, M_PATTPTR, M_PATTNUM, M_TICK, M_TEMPO, M_NEXT = range(7)
_M_TIMINGMARK = 0xC0
_M_CHANNEL_GUARD = 0x100
_M_SFX_INIT_STEPS = 3


class _Player11(Gt1Player):
    """The V1.25 musicroutines 11.1..11.5 (``PLAYER1.S``..``PLAYER5.S``)."""

    def _setup(self, tables: dict) -> None:
        lab, name = self._lab, self._song.player
        self._note = lab["mt_chnnote"]
        self._wave = lab["mt_chnwave"]
        self._song_blk = lab["mt_chnsongadrlo"]
        self._playflag = lab["playflag"] + 1
        self._cutoff = lab["mt_filtcutoff"] + 1
        self._cutoffadd = lab["mt_filtcutoffadd"] + 1
        self._ctrl = lab["mt_filtctrl"] + 1
        self._type = lab["mt_filttype"] + 1
        self._volume = lab["mt_volume"] + 1
        self._hrtimer = lab.get("mt_hrtimer")
        self._sfx = name in MUSICROUTINE11_SFX
        self._timing = name in MUSICROUTINE11_TIMING

    def _a(self, var: int, x: int) -> int:
        return self._mem[self._note + var + x]

    def _seta(self, var: int, x: int, value: int) -> None:
        self._mem[self._note + var + x] = value & 0xFF

    def _b(self, var: int, x: int) -> int:
        return self._mem[self._wave + var + x]

    def _setb(self, var: int, x: int, value: int) -> None:
        self._mem[self._wave + var + x] = value & 0xFF

    def _c(self, var: int, x: int) -> int:
        return self._mem[self._song_blk + var + x]

    def _setc(self, var: int, x: int, value: int) -> None:
        self._mem[self._song_blk + var + x] = value & 0xFF

    def _song_init(self, subtune: int) -> None:
        self._wr(self._playflag, subtune)

    def _frame(self) -> None:
        mem = self._mem
        if mem[self._playflag] < 0x80:
            self._start()
            return
        cutoff = (mem[self._cutoff] + mem[self._cutoffadd]) & 0xFF
        mem[self._cutoff] = cutoff
        self._sid(0x16, cutoff)
        self._sid(0x17, mem[self._ctrl])
        self._sid(0x18, mem[self._type] | mem[self._volume])
        x = 0
        for _ in range(_M_CHANNEL_GUARD):
            self._channel(x)
            x = self._c(M_NEXT, x)
            if not x:
                return
        raise GoatTrackerError("GT1 channel chain does not end")

    def _start(self) -> None:
        song = self._mem[self._playflag]
        pos = ((song << 1) + song + (song >> 7)) & 0xFF
        x = 0
        for _ in range(_M_CHANNEL_GUARD):
            self._setc(M_SONGLO, x, self._rd(self._songlo + pos))
            self._setc(M_SONGHI, x, self._rd(self._songhi + pos))
            pos = (pos + 1) & 0xFF
            self._setb(M_SONGPTR, x, 0)
            self._setb(M_WAVETBL, x, 0)
            self._setb(M_PULSEDIR, x, 0)
            self._sid(0x04 + x, 0)
            self._setc(M_TICK, x, 5)
            self._setc(M_TEMPO, x, 5)
            self._seta(M_NEWNOTE, x, 5)
            self._setc(M_PATTPTR, x, _ENDPATT)
            self._mem[self._playflag] = 0xFF
            x = self._c(M_NEXT, x)
            if not x:
                break
        else:
            raise GoatTrackerError("GT1 channel chain does not end")
        self._sid(0x15, 0)
        self._mem[self._ctrl] = 0
        self._mem[self._cutoffadd] = 0

    def _channel(self, x: int) -> None:
        tick = self._c(M_TICK, x)
        if not tick:
            self._newnotes(x)
            return
        if tick >= 0x80:
            tick = self._c(M_TEMPO, x)
        self._setc(M_TICK, x, tick - 1)
        if self._c(M_PATTPTR, x) == _ENDPATT:
            self._nextpatt(x)
        else:
            self._effects(x, 0)

    def _nextpatt(self, x: int) -> None:
        base = self._c(M_SONGLO, x) | self._c(M_SONGHI, x) << 8
        pos = self._b(M_SONGPTR, x)
        acc = self._rd(base + pos)
        if self._sfx:
            if acc == _LOOPSONG:
                self._setb(M_SONGPTR, x, self._rd(base + ((pos + 1) & 0xFF)))
            else:
                self._setc(M_PATTNUM, x, acc)
                self._setc(M_PATTPTR, x, self._c(M_PATTPTR, x) + 1)
                self._setb(M_SONGPTR, x, pos + 1)
            self._effects(x, 1)
            return
        self._setc(M_PATTPTR, x, self._c(M_PATTPTR, x) + 1)
        if acc == _LOOPSONG:
            pos = self._rd(base + ((pos + 1) & 0xFF))
            acc = self._rd(base + pos)
        self._setc(M_PATTNUM, x, acc)
        self._setb(M_SONGPTR, x, pos + 1)
        if self._a(M_NEWNOTE, x):
            self._pulseok2(x)
        else:
            self._newnoteinit(x)

    def _effects(self, x: int, carry: int) -> None:
        ins = self._a(M_INSTNUM, x)
        if not self._a(M_NEWNOTE, x):
            self._newnoteinit(x)
            return
        if self._sfx:
            if self._a(M_NOTE, x) >= 0x80:
                self._dosfx(x)
                return
            if carry:
                self._pulseok2(x)
                return
        direction = self._b(M_PULSEDIR, x)
        if direction >= 0x80:
            self._setb(M_PULSEDIR, x, direction & 0x7F)
            first = self._ins + 7 + ins
            wave = self._rd(self._wavetbl + self._rd(first))
            self._setb(M_WAVE, x, wave)
            self._sid(0x04 + x, wave)
            self._sid(0x05 + x, self._rd(self._ins + ins))
            self._sid(0x06 + x, self._rd(self._ins + 1 + ins))
            ins = self._rd(first)
            if ins:
                self._skipwave(x, ins)
                return
            direction = 0
        pulse = self._b(M_PULSE, x)
        speed = self._rd(self._ins + 3 + ins)
        if direction & 1:
            pulse -= speed
            pulse = (pulse & 0xFF) - (pulse < 0)
            flip = (pulse & 0x0F) < self._rd(self._ins + 4 + ins)
        else:
            pulse += speed
            pulse = (pulse & 0xFF) + (pulse >> 8)
            flip = (pulse & 0x0F) >= self._rd(self._ins + 5 + ins)
        self._setb(M_PULSE, x, pulse)
        if not self._sfx:
            self._sid(0x02 + x, pulse)
            self._sid(0x03 + x, pulse)
        if flip:
            self._setb(M_PULSEDIR, x, 0 if direction & 1 else 1)
        self._pulseok2(x)

    def _pulseok2(self, x: int) -> None:
        ptr = self._b(M_WAVETBL, x)
        if ptr:
            wave = self._rd(self._wavetbl + ptr)
            if wave:
                self._setb(M_WAVE, x, wave)
                self._sid(0x04 + x, wave)
            self._skipwave(x, ptr)
            return
        command = self._a(M_COMMAND, x)
        param = self._a(M_CMDDATA, x)
        if command == 0:
            self._arpeggio(x)
        elif command == 1:
            self._freqstep(x, (param << 1) & 0xFF, param >> 7)
        elif command == _CMD_TONEPORTA:
            self._toneporta(x, param)
        elif command == 4:
            self._vibrato(x, param)
        elif command < 8:
            self._loadpulse(x)
        else:
            raise GoatTrackerError(f"GT1 effect index {command} outside the jump table")

    def _skipwave(self, x: int, ptr: int) -> None:
        note = self._rd(self._notetbl + ptr)
        if note < 0x80:
            note += self._a(M_NOTE, x)
        note &= 0x7F
        if self._rd(self._wavetbl + ptr + 1) == 0xFF:
            loop = self._rd(self._notetbl + ptr + 1)
            if loop:
                instnum = self._a(M_INSTNUM, x)
                total = loop + self._rd(self._ins + 7 + instnum) + 1
                loop = ((total & 0xFF) + 0xFE + (total >> 8)) & 0xFF
                if not loop:
                    loop = (instnum + 1) & 0xFF
        else:
            loop = (ptr + 1) & 0xFF
        self._setb(M_WAVETBL, x, loop)
        self._arpfreq(x, note)

    def _arpeggio(self, x: int) -> None:
        param = self._a(M_CMDDATA, x)
        if not param or (param >= 0x80 and self._c(M_TICK, x) & 1):
            self._loadpulse(x)
            return
        count, note = self._b(M_ARPCOUNT, x), self._a(M_NOTE, x)
        if count >= 0x80:
            note, count = ((param & 0x70) >> 4) + note, 1
        elif count:
            note, count = (param & 0x0F) + note, 0
        else:
            count = 0xFF
        self._setb(M_ARPCOUNT, x, count)
        self._arpfreq(x, note & 0xFF)

    def _arpfreq(self, x: int, note: int) -> None:
        low, high = self._rd(self._freqlo + note), self._rd(self._freqhi + note)
        self._seta(M_FREQLO, x, low)
        self._sid(0x00 + x, low)
        self._seta(M_FREQHI, x, high)
        self._sid(0x01 + x, high)
        self._loadpulse(x)

    def _loadpulse(self, x: int) -> None:
        if self._sfx:
            self._sid(0x02 + x, self._b(M_PULSE, x))
            self._sid(0x03 + x, self._b(M_PULSE, x))

    def _freqstep(self, x: int, speed: int, down: int) -> None:
        low, high = self._a(M_FREQLO, x), self._a(M_FREQHI, x)
        self._sid(0x00 + x, low)
        self._sid(0x01 + x, high)
        low = low - speed if down else low + speed
        self._seta(M_FREQLO, x, low)
        self._seta(M_FREQHI, x, high - (low < 0) if down else high + (low >> 8))
        self._loadpulse(x)

    def _toneporta(self, x: int, param: int) -> None:
        note = self._a(M_NOTE, x)
        speed, down = (param << 1) & 0xFF, param >> 7
        here = self._a(M_FREQHI, x) << 8 | self._a(M_FREQLO, x)
        there = self._rd(self._freqhi + note) << 8 | self._rd(self._freqlo + note)
        if (here > there) if down else (here < there):
            self._freqstep(x, speed, down)
        else:
            self._arpfreq(x, note)

    def _vibrato(self, x: int, param: int) -> None:
        count = self._b(M_VIBCOUNT, x)
        if count < 0x80 and count >= param & 0x0E:
            count ^= 0xFF
        else:
            count = (count + 2) & 0xFF
        self._setb(M_VIBCOUNT, x, count)
        self._freqstep(x, param, count & 1)

    def _newnotes(self, x: int) -> None:
        self._setc(M_TICK, x, 0xFF)
        patt = self._c(M_PATTNUM, x)
        base = self._rd(self._pattlo + patt) | self._rd(self._patthi + patt) << 8
        pos = self._c(M_PATTPTR, x)
        acc = self._rd(base + pos)
        pos = (pos + 1) & 0xFF
        if acc >= _PACKEDREST:
            count = (self._a(M_NEWNOTE, x) + 1) & 0xFF
            self._seta(M_NEWNOTE, x, count)
            if count < 0x80:
                self._seta(M_NEWNOTE, x, acc)
            elif count == 0xFF:
                self._setc(M_PATTPTR, x, pos)
                self._seta(M_NEWNOTE, x, 1)
            command = self._a(M_COMMAND, x)
            if command >= 0x80:
                raise GoatTrackerError("GT1 command byte has bit 7 set")
            self._tick0(x, command)
            return
        if acc < _NOCMD:
            note = acc
            fx = self._rd(base + pos)
            if fx & 0xF8:
                self._seta(M_INSTNUM, x, fx & 0xF8)
            self._seta(M_COMMAND, x, fx & 7)
            pos = (pos + 1) & 0xFF
            self._seta(M_CMDDATA, x, self._rd(base + pos))
            pos = (pos + 1) & 0xFF
        else:
            note = acc - _NOCMD
        self._setc(M_PATTPTR, x, _ENDPATT if self._rd(base + pos) == _ENDPATT else pos)
        command = self._a(M_COMMAND, x)
        if not (self._sfx and self._a(M_NOTE, x) >= 0x80) and note <= _KEYOFF:
            if note < _KEYOFF:
                self._seta(M_NOTE, x, note)
                if command == _CMD_TONEPORTA:
                    self._tick0(x, command)
                    return
                if self._hrtimer is not None:
                    self._setb(M_ARPCOUNT, x, self._mem[self._hrtimer])
                self._seta(M_NEWNOTE, x, 0)
                self._sid(0x05 + x, 0)
                self._sid(0x06 + x, 0)
            self._sid(0x04 + x, self._b(M_WAVE, x) & 0xFE)
        self._tick0(x, command)

    def _tick0(self, x: int, command: int) -> None:
        param = self._a(M_CMDDATA, x)
        mem = self._mem
        if command == 2:
            mem[self._cutoffadd] = param
        elif command == _CMD_TONEPORTA:
            self._setb(M_VIBCOUNT, x, 0xFE)
        elif command == 5:
            mem[self._ctrl] = param
        elif command == _CMD_SETSUSTAIN:
            if not self._sfx:
                self._sid(0x06 + x, param)
            elif self._a(M_NOTE, x) < 0x80:
                self._sid(0x06 + x, param)
                return
        elif command == 7:
            if param < 0x80:
                for chn in (0, 7, 14):
                    self._setc(M_TEMPO, chn, param)
            elif not (self._timing and param >= _M_TIMINGMARK):
                self._setc(M_TEMPO, x, param & 0x7F)
        if self._sfx and self._a(M_NOTE, x) >= 0x80:
            self._dosfx(x)

    def _newnoteinit(self, x: int) -> None:
        if self._hrtimer is not None and self._b(M_ARPCOUNT, x):
            self._setb(M_ARPCOUNT, x, self._b(M_ARPCOUNT, x) - 1)
            return
        self._seta(M_NEWNOTE, x, 1)
        self._setb(M_ARPCOUNT, x, 0xFE)
        self._setb(M_VIBCOUNT, x, 0xFE)
        ins = self._a(M_INSTNUM, x)
        filt = self._rd(self._ins + 6 + ins)
        if filt:
            self._mem[self._cutoff] = filt
            self._mem[self._type] = (filt << 4) & 0xFF
        pulse = self._rd(self._ins + 2 + ins)
        if pulse:
            self._setb(M_PULSE, x, pulse)
            self._sid(0x02 + x, pulse)
            self._sid(0x03 + x, pulse)
            self._setb(M_PULSEDIR, x, 0x80)
        else:
            self._setb(M_PULSEDIR, x, self._b(M_PULSEDIR, x) | 0x80)

    def _dosfx(self, x: int) -> None:
        step = self._a(M_NOTE, x) & 0x7F
        base = self._a(M_FREQLO, x) | self._a(M_FREQHI, x) << 8
        if step < _M_SFX_INIT_STEPS:
            self._seta(M_NOTE, x, self._a(M_NOTE, x) + 1)
            self._setb(M_PULSEDIR, x, 0)
            self._setb(M_WAVETBL, x, 0)
            self._setb(M_WAVE, x, 0)
            self._sfx_hardrestart(x)
            return
        delay = self._b(M_WAVE, x)
        if delay:
            step = (step + 1) & 0xFF
            delay = (delay + 1) & 0xFF
            self._setb(M_WAVE, x, delay)
            if delay:
                return
        acc = self._rd(base + step)
        if not acc:
            self._seta(M_NOTE, x, 0)
            self._sfx_hardrestart(x)
            return
        if acc >= 0xE0:
            self._setb(M_WAVE, x, acc)
            return
        note = acc & 0x7F
        self._sid(0x00 + x, self._rd(self._freqlo + note))
        self._sid(0x01 + x, self._rd(self._freqhi + note))
        step = (step + 1) & 0xFF
        wave = self._rd(base + step)
        if wave and wave < 0x82:
            self._sid(0x04 + x, wave)
            step = (step + 1) & 0xFF
        self._seta(M_NOTE, x, step | 0x80)
        self._sid(0x05 + x, self._rd(base))
        self._sid(0x06 + x, self._rd(base + 1))
        pulse = self._rd(base + 2)
        self._sid(0x02 + x, pulse)
        self._sid(0x03 + x, pulse)

    def _sfx_hardrestart(self, x: int) -> None:
        for reg in (0x04, 0x05, 0x06):
            self._sid(reg + x, 0)


_PLAYERS = {V15: _Player15, "11": _Player11}
