"""Decompiling packed GoatTracker .sid files.

Self-contained: a minimal packed image is assembled in memory (mirroring
greloc's data layout) so no external .sid files are needed. An optional
real-HVSC smoke test runs only when ``PYGOATTRACKER_SID`` points at a file.
"""

import os
import struct

import pytest

from pysidtracker import PlayroutineKind, SidError

from pygoattracker import constants
from pygoattracker import sid
from pygoattracker.errors import GoatTrackerError, SidParseError
from pygoattracker.reader import parse_sng
from pygoattracker.writer import build_sng

REST = constants.REST
LOAD = 0x1000


def _psid(
    data: bytes,
    init=LOAD,
    play=LOAD,
    songs=1,
    name="TEST",
    author="AUTH",
    released="2026",
) -> bytes:
    """Wrap C64 ``data`` (loaded at LOAD) in a minimal PSID v2 container."""
    header = bytearray(0x7C)
    header[0:4] = b"PSID"
    struct.pack_into(">H", header, 0x04, 2)  # version
    struct.pack_into(">H", header, 0x06, 0x7C)  # data offset
    struct.pack_into(">H", header, 0x08, 0)  # load addr in file
    struct.pack_into(">H", header, 0x0A, init)
    struct.pack_into(">H", header, 0x0C, play)
    struct.pack_into(">H", header, 0x0E, songs)
    struct.pack_into(">H", header, 0x10, 1)  # start song
    header[0x16 : 0x16 + len(name)] = name.encode("latin-1")
    header[0x36 : 0x36 + len(author)] = author.encode("latin-1")
    header[0x56 : 0x56 + len(released)] = released.encode("latin-1")
    return bytes(header) + struct.pack("<H", LOAD) + data


def _minimal_packed(trailer: bytes = b"", freq_lo=None, songs=1) -> bytes:
    """One subtune, one instrument, a 2-row wavetable, two 1-row patterns.

    ``trailer`` appends bytes after the pattern block (as greloc does with
    author info); the pattern count must come from the pattern-pointer table,
    not the data end, so these must be ignored. ``freq_lo`` overrides the
    frequency-table low column (to model a finetuned table whose high column is
    stock but whose low column has been retuned). ``songs`` sets the PSID header
    subtune count without changing the single-subtune data (to model a header
    that mis-advertises the count).
    """
    note_c4 = constants.note_value("C-4")
    mem = bytearray(0x100)
    freqlen = 12
    pos = 0

    def emit(chunk):
        nonlocal pos
        start = pos
        mem[pos : pos + len(chunk)] = bytes(chunk)
        pos += len(chunk)
        return start

    lo_col = constants.FREQ_LO[:freqlen] if freq_lo is None else freq_lo[:freqlen]
    emit(lo_col)
    emit(constants.FREQ_HI[:freqlen])
    songtbllo = emit([0, 0, 0])
    songtblhi = emit([0, 0, 0])
    patttbllo = emit([0, 0])
    patttblhi = emit([0, 0])
    emit([0x09])  # mt_insad
    emit([0x00])  # mt_inssr
    emit([0x01])  # mt_inswaveptr
    emit([0x41, 0xFF])  # wavetable left (waveform, jump)
    emit([0x00, 0x01])  # wavetable right (packed note, jump target)
    order0 = emit([0x00, 0xFF, 0x00])  # channel 0: pattern 0
    order1 = emit([0x01, 0xFF, 0x00])  # channel 1: pattern 1
    order2 = emit([0x00, 0xFF, 0x00])  # channel 2: pattern 0
    patt0 = emit([0x01, note_c4, 0x00])  # instr 1, C-4, end
    patt1 = emit([REST, 0x00])  # one rest row, end

    for i, addr in enumerate((order0, order1, order2)):
        mem[songtbllo + i] = (LOAD + addr) & 0xFF
        mem[songtblhi + i] = (LOAD + addr) >> 8
    for i, addr in enumerate((patt0, patt1)):
        mem[patttbllo + i] = (LOAD + addr) & 0xFF
        mem[patttblhi + i] = (LOAD + addr) >> 8
    return _psid(bytes(mem[:pos]) + trailer, songs=songs)


