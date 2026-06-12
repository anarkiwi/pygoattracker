"""Playroutine behavior, frame by frame.

Frame numbers: frame 0 is the player's init frame; with the default
tempo 6 the first pattern row initializes on frame 5 and its wavetable
starts executing on frame 6. Notes are fetched (and gateoff / hard
restart applied) when the tick counter equals the gateoff timer, two
frames before the row with the default test instrument. Pattern row N
of the first pattern is fetched on frame 3 + 6N and initialized on
frame 5 + 6N.
"""

import pytest

from pygoattracker import constants
from pygoattracker.errors import GoatTrackerError
from pygoattracker.model import (
    Instrument,
    Orderlist,
    Pattern,
    PlayPattern,
    Repeat,
    Row,
    Subtune,
    Transpose,
)
from pygoattracker.player import Player, iter_frames

from tests.conftest import add_test_instrument, basic_song, note

C4_FREQ = constants.FREQ_TABLE[48]


def play(player: Player, frames: int):
    """Run ``frames`` frames; return each frame's writes."""
    return [player.play_frame() for _ in range(frames)]


def reg_history(song, frames: int, reg: int, **kwargs):
    """Values of one register after each frame."""
    player = Player(song, **kwargs)
    history = []
    for _ in range(frames):
        player.play_frame()
        history.append(player.regs[reg])
    return history


def freq_history(song, frames: int, voice: int = 0, **kwargs):
    """16-bit voice frequency after each frame."""
    player = Player(song, **kwargs)
    history = []
    for _ in range(frames):
        player.play_frame()
        base = voice * constants.VOICE_REG_SIZE
        history.append((player.regs[base + 1] << 8) | player.regs[base])
    return history


def init_frames(song, frames: int = 20):
    """Frames on which a note init wrote the $09 first frame waveform."""
    history = reg_history(song, frames, constants.CONTROL_REG)
    return [num for num, value in enumerate(history) if value == 0x09]


def test_init_frame_writes_all_registers(song):
    frames = play(Player(song), 2)
    assert frames[0] == [(reg, 0) for reg in range(constants.SID_REGISTERS)]
    # Master volume appears with the first played frame.
    assert (constants.MODE_VOL_REG, 0x0F) in frames[1]


def test_single_note_timeline(song):
    frames = play(Player(song), 60)
    # Note fetch two frames ahead: gate masked off and hard restart ADSR.
    assert (constants.AD_REG, 0x0F) in frames[3]
    # Row 0 init: instrument ADSR and first frame waveform $09 (test+gate).
    assert (constants.AD_REG, 0x09) in frames[5]
    assert (constants.CONTROL_REG, 0x09) in frames[5]
    # First wavetable step: real waveform and the note's frequency.
    assert (constants.CONTROL_REG, 0x41) in frames[6]
    assert (constants.FREQ_LO_REG, C4_FREQ & 0xFF) in frames[6]
    assert (constants.FREQ_HI_REG, C4_FREQ >> 8) in frames[6]
    # Wavetable stopped; nothing changes while the rest rows play.
    assert frames[7] == []
    assert frames[8] == []
    # The 8-row pattern loops: next fetch at frame 51, next init at 53.
    assert (constants.CONTROL_REG, 0x40) in frames[51]
    assert (constants.AD_REG, 0x0F) in frames[51]
    assert (constants.CONTROL_REG, 0x09) in frames[53]


def test_keyoff_keyon(song):
    song.patterns[0].rows[4] = Row(note=constants.KEYOFF)
    song.patterns[0].rows[5] = Row(note=constants.KEYON)
    frames = play(Player(song), 36)
    # Keyoff/keyon are applied at their fetch frames (3 + 6N).
    assert (constants.CONTROL_REG, 0x40) in frames[27]
    assert (constants.CONTROL_REG, 0x41) in frames[33]


def test_set_tempo():
    song = basic_song(
        rows={
            row: Row(note=note("C-4"), instrument=1, command=0xF, data=4)
            for row in range(4)
        }
    )
    # Tempo 4 from row 0 onwards: row inits every 4 frames.
    assert init_frames(song) == [5, 9, 13, 17]


