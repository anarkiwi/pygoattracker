"""GoatTracker V1.25 editor songs (``GTS!``, musicroutine 11.x).

The pinned ``goatold.zip`` example set is the corpus: every song must
parse, round-trip byte for byte, and play identical to the packed 6502
playroutine (``EmuPlayer``) once the F9 relocator runs.
"""

import sys
from pathlib import Path

import pytest
from pysidtracker.oracle import EmuPlayer

from pygoattracker.errors import SngParseError, SngValidationError
from pygoattracker.gt1 import DEFAULT_PARAMS, Gt1Song, decompile_gt1
from pygoattracker.gt1player import Gt1Player
from pygoattracker.gt1sng import (
    Gt1Sng,
    LAYOUT_V093,
    parse_gts1,
    relocate,
    write_gts1,
)
from tests._fixture_cache import (
    GOATTRK093_SONGS,
    GOATTRK_SONGS,
    GOATOLD_SONGS,
    goatold_song,
    goattrk093_song,
    goattrk_song,
)

REPO = Path(__file__).resolve().parent.parent
ORACLE_SID = "MUSICIANS/C/Cadaver/GoatTracker_Classical_Example.sid"
DOJO_SID = "MUSICIANS/C/Cadaver/Dojo.sid"
SONGS = sorted(GOATOLD_SONGS)
V093_SONGS = sorted(GOATTRK093_SONGS)
V153_SONGS = sorted(GOATTRK_SONGS)


def _oracle_sid(relpath: str = ORACLE_SID) -> Path:
    sys.path.insert(0, str(REPO / "scripts"))
    import fetch_tunes
    from pysidtracker.testing import resolve_tune

    path = resolve_tune(relpath, cache_dir=fetch_tunes.CACHE)
    if path is None:
        pytest.skip("oracle sid unavailable (no local HVSC tree, mirror offline)")
    return Path(path)


_EMPTY_WAVE = b"\x00\x00\xff\x00"
_REST_ROW = b"\x5f\x00\x00"
_END_ROW = b"\xff\x00\x00"


def _sng(
    orderlists=None,
    instruments=None,
    patterns=None,
    name=b"SONG",
    author=b"AUTHOR",
    copyright_text=b"CC",
    magic=b"GTS!",
    filtertable=None,
):
    out = bytearray(magic)
    for text in (name, author, copyright_text):
        out += bytes(text)[:32].ljust(32, b"\x00")
    orderlists = [[bytes((0, 0xFF, 0))] * 3] if orderlists is None else orderlists
    out.append(len(orderlists))
    for chans in orderlists:
        for blob in chans:
            out.append(len(blob) - 1)
            out += bytes(blob)
    instruments = (
        [(bytes(7), len(_EMPTY_WAVE), bytes(16), _EMPTY_WAVE)] * 31
        if instruments is None
        else instruments
    )
    for params, wavelen, insname, wave in instruments:
        out += bytes(params) + bytes((wavelen,)) + insname + wave
    patterns = [b"\x5f\x00\x00\xff\x00\x00"] if patterns is None else patterns
    out.append(len(patterns))
    for body in patterns:
        out.append(len(body))
        out += bytes(body)
    if filtertable is not None:
        out += bytes(filtertable)[:256].ljust(256, b"\x00")
    return bytes(out)


def _v093_body(
    wave=b"\x01\x00\x00\x00", orderlist=b"\x00\xff", pattern=b"\x5f\x00\x00\xff\x00\x00"
):
    """A hand-built V0.9x body: ``$00``-terminated wavetables, orderlist
    ``[entries][endmark]`` (no restart byte), independent of the fixtures."""
    out = bytearray(b"GTS!")
    for text in (b"NAME", b"AUTHOR", b"CC"):
        out += text.ljust(32, b"\x00")
    out.append(1)
    for _ in range(3):
        out.append(len(orderlist) - 1)
        out += orderlist
    for _ in range(31):
        out += bytes(7) + bytes((len(wave),)) + b"\x00" * 16 + wave
    out.append(1)
    out.append(len(pattern))
    out += pattern
    return bytes(out)


def emulated(song: Gt1Song, frames: int, subtune: int = 0):
    """The register grid of the packed 6502 playroutine itself."""
    address, data = song.pack()
    emu = EmuPlayer(data, address, address, address + 3, subtune)
    return emu.render_grid(frames)