@pytest.fixture(name="packed")
def packed_fixture() -> bytes:
    return _minimal_packed()


def test_header_fields(packed):
    header = sid.parse_sid_header(packed)
    assert header.magic == b"PSID"
    assert header.version == 2
    assert header.songs == 1
    assert header.name == "TEST"
    assert header.author == "AUTH"


def test_not_a_sid():
    with pytest.raises(SidParseError, match="PSID/RSID"):
        sid.parse_sid_header(b"NOPE" + b"\0" * 120)


def test_decompile_minimal(packed):
    result = sid.decompile_sid(packed)
    song = result.song
    assert song.name == "TEST"
    assert len(song.subtunes) == 1
    assert len(song.subtunes[0].channels) == 3
    assert len(song.patterns) == 2
    assert len(song.instruments) == 1


def test_minimal_notes_and_instrument(packed):
    song = sid.decompile_sid(packed).song
    row = song.patterns[0].rows[0]
    assert row.note == constants.note_value("C-4")
    assert row.instrument == 1
    assert song.patterns[1].rows[0].note == REST
    instr = song.instruments[0]
    assert instr.attack_decay == 0x09
    assert instr.wave_ptr == 1


def test_minimal_wavetable_reversed(packed):
    song = sid.decompile_sid(packed).song
    # right column note byte reverses via ^0x80; the jump row is preserved.
    assert song.wavetable.left == [0x41, 0xFF]
    assert song.wavetable.right == [0x80, 0x01]


def test_orderlist_patterns(packed):
    song = sid.decompile_sid(packed).song
    channels = song.subtunes[0].channels
    assert channels[0].entries[0].num == 0
    assert channels[1].entries[0].num == 1


def test_roundtrips_through_writer(packed):
    song = sid.decompile_sid(packed).song
    reparsed = parse_sng(build_sng(song))
    assert len(reparsed.patterns) == len(song.patterns)
    assert len(reparsed.instruments) == len(song.instruments)
    assert reparsed.wavetable.left == song.wavetable.left


def test_pattern_count_ignores_trailing_bytes():
    # Bytes trailing the pattern block (e.g. author info, or an unbounded
    # decrunched image) must not be decoded as extra patterns.
    junk = bytes([0x01, 0x60, 0x00] * 8)  # would parse as spurious patterns
    song = sid.decompile_sid(_minimal_packed(trailer=junk)).song
    assert len(song.patterns) == 2


def test_count_patterns_from_pointer_table():
    # patttbl with two entries; a valid third pattern follows but is not
    # pointed to, so the count is 2.
    mem = bytearray(0x40)
    patt_lo = 0x00
    first = 0x10
    mem[patt_lo + 0] = first  # patttbllo[0]
    mem[patt_lo + 1] = first + 4  # patttbllo[1]
    mem[patt_lo + 2] = 0x00  # patttblhi[0]
    mem[patt_lo + 3] = 0x00  # patttblhi[1]
    mem[first : first + 4] = bytes([0x60, 0x60, 0x60, 0x00])  # pattern 0
    mem[first + 4 : first + 8] = bytes([0x61, 0x61, 0x61, 0x00])  # pattern 1
    assert sid._count_patterns(mem, patt_lo, first, order_start=0x30) == 2


def test_no_frequency_table():
    # A PSID whose payload is not a GoatTracker player.
    blob = _psid(b"\xea" * 64, init=LOAD, play=LOAD)
    with pytest.raises(SidParseError):
        sid.decompile_sid(blob)


def test_read_sid_convenience(packed):
    song = sid.read_sid(packed)
    assert len(song.patterns) == 2