def test_per_channel_tempo():
    song = basic_song(
        rows={0: Row(note=note("C-4"), instrument=1, command=0xF, data=0x84)}
    )
    # Other channels play an empty pattern, away from the F command.
    song.patterns.append(Pattern.empty(8))
    song.subtunes[0].channels[1].entries = [PlayPattern(1)]
    song.subtunes[0].channels[2].entries = [PlayPattern(1)]
    player = Player(song)
    play(player, 8)
    channels = player._channels
    assert channels[0].tempo == 3
    assert channels[1].tempo == 5
    assert channels[2].tempo == 5


def test_funktempo():
    rows = {row: Row(note=note("C-4"), instrument=1) for row in range(1, 8)}
    rows[0] = Row(note=note("C-4"), instrument=1, command=0xE, data=1)
    song = basic_song(rows=rows)
    song.speedtable.add(9, 6)
    # Rows alternate between 9 and 6 frames once funktempo kicks in.
    assert init_frames(song, 40)[:4] == [5, 14, 20, 29]


def test_funktempo_recall():
    rows = {
        0: Row(note=note("C-4"), instrument=1, command=0xE, data=1),
        2: Row(note=note("C-4"), instrument=1, command=0xF, data=4),
        4: Row(note=note("C-4"), instrument=1, command=0xF, data=0),
    }
    song = basic_song(rows=rows)
    song.speedtable.add(9, 6)
    player = Player(song)
    play(player, 40)
    assert player.playing


def test_master_volume(song):
    song.patterns[0].rows[1] = Row(command=0xD, data=0x0A)
    history = reg_history(song, 16, constants.MODE_VOL_REG)
    # Row 1 runs its command on frame 11; the register follows on 12.
    assert history[11] == 0x0F
    assert history[12] == 0x0A


def test_timing_mark_is_not_volume(song):
    song.patterns[0].rows[1] = Row(command=0xD, data=0x3A)
    history = reg_history(song, 16, constants.MODE_VOL_REG)
    assert history[12] == 0x0F


def test_vibrato(song):
    idx = song.speedtable.add(3, 0x40)
    for row in range(1, 8):
        song.patterns[0].rows[row] = Row(command=0x4, data=idx)
    history = freq_history(song, 45)
    active = history[12:]
    assert max(active) > C4_FREQ
    assert min(active) < C4_FREQ
    assert max(active) <= C4_FREQ + 0x180
    assert min(active) >= C4_FREQ - 0x180


def test_instrument_vibrato(song):
    idx = song.speedtable.add(3, 0x40)
    song.instruments[0].vibrato_param = idx
    song.instruments[0].vibrato_delay = 10
    history = freq_history(song, 40)
    # Frequency is steady until the vibrato delay has elapsed.
    assert set(history[6:15]) == {C4_FREQ}
    wobble = history[16:]
    assert max(wobble) > C4_FREQ
    assert min(wobble) < C4_FREQ


def test_portamento_up(song):
    idx = song.speedtable.add(0x01, 0x00)
    for row in range(1, 4):
        song.patterns[0].rows[row] = Row(command=0x1, data=idx)
    history = freq_history(song, 32)
    assert history[6] == C4_FREQ
    # Portamento runs on every tick but tick 0: 5 ticks on each of the
    # 3 command rows, $0100 each.
    assert history[29] == C4_FREQ + 15 * 0x100
    assert history == sorted(history)


def test_portamento_down_realtime_speed(song):
    # High bit set in the speedtable: note-independent speed, divisor 4.
    idx = song.speedtable.add(0x80, 0x02)
    for row in range(1, 4):
        song.patterns[0].rows[row] = Row(command=0x2, data=idx)
    step = (constants.FREQ_TABLE[49] - constants.FREQ_TABLE[48]) >> 2
    history = freq_history(song, 32)
    assert history[29] == C4_FREQ - 15 * step


def test_toneportamento(song):
    idx = song.speedtable.add(0x02, 0x00)
    song.patterns[0].rows[2] = Row(note=note("E-4"), command=0x3, data=idx)
    target = constants.FREQ_TABLE[52]
    history = freq_history(song, 40)
    assert history[6] == C4_FREQ
    # Slides up from row 2 and clamps exactly at the E-4 frequency.
    assert C4_FREQ < history[18] <= target
    assert history[30] == target
    assert history[35] == target


def test_tie_note(song):
    song.patterns[0].rows[2] = Row(note=note("E-4"), command=0x3, data=0)
    history = freq_history(song, 30)
    assert history[6] == C4_FREQ
    # Speed $00 toneportamento jumps straight to the target note.
    assert history[18] == constants.FREQ_TABLE[52]
    assert history[25] == constants.FREQ_TABLE[52]


