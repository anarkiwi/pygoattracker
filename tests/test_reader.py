"""Reader error handling."""

import pytest

from pygoattracker.errors import SngParseError
from pygoattracker.reader import parse_sng, read_sng
from pygoattracker.writer import build_sng

from tests.conftest import basic_song


@pytest.fixture(name="data")
def data_fixture() -> bytes:
    return build_sng(basic_song())


def test_bad_magic(data):
    with pytest.raises(SngParseError, match="identifier"):
        parse_sng(b"GTI5" + data[4:])


def test_empty_file():
    with pytest.raises(SngParseError, match="GoatTracker"):
        parse_sng(b"")


@pytest.mark.parametrize("fraction", [0.1, 0.5, 0.9])
def test_truncated_everywhere(data, fraction):
    with pytest.raises(SngParseError, match="truncated"):
        parse_sng(data[: int(len(data) * fraction)])


def test_truncated_by_one(data):
    with pytest.raises(SngParseError, match="truncated"):
        parse_sng(data[:-1])


def test_trailing_garbage(data):
    with pytest.raises(SngParseError, match="trailing"):
        parse_sng(data + b"\0")


def test_bad_subtune_count(data):
    mangled = bytearray(data)
    mangled[100] = 0
    with pytest.raises(SngParseError, match="subtune count"):
        parse_sng(bytes(mangled))


def test_zero_length_orderlist(data):
    mangled = bytearray(data)
    mangled[101] = 0
    with pytest.raises(SngParseError, match="zero length"):
        parse_sng(bytes(mangled))


def test_missing_rst_endmark(data):
    mangled = bytearray(data)
    assert mangled[103] == 0xFF
    mangled[103] = 0x00
    with pytest.raises(SngParseError, match="RST endmark"):
        parse_sng(bytes(mangled))


def test_bad_orderlist_entry():
    song = basic_song()
    data = bytearray(build_sng(song))
    # Lengthen channel 1's orderlist so the entry byte is the endmark
    # value, which is not decodable as an entry.
    assert data[101] == 2
    with pytest.raises(SngParseError, match="orderlist"):
        parse_sng(bytes(data[:102] + b"\xff\xff\x00" + data[105:]))


def test_bad_pattern_end_marker(data):
    mangled = bytearray(data)
    mangled[-4] = 0x00
    with pytest.raises(SngParseError, match="end marker"):
        parse_sng(bytes(mangled))


def test_bad_pattern_length(data):
    mangled = bytearray(data)
    # The last pattern length byte precedes the 9 stored 4-byte rows.
    pattern_length_at = len(data) - (8 + 1) * 4 - 1
    assert mangled[pattern_length_at] == 9
    mangled[pattern_length_at] = 0
    with pytest.raises(SngParseError, match="length"):
        parse_sng(bytes(mangled))


def test_read_rejects_unknown_type():
    with pytest.raises(TypeError):
        read_sng(12345)


def test_bad_instrument_count(data):
    mangled = bytearray(data)
    # The instrument count byte follows the three orderlists.
    assert mangled[113] == 1
    mangled[113] = 0xFF
    with pytest.raises(SngParseError, match="instrument count"):
        parse_sng(bytes(mangled))


def test_bad_pattern_count():
    full = bytearray(build_sng(basic_song()))
    # Truncate to just past the tables and claim zero patterns.
    pattern_count_at = len(full) - (8 + 1) * 4 - 2
    assert full[pattern_count_at] == 1
    full[pattern_count_at] = 0
    with pytest.raises(SngParseError, match="pattern count"):
        parse_sng(bytes(full[: pattern_count_at + 1]))