def test_decode_packed_pattern_grammar():
    # instrument change, FX+note, packed rest run, endmark.
    mem = bytearray([0x02, 0x43, 0x11, constants.note_value("E-4"), 0xFE, 0x00])
    rows, end = sid.decode_packed_pattern(mem, 0)
    assert end == 6
    assert rows[0].instrument == 2
    assert rows[0].command == 0x3
    assert rows[0].data == 0x11
    assert rows[0].note == constants.note_value("E-4")
    # 0xFE == packed rest of 2 rows, carrying the running command.
    assert len(rows) == 3
    assert rows[1].note == REST and rows[2].note == REST


def test_decode_orderlist_repeat_and_transpose():
    # transpose +3, pattern 5 repeated 3 extra times, endmark + restart 1.
    mem = bytearray([0xF3, 0x05, 0xD3, 0xFF, 0x01])
    olist, end = sid.decode_packed_orderlist(mem, 0)
    assert end == 5
    assert olist.restart == 1
    kinds = [type(e).__name__ for e in olist.entries]
    assert kinds == ["Transpose", "Repeat", "PlayPattern"]
    assert olist.entries[0].semitones == 3
    assert olist.entries[1].count == 3
    assert olist.entries[2].num == 5


def test_load_sid_explicit_load_address():
    header = bytearray(0x7C)
    header[0:4] = b"PSID"
    struct.pack_into(">H", header, 0x04, 2)
    struct.pack_into(">H", header, 0x06, 0x7C)
    struct.pack_into(">H", header, 0x08, 0x2000)  # explicit load address
    struct.pack_into(">H", header, 0x0E, 1)
    mem, _, load, end = sid.load_sid(bytes(header) + b"\x01\x02\x03")
    assert load == 0x2000
    assert mem[0x2000] == 0x01
    assert end == 0x2003


def test_load_sid_overrun():
    header = bytearray(0x7C)
    header[0:4] = b"PSID"
    struct.pack_into(">H", header, 0x04, 2)
    struct.pack_into(">H", header, 0x06, 0x7C)
    struct.pack_into(">H", header, 0x08, 0xFFFF)
    with pytest.raises(SidParseError, match="overruns"):
        sid.load_sid(bytes(header) + b"\x00" * 8)


def test_read_sid_from_path_and_file(tmp_path, packed):
    path = tmp_path / "tune.sid"
    path.write_bytes(packed)
    assert len(sid.read_sid(path).patterns) == 2
    assert len(sid.read_sid(str(path)).patterns) == 2
    with path.open("rb") as handle:
        assert len(sid.read_sid(handle).patterns) == 2


def test_read_sid_bad_type():
    with pytest.raises(TypeError):
        sid.read_sid(1234)


def test_cli_sid2sng(tmp_path, packed):
    from pygoattracker.cli import main

    sid_path = tmp_path / "tune.sid"
    out = tmp_path / "tune.sng"
    sid_path.write_bytes(packed)
    assert main(["sid2sng", str(sid_path), str(out)]) == 0
    reparsed = parse_sng(out.read_bytes())
    assert len(reparsed.patterns) == 2


def test_error_base_is_sid_error():
    # The pygoattracker error hierarchy is re-parented onto pysidtracker's.
    assert issubclass(GoatTrackerError, SidError)
    assert issubclass(SidParseError, SidError)


def test_parser_detects_direct_load(packed):
    detection = sid.GoatTrackerSidParser().detect(packed)
    assert detection.kind is PlayroutineKind.DIRECT
    assert detection.trustworthy_header
    assert not detection.ran_init
    assert detection.anchor  # the frequency-table anchor tuple


def test_parser_parse_returns_song(packed):
    song = sid.GoatTrackerSidParser().parse(packed)
    assert len(song.patterns) == 2


# --- finetuned frequency table (stock high column, retuned low column) --------


def _retuned_low():
    # A low column that differs from the stock table so the exact both-column
    # anchor misses, but the high column is left stock so the fallback finds it.
    return tuple((b + 3) & 0xFF for b in constants.FREQ_LO)