def test_pulse_program(song):
    song.pulsetable.left = [0x88, 0x10, 0xFF]
    song.pulsetable.right = [0x00, 0x40, 0x00]
    song.instruments[0].pulse_ptr = 1
    player = Player(song)
    frames = play(player, 8)
    # Pulse $800 set on the first frame after note init.
    assert (constants.PULSE_HI_REG, 0x08) in frames[6]
    history = []
    for _ in range(30):
        player.play_frame()
        history.append((player.regs[3] << 8) | player.regs[2])
    # Sixteen modulation steps of $40 land on $C00 and stay.
    assert history[0] >= 0x800
    assert history[-1] == 0xC00
    assert history == sorted(history)


def test_filter_program():
    # A long pattern so the looping song does not retrigger the note
    # (and with it the filter program) during the measurement.
    song = basic_song(length=128)
    song.filtertable.left = [0x90, 0x00, 0x7F, 0xFF]
    song.filtertable.right = [0xF1, 0x40, 0x01, 0x00]
    song.instruments[0].filter_ptr = 1
    player = Player(song)
    frames = play(player, 10)
    # Filter parameter and cutoff set steps execute on the same frame.
    assert (constants.FC_HI_REG, 0x40) in frames[6]
    assert (constants.RES_FILT_REG, 0xF1) in frames[6]
    assert (constants.MODE_VOL_REG, 0x1F) in frames[6]
    for _ in range(0x7F):
        player.play_frame()
    # Modulation added $01 for $7F frames, then the table jump stopped.
    assert player.regs[constants.FC_HI_REG] == 0x40 + 0x7F
    play(player, 5)
    assert player.regs[constants.FC_HI_REG] == 0x40 + 0x7F


def test_filter_command(song):
    song.patterns[0].rows[1] = Row(command=0xB, data=0xF7)
    song.patterns[0].rows[2] = Row(command=0xC, data=0x80)
    frames = play(Player(song), 25)
    assert (constants.RES_FILT_REG, 0xF7) in frames[12]
    assert (constants.FC_HI_REG, 0x80) in frames[18]


def test_set_ad_sr_wave_commands(song):
    song.patterns[0].rows[1] = Row(command=0x5, data=0x33)
    song.patterns[0].rows[2] = Row(command=0x6, data=0x44)
    song.patterns[0].rows[3] = Row(command=0x7, data=0x21)
    frames = play(Player(song), 30)
    assert (constants.AD_REG, 0x33) in frames[11]
    assert (constants.SR_REG, 0x44) in frames[17]
    assert (constants.CONTROL_REG, 0x21) in frames[23]


def test_wavetable_pointer_command(song):
    # A second wavetable program: triangle waveform.
    ptr = song.wavetable.add(0x11, 0x00)
    song.wavetable.add(constants.TABLEJUMP, 0x00)
    song.patterns[0].rows[2] = Row(command=0x8, data=ptr)
    frames = play(Player(song), 25)
    # The new wavetable step executes on the command row's own tick 0.
    assert (constants.CONTROL_REG, 0x11) in frames[17]


def test_wavetable_command_execution(song):
    song.wavetable.left = [0x41, 0xF6, 0xFF]
    song.wavetable.right = [0x00, 0x2A, 0x00]
    frames = play(Player(song), 10)
    assert (constants.CONTROL_REG, 0x41) in frames[6]
    # Step 2 executes pattern command 6XY (set sustain/release).
    assert (constants.SR_REG, 0x2A) in frames[7]


def test_wavetable_arpeggio(song):
    song.wavetable.left = [0x41, 0x00, 0x00, 0xFF]
    song.wavetable.right = [0x00, 0x04, 0x07, 0x02]
    history = freq_history(song, 12)
    assert history[6] == constants.FREQ_TABLE[48]
    assert history[7] == constants.FREQ_TABLE[52]
    assert history[8] == constants.FREQ_TABLE[55]
    # The jump loops steps 2-3.
    assert history[9] == constants.FREQ_TABLE[52]


def test_wavetable_absolute_note_and_delay(song):
    song.wavetable.left = [0x81, 0x02, 0x41, 0xFF]
    song.wavetable.right = [0x80 + 60, 0x80, 0x00, 0x00]
    history = freq_history(song, 14)
    # Absolute C-5 with noise, regardless of the pattern note.
    assert history[6] == constants.FREQ_TABLE[60]
    # Two delay frames, one keep-frequency step, then the note's pitch.
    assert history[7] == constants.FREQ_TABLE[60]
    assert history[8] == constants.FREQ_TABLE[60]
    assert history[9] == constants.FREQ_TABLE[60]
    assert history[10] == constants.FREQ_TABLE[48]