@pytest.mark.parametrize("name", SONGS)
def test_parse_and_round_trip(name):
    data = goatold_song(name)
    song = parse_gts1(data)
    assert song.sng is not None
    assert len(song.instruments) == 31
    assert "filtertable" not in song.segments
    assert write_gts1(song) == data


@pytest.mark.parametrize("name", SONGS)
def test_playback_matches_6502_playroutine(name):
    song = parse_gts1(goatold_song(name))
    assert Gt1Player(song).render_grid(120) == emulated(song, 120)


def test_goatclas_matches_gvsc_sid():
    """The relocator must reproduce the editor's packed tune byte for byte."""
    sid = _oracle_sid().read_bytes()
    image = sid[0x7E:]
    song = parse_gts1(goatold_song("GOATCLAS.SNG"), "11.3")
    assert song.sng.name == b"GoatTracker classical example"
    assert relocate(song).pack() == (0x1000, image)
    decoded = decompile_gt1(sid)
    assert decoded.player == "11.3"
    assert Gt1Player(song).render_grid(300) == Gt1Player(decoded).render_grid(300)
    assert Gt1Player(song).render_grid(300) == emulated(song, 300)


def test_goatclas_relocates_instruments_and_wavetable():
    packed = relocate(parse_gts1(goatold_song("GOATCLAS.SNG")))
    assert [ins.wave_ptr for ins in packed.instruments] == [1, 1, 3]
    assert packed.wavetable == [
        (0x21, 0),
        (0xFF, 0),
        (0x11, 0),
        (0xFF, 0),
    ]
    assert all(ins.name == b"" for ins in packed.instruments)


def test_old_endmark_parses_round_trips_and_relocates():
    data = goatold_song("GOATCLAS.SNG")
    song = parse_gts1(data)
    original, old = song.orderlists[0][0], song.orderlists[0][0][:-2] + b"\xfe"
    song.orderlists[0][0] = old
    assert write_gts1(song) == data.replace(b"\x06" + original, b"\x05" + old, 1)
    packed = relocate(song)
    assert packed.orderlists[0][0] == original[:-2] + b"\xff\x00"


def test_tempo_data_decrement_and_timing_marks():
    song = parse_gts1(_sng(patterns=[_REST_ROW + _END_ROW]))
    song.patterns[0] = bytes((0x30, 0x07, 0x9A)) + bytes((0x31, 0x07, 0xC0)) + _END_ROW
    keep = relocate(song)
    assert keep.patterns[0] == bytes((0x30, 0x07, 0x99, 0x31, 0x07, 0xC0, 0xFF))
    body = bytes((0x30, 0x07, 0x9A)) + bytes((0x31, 0x07, 0xC0)) + _END_ROW
    strip = relocate(parse_gts1(_sng(patterns=[body]), "11.1"))
    assert strip.patterns[0] == bytes((0x30, 0x07, 0x99, 0x31, 0x00, 0x00, 0xFF))


def test_vibrato_nybble_swap_and_sticky_instrument():
    body = bytes((0x30, 0x24, 0x12)) + bytes((0x31, 0x00, 0x34)) + _END_ROW
    song = parse_gts1(_sng(patterns=[body]))
    assert relocate(song).patterns[0] == bytes(
        (0x30, 0x24, 0x21, 0x31, 0x20, 0x34, 0xFF)
    )


def test_packed_rest_runs_and_max_length():
    def packed(rows):
        body = _REST_ROW * rows + _END_ROW
        return relocate(parse_gts1(_sng(patterns=[body]))).patterns[0]

    assert packed(3) == b"\x5f\x00\x00\xbf\xbf\xff"
    assert packed(4) == b"\x5f\x00\x00\xfe\xbf\xff"
    assert packed(66) == b"\x5f\x00\x00\xc0\xbf\xff"
    assert packed(67) == b"\x5f\x00\x00\xc0\xbf\xbf\xff"


def test_unreferenced_patterns_are_not_packed():
    data = goatold_song("GOATCLAS.SNG")
    song = parse_gts1(data)
    song.patterns[1] = _END_ROW
    song.patterns[2] = _END_ROW
    song.orderlists[0] = [bytes((0, 0xFF, 0))] * 3
    packed = relocate(song)
    assert len(packed.patterns) == 1
    assert len(packed.instruments) <= len(song.instruments)


