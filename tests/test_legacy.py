"""GoatTracker 1.x (GTS!) and early GoatTracker 2 (GTS2) imports."""

import pytest

from pygoattracker import constants
from pygoattracker.errors import SngParseError
from pygoattracker.model import PlayPattern, Row
from pygoattracker.reader import parse_sng
from pygoattracker.writer import build_sng

from tests.conftest import basic_song


def header(magic: bytes) -> bytes:
    return (
        magic
        + b"OLD SONG".ljust(32, b"\0")
        + b"AUTHOR".ljust(32, b"\0")
        + b"1999".ljust(32, b"\0")
    )


def orderlists() -> bytes:
    # One subtune; every channel plays pattern 0.
    return bytes([1]) + bytes([2, 0x00, 0xFF, 0x00]) * 3


def gts1_instrument(
    ad=0,
    sr=0,
    pulse=0,
    pulseadd=0,
    pulselow=0,
    pulsehigh=0,
    filterptr=0,
    wave=(),
    name="",
) -> bytes:
    out = bytes([ad, sr, pulse, pulseadd, pulselow, pulsehigh, filterptr])
    out += bytes([len(wave) * 2])
    out += name.encode("latin-1").ljust(constants.MAX_INSTRNAMELEN, b"\0")
    for left, right in wave:
        out += bytes([left, right])
    return out


def gts1_pattern(rows) -> bytes:
    rows = list(rows) + [(0xFF, 0, 0, 0)]
    out = bytes([len(rows) * 3])
    for note, instrument, command, data in rows:
        out += bytes([note, (instrument << 3) | command, data])
    return out


def build_gts1() -> bytes:
    instruments = {
        1: gts1_instrument(
            ad=0x09,
            pulse=0x80,
            pulseadd=0x20,
            pulselow=0x40,
            pulsehigh=0xC0,
            filterptr=1,
            wave=[(0x41, 0x00), (0xFF, 0x01)],
            name="LEAD",
        ),
        2: gts1_instrument(ad=0x22, wave=[(0x00, 0x00), (0xFF, 0x00)], name="EMPTY"),
        3: gts1_instrument(
            pulse=0x41,  # odd: GT1 "no hardrestart" flag
            filterptr=2,
            wave=[(0x08, 0x00), (0xFF, 0x00)],
            name="HAT",
        ),
    }
    data = header(b"GTS!") + orderlists()
    for num in range(1, 32):
        data += instruments.get(num, gts1_instrument())
    data += bytes([1])  # pattern count
    data += gts1_pattern(
        [
            (0x30, 1, 0, 0x00),  # note with instrument 1
            (0x5F, 0, 4, 0x34),  # old rest + vibrato $34
            (0x5E, 0, 0, 0x00),  # old keyoff
            (0x00, 0, 1, 0x08),  # portamento up $08
            (0x10, 0, 0, 0x47),  # old arpeggio $47
            (0x5F, 0, 5, 0x02),  # filtertable pointer
            (0x5F, 0, 7, 0x03),  # tempo 3
            (0x5F, 0, 7, 0xF5),  # master volume 5
            (0x5F, 0, 7, 0x00),  # tempo 0 = funktempo
            (0x60, 0, 0, 0x00),  # out-of-range note
        ]
    )
    filtertable = bytearray(256)
    filtertable[2] = 9  # GT1 funktempo values live in step 0
    filtertable[3] = 6
    # Step 1: set lowpass, resonance/channel $40, cutoff $50, next step.
    filtertable[4:8] = bytes([0x40, 0x90, 0x50, 2])
    # Step 2: modulate for 130 frames speed 1, then jump back to step 1.
    filtertable[8:12] = bytes([0x00, 130, 0x01, 1])
    return data + bytes(filtertable)


@pytest.fixture(name="gts1")
def gts1_fixture():
    return parse_sng(build_gts1())


def test_gts1_header(gts1):
    assert gts1.name == "OLD SONG"
    assert gts1.author == "AUTHOR"
    assert len(gts1.subtunes) == 1
    assert gts1.subtunes[0].channels[0].entries == [PlayPattern(0)]


def test_gts1_wavetable_conversion(gts1):
    # Instrument 1's table, instrument 3's (silent wave $08 -> $E8,
    # empty instrument 2 removed), then the arpeggio program.
    assert gts1.wavetable.left == [0x41, 0xFF, 0xE8, 0xFF, 0x41, 0, 0, 0, 0xFF]
    assert gts1.wavetable.right == [0x00, 0x01, 0x00, 0x00, 0, 4, 7, 0, 6]
    assert gts1.instruments[0].wave_ptr == 1
    assert gts1.instruments[2].wave_ptr == 3