def test_finetuned_freqtable_split_anchor_misses():
    from pysidtracker import SidImage

    image = SidImage.from_sid(_minimal_packed(freq_lo=_retuned_low()))
    # exact both-column match fails on the retuned low bytes ...
    assert (
        image.find_split_table(constants.FREQ_LO, constants.FREQ_HI, min_length=8)
        is None
    )
    # ... but the high-column fallback recovers the anchor.
    assert sid._find_freq_hi_anchor(image) is not None


def test_finetuned_freqtable_decompiles():
    song = sid.decompile_sid(_minimal_packed(freq_lo=_retuned_low())).song
    assert len(song.patterns) == 2
    assert song.patterns[0].rows[0].note == constants.note_value("C-4")


def test_finetuned_freqtable_recovers_retuned_low_bytes():
    lo = _retuned_low()
    result = sid.decompile_sid(_minimal_packed(freq_lo=lo))
    # The reported frequency table reflects the retuned low bytes read from the
    # image, not the stock table.
    freq = result.info.freq_table
    assert (freq[0] & 0xFF) == lo[0]


def test_parser_detects_finetuned_as_direct():
    detection = sid.GoatTrackerSidParser().detect(
        _minimal_packed(freq_lo=_retuned_low())
    )
    assert detection.kind is PlayroutineKind.DIRECT


# --- subtune count recovered from the packed layout, not the header -----------


def test_candidate_song_counts_prefers_header():
    class H:
        songs = 4

    counts = sid._candidate_song_counts(H())
    assert counts[0] == 4
    assert set(counts) == set(range(1, constants.MAX_SONGS + 1))


def test_candidate_song_counts_skips_implausible_header():
    class H:
        songs = 99  # out of range, must not be tried first

    counts = sid._candidate_song_counts(H())
    assert counts[0] == 1
    assert 99 not in counts


def test_subtune_count_derived_when_header_wrong():
    # Header advertises 2 subtunes but the data holds 1; decode must fall back
    # to the count the packed layout actually supports.
    song = sid.decompile_sid(_minimal_packed(songs=2)).song
    assert len(song.subtunes) == 1
    assert len(song.patterns) == 2


# --- code-scan layout: song/pattern tables and instrument/table region are ----
# --- located from the player's own table-access instructions ------------------


def _img_from_data(data: bytes):
    from pysidtracker import SidImage

    return SidImage.from_sid(_psid(data))


def _le(addr):
    return bytes([addr & 0xFF, addr >> 8])


def test_layout_candidates_reads_song_table_from_code():
    # A player-code sequencer idiom (LDA songtbllo,Y / STA zp / LDA songtblhi,Y /
    # STA zp / LDY chn,X / LDA (zp),Y / CMP #LOOPSONG) pins the song(order)-table
    # base and subtune count regardless of where the song data actually sits.
    slo, shi = 0x2000, 0x2009  # songs = (0x2009 - 0x2000)/3 = 3
    idiom = (
        b"\xb9" + _le(slo) + b"\x85\x00"
        b"\xb9" + _le(shi) + b"\x85\x00"
        b"\xbc\x00\x00\xb1\x00\xc9\xff"
    )
    image = _img_from_data(idiom)
    cands = sid._layout_candidates(image, image.header, (LOAD, 0, 1))
    assert (slo, 3, slo + 6 * 3) in cands
    # the stock "right after the frequency table" hypothesis is still tried first
    assert cands[0][0] == LOAD + 2 * 1


def test_layout_candidates_ignores_implausible_song_idiom():
    # A song idiom whose (hi - lo) is not a multiple of 3, or implies too many
    # subtunes, is not offered as a candidate.
    slo, shi = 0x2000, 0x2001  # (1)/3 -> not a whole subtune count
    idiom = (
        b"\xb9" + _le(slo) + b"\x85\x00"
        b"\xb9" + _le(shi) + b"\x85\x00"
        b"\xbc\x00\x00\xb1\x00\xc9\xff"
    )
    image = _img_from_data(idiom)
    cands = sid._layout_candidates(image, image.header, (LOAD, 0, 1))
    assert all(c[0] != slo for c in cands)