def test_relocate_is_idempotent_and_inert_for_packed_songs():
    song = parse_gts1(goatold_song("GOATCLAS.SNG"))
    once, twice = relocate(song), relocate(relocate(song))
    assert once.sng is None
    assert twice.pack() == once.pack()
    assert relocate(once) is once


def test_write_gts1_refuses_packed_and_malformed_songs():
    packed = relocate(parse_gts1(goatold_song("GOATCLAS.SNG")))
    with pytest.raises(SngValidationError):
        write_gts1(packed)
    song = parse_gts1(goatold_song("GOATCLAS.SNG"))
    song.instruments = song.instruments[:30]
    with pytest.raises(SngValidationError):
        write_gts1(song)
    song2 = parse_gts1(goatold_song("GOATCLAS.SNG"))
    song2.orderlists[0] = [b"\x00\xff\x00", b"\x00\xff\x00"]
    with pytest.raises(SngValidationError):
        write_gts1(song2)
    song3 = parse_gts1(goatold_song("GOATCLAS.SNG"))
    song3.sng = Gt1Sng(b"x" * 33, b"", b"")
    with pytest.raises(SngValidationError, match="32"):
        write_gts1(song3)


def test_parse_rejects_bad_input():
    base = _sng()
    with pytest.raises(SngParseError, match="identifier"):
        parse_gts1(_sng(magic=b"GTS2"))
    with pytest.raises(SngParseError, match="11.x"):
        parse_gts1(base, "1.53")
    with pytest.raises(SngParseError, match="unknown playroutine"):
        parse_gts1(base, "1.59")
    with pytest.raises(SngParseError, match="truncated"):
        parse_gts1(base[:-4])
    with pytest.raises(SngParseError, match="endmark"):
        parse_gts1(_sng(orderlists=[[b"\x00\x01\x02"] * 3]))
    with pytest.raises(SngParseError, match="end row"):
        parse_gts1(_sng(patterns=[b"\x00" * 9]))
    with pytest.raises(SngParseError, match="bad row length"):
        parse_gts1(_sng(patterns=[b"\x00\x00\x00\x00"]))
    with pytest.raises(SngParseError, match="trailing bytes"):
        parse_gts1(base + b"\x00" * 255)
    v153 = base + b"\x00" * 256
    song = parse_gts1(v153)
    assert song.player == "1.53" and len(song.filtertable) == 256
    with pytest.raises(SngParseError, match="V1.5x"):
        parse_gts1(v153, "11.3")
    with pytest.raises(SngParseError, match="11.x"):
        parse_gts1(base, "1.50")


def test_parse_rejects_bad_limits():
    with pytest.raises(SngParseError, match="subtune"):
        parse_gts1(_sng(orderlists=[[bytes((0, 0xFF, 0))] * 3] * 33))
    with pytest.raises(SngParseError, match="pattern count"):
        parse_gts1(_sng(patterns=[]))
    with pytest.raises(SngParseError, match="wavetable size"):
        parse_gts1(_sng(instruments=[(bytes(7), 5, bytes(16), b"\x00" * 5)] * 31))
    with pytest.raises(SngParseError, match="references pattern"):
        parse_gts1(_sng(orderlists=[[bytes((0x50, 0xFF, 0))] * 3]))
    with pytest.raises(SngParseError, match="zero length"):
        parse_gts1(_sng(orderlists=[[b"\x00"] * 3]))
    truncated = goatold_song("BOFH.SNG")[:400]
    with pytest.raises(SngParseError, match="truncated"):
        parse_gts1(truncated)


def test_reader_reads_gts1_natively():
    from pygoattracker.reader import parse_sng

    assert isinstance(parse_sng(goatold_song("GOATCLAS.SNG")), Gt1Song)
    assert isinstance(parse_sng(goattrk_song("examples/dojo.sng")), Gt1Song)


def test_v153_layout_reads_natively_and_gt2_import_remains():
    v153 = goattrk_song("examples/dojo.sng")
    assert v153[:4] == b"GTS!"
    song = parse_gts1(v153)
    assert song.player == "1.53" and len(song.filtertable) == 256
    assert write_gts1(song) == v153
    from pygoattracker.reader import parse_sng

    assert isinstance(parse_sng(v153), Gt1Song)
    assert parse_sng(v153, convert=True).name  # the GT2 import path still accepts it