def test_gts1_pulse_conversion(gts1):
    # Set $800, up to $C00, down to $400, back up to $800, loop; the
    # pre-v2.4 speed doubling restores the halved speeds. Instrument
    # 3's program: set $400 and stop.
    assert gts1.pulsetable.left == [0x88, 0x20, 0x40, 0x20, 0xFF, 0x84, 0xFF]
    assert gts1.pulsetable.right == [0x00, 0x20, 0xE0, 0x20, 0x02, 0x00, 0x00]
    assert gts1.instruments[0].pulse_ptr == 1
    assert gts1.instruments[2].pulse_ptr == 6


def test_gts1_pulse_dedup(gts1):
    data = bytearray(build_gts1())
    # Give instrument 2 the same pulse parameters as instrument 1: no
    # new pulse program may appear, the existing one is shared.
    base = len(header(b"GTS!") + orderlists())
    size = 8 + 16 + 4  # params + name + two wavetable rows
    data[base + size + 2 : base + size + 6] = data[base + 2 : base + 6]
    song = parse_sng(bytes(data))
    assert song.pulsetable == gts1.pulsetable


def test_gts1_filter_conversion(gts1):
    assert gts1.filtertable.left == [0x90, 0x00, 0x7F, 0x03, 0xFF]
    assert gts1.filtertable.right == [0x40, 0x50, 0x01, 0x01, 0x01]
    assert gts1.instruments[0].filter_ptr == 1
    assert gts1.instruments[2].filter_ptr == 3


def test_gts1_pattern_conversion(gts1):
    rows = gts1.patterns[0].rows
    assert len(rows) == 10
    assert rows[0] == Row(0x90, 1, 0, 0)
    # Old vibrato $34 -> speedtable (3, $40).
    assert rows[1] == Row(constants.REST, 0, constants.CMD_VIBRATO, 1)
    assert gts1.speedtable.left[0] == 3
    assert gts1.speedtable.right[0] == 0x40
    assert rows[2].note == constants.KEYOFF
    # Old portamento $08 -> 16-bit speed $0020.
    assert rows[3] == Row(constants.FIRSTNOTE, 0, constants.CMD_PORTAUP, 2)
    assert gts1.speedtable.left[1] == 0x00
    assert gts1.speedtable.right[1] == 0x20
    # Filtertable pointer through the conversion map.
    assert rows[5] == Row(constants.REST, 0, constants.CMD_SETFILTERPTR, 3)
    assert rows[6] == Row(constants.REST, 0, constants.CMD_SETTEMPO, 3)
    assert rows[7] == Row(constants.REST, 0, constants.CMD_SETMASTERVOL, 5)
    # Tempo 0 recalls GT1's funktempo from the filtertable's step 0.
    assert rows[8] == Row(constants.REST, 0, constants.CMD_FUNKTEMPO, 3)
    assert gts1.speedtable.left[2] == 9
    assert gts1.speedtable.right[2] == 6
    # Note beyond G#7 becomes a rest.
    assert rows[9] == Row(constants.REST, 0, 0, 0)


def test_gts1_arpeggio_conversion(gts1):
    # The 0XY arpeggio became a cloned instrument with a wavetable
    # program: copied waveform, then 4/7/0 relative steps in a loop.
    row = gts1.patterns[0].rows[4]
    assert row.instrument == 2
    assert row.command == 0
    assert row.data == 0
    clone = gts1.instruments[1]
    assert clone.name == "LEAD047"
    assert clone.wave_ptr == 5
    assert clone.attack_decay == 0x09


def test_gts1_instrument_flags(gts1):
    assert len(gts1.instruments) == 3
    lead, clone, hat = gts1.instruments
    # First wave 0 turned into the legato flag for all instruments.
    assert lead.gateoff_timer == 0x40
    assert clone.gateoff_timer == 0x40
    # GT1's pulse LSB set the no-hardrestart flag.
    assert hat.gateoff_timer == 0xC0
    assert hat.name == "HAT"


def test_gts1_writes_as_gts5(gts1):
    data = build_sng(gts1)
    assert data[:4] == constants.SNG_MAGIC
    assert parse_sng(data) == gts1


def test_gts1_truncated():
    with pytest.raises(SngParseError, match="truncated"):
        parse_sng(build_gts1()[:200])