def test_illegal_wavetable_command_stops(song):
    song.wavetable.left = [0x41, 0xFE, 0xFF]
    song.wavetable.right = [0x00, 0x00, 0x00]
    player = Player(song)
    play(player, 8)
    assert not player.playing
    assert player.play_frame() == []


def test_gatetimer_legato_bits(song):
    legato = add_test_instrument(
        song, gateoff_timer=0x42, first_wave=0, attack_decay=0x55
    )
    song.patterns[0].rows[4] = Row(note=note("E-4"), instrument=legato)
    player = Player(song)
    frames = play(player, 36)
    late = frames[20:]
    # No gateoff and no hard restart before the legato note...
    assert not any((constants.CONTROL_REG, 0x40) in writes for writes in late)
    assert not any((constants.AD_REG, 0x0F) in writes for writes in late)
    # ...but its ADSR and wavetable still apply (gate stays on).
    assert (constants.AD_REG, 0x55) in frames[29]
    freq = (player.regs[1] << 8) | player.regs[0]
    assert freq == constants.FREQ_TABLE[52]
    assert player.regs[constants.CONTROL_REG] == 0x41


def test_hard_restart_disabled_bit(song):
    song.instruments[0].gateoff_timer = 0x82
    frames = play(Player(song), 8)
    # Gate still masked off at fetch, but no $0F00 ADSR write.
    assert (constants.AD_REG, 0x0F) not in frames[3]
    assert (constants.AD_REG, 0x09) in frames[5]


def test_transpose():
    song = basic_song()
    song.subtunes[0].channels[0].entries = [Transpose(2), PlayPattern(0)]
    history = freq_history(song, 8)
    assert history[6] == constants.FREQ_TABLE[50]


def test_repeat():
    song = basic_song(rows={0: Row(note=note("C-4"), instrument=1)}, length=1)
    song.patterns.append(Pattern(rows=[Row(note=note("C-5"), instrument=1)]))
    song.subtunes[0].channels[0].entries = [
        Repeat(1),
        PlayPattern(0),
        PlayPattern(1),
    ]
    history = freq_history(song, 24)
    # Pattern 0 plays twice (repeat count 1 = one extra), then pattern 1.
    assert history[6] == constants.FREQ_TABLE[48]
    assert history[12] == constants.FREQ_TABLE[48]
    assert history[18] == constants.FREQ_TABLE[60]


def test_subtunes():
    song = basic_song()
    song.patterns.append(Pattern(rows=[Row(note=note("C-5"), instrument=1)]))
    song.subtunes.append(
        Subtune(
            channels=[
                Orderlist([PlayPattern(1)]),
                Orderlist([PlayPattern(0)]),
                Orderlist([PlayPattern(0)]),
            ]
        )
    )
    assert freq_history(song, 8)[6] == constants.FREQ_TABLE[48]
    assert freq_history(song, 8, subtune=1)[6] == constants.FREQ_TABLE[60]
    with pytest.raises(GoatTrackerError, match="subtune"):
        Player(song, subtune=2)


def test_until_loop(song):
    frames = list(iter_frames(song, until_loop=True))
    # Loop detected at the orderlist wrap on frame 47.
    assert len(frames) == 48


def test_max_frames(song):
    assert len(list(iter_frames(song, max_frames=10))) == 10


def test_zero_length_orderlist_stops():
    song = basic_song()
    song.subtunes[0].channels[2].entries = []
    player = Player(song)
    assert len(player.play_frame()) == constants.SID_REGISTERS
    assert player.play_frame() == []
    assert not player.playing
    assert len(list(iter_frames(song, max_frames=10))) == 1


def test_gatetimer_too_high_stops(song):
    song.instruments[0].gateoff_timer = 3
    song.patterns[0].rows[0].command = 0xF
    song.patterns[0].rows[0].data = 0x03
    player = Player(song)
    play(player, 12)
    assert not player.playing


def test_instrument63_default_tempo():
    song = basic_song(
        rows={row: Row(note=note("C-4"), instrument=1) for row in range(4)}
    )
    while len(song.instruments) < 63:
        song.instruments.append(Instrument())
    song.instruments[62].attack_decay = 4
    assert init_frames(song) == [5, 9, 13, 17]