def test_header_fields_are_raw_bytes():
    song = parse_gts1(goatold_song("GOATCLAS.SNG"))
    assert isinstance(song.sng, Gt1Sng)
    assert song.sng.author == b"Lasse \xd6\xf6rni"
    assert song.sng.copyright == b"11/2001"


def test_author_block_per_playroutine():
    data = goatold_song("GOATCLAS.SNG")
    author = b"Lasse \xd6\xf6rni".ljust(32, b" ")
    for player in ("11.1", "11.2"):
        assert relocate(parse_gts1(data, player)).author == b""
    for player in ("11.3", "11.4"):
        assert relocate(parse_gts1(data, player)).author == author
    assert relocate(parse_gts1(data, "11.5")).author == author + b"\x00"


@pytest.mark.parametrize("player", ("11.1", "11.2", "11.3", "11.4", "11.5"))
def test_playback_matches_6502_for_each_playroutine(player):
    song = parse_gts1(goatold_song("GOATCLAS.SNG"), player)
    assert Gt1Player(song).render_grid(120) == emulated(song, 120)


@pytest.mark.parametrize("member", V093_SONGS)
def test_v093_parse_and_round_trip(member):
    data = goattrk093_song(member)
    song = parse_gts1(data)
    assert song.sng is not None and song.sng.layout == LAYOUT_V093
    assert song.player == "0.93"
    assert "filtertable" not in song.segments
    assert len(song.instruments) == 31
    assert write_gts1(song) == data


@pytest.mark.parametrize("member", V093_SONGS)
def test_v093_is_native_only(member):
    song = parse_gts1(goattrk093_song(member))
    with pytest.raises(SngValidationError, match="native-only"):
        relocate(song)
    for call in (song.pack, song.to_sid):
        with pytest.raises(SngValidationError, match="native-only"):
            call()
    with pytest.raises(SngParseError, match="playroutine"):
        Gt1Player(song)
    with pytest.raises(SngParseError):
        song.to_gt2()


def test_reader_reads_v093_natively_and_gt2_import_rejects():
    from pygoattracker.reader import parse_sng

    data = goattrk093_song("examples/example.sng")
    assert isinstance(parse_sng(data), Gt1Song)
    with pytest.raises(SngParseError):
        parse_sng(data, convert=True)


def test_v093_layouts_stay_distinct():
    assert parse_gts1(goattrk093_song("examples/example.sng")).sng.layout == LAYOUT_V093
    assert parse_gts1(goatold_song("GOATCLAS.SNG")).sng.layout == "1.25"
    assert parse_gts1(goattrk_song("examples/dojo.sng")).sng.layout == "1.4b-V1.53"


def test_v093_detection_is_by_wavetable_terminator_not_fixture():
    body = _v093_body(wave=b"\x01\x00\x00\x00", orderlist=b"\x00\xff")
    assert parse_gts1(body).sng.layout == LAYOUT_V093
    assert write_gts1(parse_gts1(body)) == body
    v125 = _v093_body(wave=b"\x01\x00\xff\x00", orderlist=b"\x00\xff\x00")
    assert parse_gts1(v125).sng.layout == "1.25"


def test_v093_rejects_bad_bodies():
    with pytest.raises(SngParseError, match="trailing bytes"):
        parse_gts1(_v093_body() + b"\x00")
    with pytest.raises(SngParseError, match="end row"):
        parse_gts1(_v093_body(pattern=b"\x5f\x00\x00\x5f\x00\x00"))
    with pytest.raises(SngParseError, match="references pattern"):
        parse_gts1(_v093_body(orderlist=b"\x05\xff"))


def _v153(**kw):
    """A V1.4b-V1.53-layout editor song with the shared ``_sng`` defaults."""
    kw.setdefault("filtertable", bytes(256))
    return parse_gts1(_sng(name=b"V153", **kw))


_FILLER = (bytes(7), len(_EMPTY_WAVE), bytes(16), _EMPTY_WAVE)


@pytest.mark.parametrize("name", V153_SONGS)
def test_v153_parse_and_round_trip(name):
    data = goattrk_song(name)
    song = parse_gts1(data)
    assert song.player == "1.53"
    assert len(song.instruments) == 31
    assert len(song.filtertable) == 256
    assert write_gts1(song) == data


@pytest.mark.parametrize("name", V153_SONGS)
def test_v153_playback_matches_6502_playroutine(name):
    song = parse_gts1(goattrk_song(name))
    assert Gt1Player(song).render_grid(300) == emulated(song, 300)