def gts2_instrument(
    ad=0,
    sr=0,
    wave_ptr=0,
    pulse_ptr=0,
    filter_ptr=0,
    vibdelay=0,
    vibrato=0,
    gatetimer=0,
    firstwave=0,
    name="",
) -> bytes:
    out = bytes([ad, sr, wave_ptr, pulse_ptr, filter_ptr, vibdelay, vibrato, gatetimer])
    out += bytes([firstwave])
    return out + name.encode("latin-1").ljust(constants.MAX_INSTRNAMELEN, b"\0")


def gts5_pattern(rows) -> bytes:
    rows = list(rows)
    out = bytes([len(rows) + 1])
    for note, instrument, command, data in rows:
        out += bytes([note, instrument, command, data])
    return out + bytes([constants.ENDPATT, 0, 0, 0])


def table(left, right) -> bytes:
    return bytes([len(left)]) + bytes(left) + bytes(right)


def build_gts2() -> bytes:
    data = header(b"GTS2") + orderlists()
    data += bytes([1])  # instrument count
    data += gts2_instrument(
        ad=0x09,
        wave_ptr=1,
        pulse_ptr=1,
        vibdelay=5,
        vibrato=0xB4,
        gatetimer=2,
        firstwave=0x89,
        name="OLDLEAD",
    )
    data += table([0x41, 0xFF], [0x00, 0x00])  # wavetable
    data += table([0x88, 0x10, 0xFF], [0x00, 0x05, 0x00])  # pulsetable
    data += table([], [])  # filtertable
    data += bytes([1])  # pattern count
    rest = constants.REST
    data += gts5_pattern(
        [
            (0x90, 1, 0, 0x00),
            (rest, 0, constants.CMD_PORTAUP, 0x40),
            (rest, 0, constants.CMD_VIBRATO, 0xB4),
            (rest, 0, constants.CMD_FUNKTEMPO, 0x96),
            (rest, 0, constants.CMD_TONEPORTA, 0x00),
        ]
    )
    return data


def test_gts2_conversion():
    song = parse_sng(build_gts2())
    instrument = song.instruments[0]
    # Old fine vibrato $B4 -> speedtable (3, $48).
    assert instrument.vibrato_param == 1
    assert song.speedtable.left[0] == 3
    assert song.speedtable.right[0] == 0x48
    # First wave $89: high bit -> no-hardrestart flag.
    assert instrument.first_wave == 0x09
    assert instrument.gateoff_timer == 0x82
    # Pre-v2.4 pulse speeds double.
    assert song.pulsetable.right == [0x00, 0x0A, 0x00]
    rows = song.patterns[0].rows
    assert rows[1].data == 2  # portamento $40 -> ($01, $00)
    assert song.speedtable.left[1] == 0x01
    assert song.speedtable.right[1] == 0x00
    assert rows[2].data == 1  # vibrato reuses the instrument's entry
    assert rows[3].data == 3  # funktempo $96 -> (9, 6)
    assert song.speedtable.left[2] == 9
    assert song.speedtable.right[2] == 6
    assert rows[4].data == 0  # tie-note stays zero
    assert parse_sng(build_sng(song)) == song


def test_gts2_nofinevibrato():
    song = parse_sng(build_gts2(), finevibrato=False)
    assert song.speedtable.left[0] == 0xB
    assert song.speedtable.right[0] == 0x40


def test_gts3_conversions():
    song = basic_song()
    song.pulsetable.left = [0x88, 0x10, 0x08, 0xFF]
    song.pulsetable.right = [0x00, 0x05, 0xB0, 0x00]
    song.instruments[0].first_wave = 0x81
    data = bytearray(build_sng(song))
    data[:4] = b"GTS3"
    parsed = parse_sng(bytes(data))
    # Modulation speeds double (signed, clamped); set rows untouched.
    assert parsed.pulsetable.right == [0x00, 0x0A, 0x80, 0x00]
    assert parsed.instruments[0].first_wave == 0x01
    assert parsed.instruments[0].gateoff_timer == 0x82


def test_gts4_conversions():
    song = basic_song()
    song.pulsetable.left = [0x10, 0xFF]
    song.pulsetable.right = [0x05, 0x00]
    song.instruments[0].first_wave = 0
    data = bytearray(build_sng(song))
    data[:4] = b"GTS4"
    parsed = parse_sng(bytes(data))
    # No pulse doubling for GTS4, but the legato conversion applies.
    assert parsed.pulsetable.right == [0x05, 0x00]
    assert parsed.instruments[0].gateoff_timer == 0x42