def test_mute(song):
    player = Player(song)
    player.mute(0)
    play(player, 8)
    assert player.regs[constants.CONTROL_REG] == 0x08
    player.mute(0, muted=False)
    frames = play(player, 60)
    assert any((constants.CONTROL_REG, 0x41) in writes for writes in frames)


def test_three_channels():
    song = basic_song()
    instrument2 = add_test_instrument(song, waveform=0x21)
    song.patterns.append(
        Pattern(rows=[Row(note=note("E-4"), instrument=instrument2)] + [Row()] * 7)
    )
    song.subtunes[0].channels[1].entries = [PlayPattern(1)]
    player = Player(song)
    play(player, 8)
    assert player.regs[constants.CONTROL_REG] == 0x41
    assert player.regs[constants.CONTROL_REG + 7] == 0x21
    voice2_base = constants.VOICE_REG_SIZE
    freq = (player.regs[voice2_base + 1] << 8) | player.regs[voice2_base]
    assert freq == constants.FREQ_TABLE[52]


def test_wavetable_realtime_commands(song):
    # Wavetable-executed portamento up/down and vibrato around C-4.
    porta = song.speedtable.add(0x00, 0x80)
    vib = song.speedtable.add(0x02, 0x20)
    song.wavetable.left = [0x41, 0xF1, 0xF1, 0xF2, 0xF4, 0xFF]
    song.wavetable.right = [0x00, porta, porta, porta, vib, 0x05]
    history = freq_history(song, 12)
    assert history[6] == C4_FREQ
    assert history[7] == C4_FREQ + 0x80
    assert history[8] == C4_FREQ + 0x100
    assert history[9] == C4_FREQ + 0x80
    # The loop keeps running the vibrato step.
    assert history[10] != history[9]


def test_wavetable_toneporta_command(song):
    speed = song.speedtable.add(0x08, 0x00)
    song.wavetable.left = [0x81, 0xF3, 0xFF]
    song.wavetable.right = [0x80 + 60, speed, 0x02]
    history = freq_history(song, 20)
    # Starts on absolute C-5, slides down to the pattern note C-4.
    assert history[6] == constants.FREQ_TABLE[60]
    assert history[7] < constants.FREQ_TABLE[60]
    assert history[16] == C4_FREQ
    assert history[18] == C4_FREQ


def test_wavetable_filter_and_volume_commands(song):
    song.wavetable.left = [0x41, 0xFB, 0xFC, 0xFD, 0xFB, 0xFF]
    song.wavetable.right = [0x00, 0xF1, 0x55, 0x07, 0x00, 0x00]
    player = Player(song)
    frames = play(player, 12)
    assert (constants.RES_FILT_REG, 0xF1) in frames[8]
    assert (constants.FC_HI_REG, 0x55) in frames[9]
    assert (constants.MODE_VOL_REG, 0x07) in frames[10]
    # Filter control $00 also stops filter execution.
    assert (constants.RES_FILT_REG, 0x00) in frames[12 - 1]


def test_wavetable_ad_pulse_filter_pointer_commands(song):
    song.pulsetable.left = [0x84, 0xFF]
    song.pulsetable.right = [0x00, 0x00]
    song.filtertable.left = [0x00, 0xFF]
    song.filtertable.right = [0x66, 0x00]
    song.wavetable.left = [0x41, 0xF5, 0xF9, 0xFA, 0xFF]
    song.wavetable.right = [0x00, 0x42, 0x01, 0x01, 0x00]
    player = Player(song)
    frames = play(player, 12)
    assert (constants.AD_REG, 0x42) in frames[7]
    # Pulse runs right after the wavetable in the same frame; the
    # filter table runs at the start of the next frame's routine.
    assert (constants.PULSE_HI_REG, 0x04) in frames[8]
    assert (constants.FC_HI_REG, 0x66) in frames[10]


def test_keyon_passes_wavetable_gate(song):
    # A wavetable that drops the gate ($40 = pulse, gate bit clear).
    song.wavetable.left = [0x41, 0x40, 0xFF]
    song.wavetable.right = [0x00, 0x80, 0x00]
    song.patterns[0].rows[2] = Row(note=constants.KEYON)
    history = reg_history(song, 20, constants.CONTROL_REG)
    assert history[7] == 0x40
    # Keyon sets the gate mask, but the wavetable wave stays $40.
    assert history[15] == 0x40