def test_dojo_matches_hvsc_sid():
    """The relocator must reproduce the editor's packed tune byte for byte."""
    sid = _oracle_sid(DOJO_SID).read_bytes()
    image = sid[0x7E:]
    song = parse_gts1(goattrk_song("examples/dojo.sng"))
    assert relocate(song).pack() == (0x1000, image)
    decoded = decompile_gt1(sid)
    assert decoded.player == "1.53"
    assert Gt1Player(song).render_grid(300) == Gt1Player(decoded).render_grid(300)


@pytest.mark.parametrize("player", ("1.50", "1.50b", "1.51", "1.52"))
def test_v153_playback_matches_6502_for_each_player(player):
    song = parse_gts1(goattrk_song("examples/dojo.sng"), player)
    assert Gt1Player(song).render_grid(120) == emulated(song, 120)


def test_v153_instrument_conversion_and_dedup():
    i1 = (
        b"\x09\x08\x80\x20\x40\xc0\x01",
        4,
        b"BASS" + b"\x00" * 12,
        b"\x41\x00\xff\x00",
    )
    i2 = (
        b"\x22\x33\x41\x00\x10\x0f\x03",
        4,
        b"KOTO" + b"\x00" * 12,
        b"\x21\x00\xff\x00",
    )
    song = _v153(
        instruments=[i1, i2] + [_FILLER] * 29,
        patterns=[bytes((0x30, 0x08, 0x00, 0x31, 0x10, 0x00)) + _END_ROW],
    )
    packed = relocate(song)

    def fields(ins):
        return (
            ins.attack_decay,
            ins.sustain_release,
            ins.pulse,
            ins.pulse_speed,
            ins.pulse_low,
            ins.pulse_high,
            ins.filter,
            ins.wave_ptr,
        )

    assert fields(packed.instruments[0]) == (
        0x09,
        0x08,
        0x08,
        0x20,
        0x04,
        0x0C,
        0x04,
        1,
    )
    assert fields(packed.instruments[1]) == (
        0x22,
        0x33,
        0x04,
        0x01,
        0x01,
        0x00,
        0x0C,
        3,
    )
    assert packed.wavetable == [(0x41, 0), (0xFF, 0), (0x21, 0), (0xFF, 0)]


def test_v153_pattern_commands():
    rows = bytes(
        (
            0x30,
            0x07,
            0x9E,  # settempo: decrement, keep funktempo bit
            0x31,
            0x07,
            0xF0,  # timing mark: above 0xEF, untouched
            0x32,
            0x07,
            0x02,  # funktempo below 3: untouched
            0x33,
            0x34,
            0x71,  # vibrato: nybble swap
            0x34,
            0x15,
            0x21,  # setfilter: x4
            0x35,
            0x30,
            0x05,  # arpeggio: toggle high bit
            0x36,
            0x30,
            0x00,  # arpeggio zero: untouched
        )
    )
    song = _v153(patterns=[rows + _END_ROW])
    assert relocate(song).patterns[0] == bytes(
        (0x30, 0x07, 0x9D, 0x31, 0x07, 0xF0, 0x32, 0x07, 0x02)
        + (0x33, 0x34, 0x17, 0x34, 0x15, 0x84, 0x35, 0x30, 0x85, 0x36, 0x30, 0x00, 0xFF)
    )


def test_v153_tempo_dedup_and_packed_rests():
    rows = bytes((0x30, 0x07, 0x05, 0x31, 0x07, 0x05))
    song = _v153(patterns=[rows + _END_ROW])
    assert relocate(song).patterns[0] == b"\x30\x07\x04\x91\xff"
    rests = _REST_ROW * 4 + _END_ROW
    song = _v153(patterns=[rests])
    assert relocate(song).patterns[0] == b"\x5f\x00\x00\xfe\xbf\xff"


def test_v153_filter_amount_from_instrument_and_filtertable():
    table = bytearray(256)
    table[0:4] = bytes((0xF7, 0x04, 0x00, 0x00))  # step 0: both wraps, amount 0
    table[8:12] = bytes((0x11, 0x22, 0x33, 0x1A))  # step 2: x4, amount 26
    i1 = (bytes(6) + b"\x06", 4, bytes(16), b"\x21\x00\xff\x00")  # filter amount 6
    rows = bytes((0x30, 0x15, 0x03, 0x31, 0x00, 0x00))  # setfilter amount 3
    song = _v153(
        instruments=[i1] + [_FILLER] * 30,
        patterns=[rows + _END_ROW],
        filtertable=bytes(table),
    )
    filtered = relocate(song).filtertable
    assert len(filtered) == 4 * (0x1A + 1)
    assert filtered[0:4] == bytes((0xF7, 0x04, 0xFF, 0xFF))
    assert filtered[8:12] == bytes((0x11, 0x22, 0x33, 0x68))