def _packed_needing_chain():
    """Image whose instrument/table region is located only from player code.

    Lays out a single-instrument, one-row-wavetable region (no pulse/filter/
    vibrato/gate arrays), plus the wave "nextstep" table-access idiom whose
    operands point at the wavetable columns, so :func:`sid._chain_layout` can
    read the geometry. Returns ``(image, insad, order_start)``.
    """
    mem = bytearray(0x80)
    # Wave table-access idiom near the start (code area): LDA wavetbl,Y ; CMP
    # #LOOPWAVE ; INY ; TYA ; BCC ; ... ; LDA notetbl-1,Y.
    insad = LOAD + 0x30
    wavetbl = insad + 3  # ad, sr, waveptr (K = 3, no optional arrays)
    notetbl = wavetbl + 1  # wlen = 1
    idiom = b"\xb9" + _le(wavetbl) + b"\xc9\xff\xc8\x98\x90\x00" b"\xb9" + _le(
        notetbl - 1
    )
    mem[0 : len(idiom)] = idiom
    off = 0x30
    mem[off : off + 3] = bytes([0x09, 0x00, 0x01])  # insad, inssr, inswaveptr=1
    mem[off + 3] = 0xFF  # wavetbl left: jump row (executed length 1)
    mem[off + 4] = 0x00  # notetbl right
    order_start = wavetbl + 2  # tables end here (speed table absent)
    image = _img_from_data(bytes(mem))
    return image, insad, order_start


def test_chain_layout_reads_tables_from_code():
    image, insad, order_start = _packed_needing_chain()
    eps = (set(), set(), set(), set())
    sol = sid._chain_layout(image, image.mem, insad, 1, order_start, eps)
    assert sol is not None
    assert sol["tstart"] == insad + 3
    assert (sol["wlen"], sol["plen"], sol["flen"], sol["slen"]) == (1, 0, 0, 0)
    assert (sol["pulse"], sol["filt"], sol["vib"], sol["gate"]) == (0, 0, 0, 0)


def test_chain_layout_returns_none_without_table_idiom():
    # No table-access idiom in the image -> nothing to locate; caller falls back.
    image = _img_from_data(b"\x00" * 0x40)
    eps = (set(), set(), set(), set())
    assert sid._chain_layout(image, image.mem, LOAD + 0x10, 1, LOAD + 0x20, eps) is None


# --- orderlist decode is bounded (a wrong subtune-count guess lands here) -----


def test_decode_orderlist_without_terminator_raises():
    mem = bytearray([0x00] * 4096)  # no 0xFF LOOPSONG marker anywhere
    with pytest.raises(ValueError, match="terminator"):
        sid.decode_packed_orderlist(mem, 0)


def test_decompile_reports_trusted_count_error(monkeypatch):
    # The trusted (header.songs) attempt fails with its own cause; later
    # candidate counts fail with a different, misleading cause. The raised
    # error must attribute the trusted-count cause, not the last candidate's.
    class H:
        songs = 3

    class Img:
        # Empty image: no player-code song-table idiom matches, so the only
        # layout candidates are the stock-layout subtune-count guesses.
        mem = bytearray(0x10000)
        load = LOAD
        end = 0x1000

        def __bytes__(self):
            return bytes(self.mem)

    def fake_decompile(
        _image, header, _dataend, _anchor, _subtune, _songtbl, songs, _patt_lo
    ):
        if songs == header.songs:
            raise SidParseError("could not segment packed instruments/tables")
        raise SidParseError("orderlist pointer out of range")

    monkeypatch.setattr(sid, "_decompile", fake_decompile)
    with pytest.raises(SidParseError, match="segment packed instruments"):
        sid._decompile_any_songs(Img(), H(), 0x1000, (LOAD, 0, 1), 0)


REAL_SID = os.environ.get("PYGOATTRACKER_SID")


@pytest.mark.skipif(not REAL_SID, reason="set PYGOATTRACKER_SID to a packed GT .sid")
def test_real_sid_roundtrips():
    song = sid.read_sid(REAL_SID)
    assert song.patterns
    parse_sng(build_sng(song))  # recovered song is a valid .SNG