def test_v153_filter_amount_overflow_rejected():
    i1 = (bytes(6) + b"\x40", 4, bytes(16), b"\x21\x00\xff\x00")
    song = _v153(instruments=[i1] + [_FILLER] * 30)
    with pytest.raises(SngValidationError, match="256"):
        relocate(song)


def test_v153_skips_empty_songs_and_channels():
    song = _v153(
        orderlists=[
            [b"\xff\x00"] * 3,  # empty subtune: discarded
            [b"\x00\xff\x00"] * 3,  # kept
            [b"\x00\xff\x00", b"\xff\x00", b"\xff\x00"],  # a dead channel: discarded
        ]
    )
    assert relocate(song).orderlists == [[b"\x00\xff\x00"] * 3]
    dead = _v153(orderlists=[[b"\xff\x00"] * 3, [b"\xff\x00"] * 3])
    with pytest.raises(SngValidationError, match="discard"):
        relocate(dead)


def test_v153_orderlist_commands_pass_through():
    body = _REST_ROW + _END_ROW
    song = _v153(
        orderlists=[[b"\x00\xe2\x01\xff\x89"] * 3],
        patterns=[body] * 2,
    )
    packed = relocate(song)
    assert packed.orderlists == [[b"\x00\xe2\x01\xff\x89"] * 3]
    assert len(packed.patterns) == 2
    song = _v153(orderlists=[[b"\x00\xff\x00"] * 3])
    song.orderlists[0][0] = b"\x02\xff\x00"
    with pytest.raises(SngValidationError, match="reference pattern"):
        relocate(song)


def test_v153_wavetable_limit():
    waves = []
    for num in range(31):
        wave = (
            bytes(w for i in range(17) for w in ((num + i + 1) & 0x7F, 0)) + b"\xff\x00"
        )
        waves.append((bytes(7), len(wave), bytes(16), wave))
    song = _v153(instruments=waves, patterns=[bytes((0x30, 0xF8, 0)) + _END_ROW])
    with pytest.raises(SngValidationError, match="512"):
        relocate(song)


def test_v153_old_endmark_normalizes():
    song = _v153(orderlists=[[b"\x00\xfe"] * 3])
    assert relocate(song).orderlists == [[b"\x00\xff\x00"] * 3]


def test_v153_writer_requires_full_filtertable():
    song = _v153()
    song.filtertable = bytes(128)
    with pytest.raises(SngValidationError, match="256"):
        write_gts1(song)


def test_v153_relocate_is_idempotent_and_inert_for_packed_songs():
    song = _v153()
    once = relocate(song)
    assert once.sng is None
    assert relocate(once) is once
    assert once.address == 0x1000 and once.pack()[0] == 0x1000


def test_v153_author_and_default_params():
    song = _v153(author=b"Cadaver")
    packed = relocate(song)
    assert packed.params == DEFAULT_PARAMS
    assert packed.author == b"Cadaver" + b" " * 25
    assert packed.player == "1.53"


def test_v153_state_and_data_come_from_the_playroutine_reference():
    from pygoattracker.gt1 import reference

    song = _v153()
    packed = relocate(song)
    ref = reference("1.53")
    assert packed.state == {pos: ref.code[pos] for pos in ref.free}
    assert packed.data == ref.code[ref.code_end :]


def test_to_gt2_matches_legacy_import():
    from pygoattracker.reader import parse_sng

    data = goattrk_song("examples/dojo.sng")
    assert parse_gts1(data).to_gt2().name == parse_sng(data, convert=True).name
    assert parse_sng(data).to_gt2().name == "Dojo"


def test_to_gt2_requires_an_editor_song():
    song = parse_gts1(goattrk_song("examples/dojo.sng"))
    with pytest.raises(SngValidationError):
        relocate(song).to_gt2()
    v125 = parse_gts1(goatold_song("GOATCLAS.SNG"))
    with pytest.raises(SngParseError):
        v125.to_gt2()
