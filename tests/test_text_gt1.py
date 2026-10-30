"""The V1.x song grammar (gt1.lark): every V1.x song prints and parses back to itself."""

import itertools

import pytest

from pygoattracker import cli, text
from pygoattracker import gt1typed as G
from pygoattracker.errors import SngParseError, SngValidationError
from pygoattracker.gt1 import (
    SEGMENTS_V11,
    SEGMENTS_V15,
    Gt1Instrument,
    Gt1Song,
    decompile_gt1,
)
from pygoattracker.gt1sng import (
    LAYOUT_V093,
    LAYOUT_V125,
    LAYOUT_V153,
    Gt1Sng,
    parse_gts1,
    relocate,
    write_gts1,
)
from pygoattracker.typed import Rest, Row

from tests._fixture_cache import (
    GOATOLD_SONGS,
    GOATTRK093_SONGS,
    GOATTRK_SONGS,
    goatold_song,
    goattrk093_song,
    goattrk_song,
)
from tests.test_gt1 import (
    M11_PLAYERS,
    V14_PLAYERS,
    V15_PLAYERS,
    make_song,
    make_song11,
    make_song14,
)

ALL = range(256)
EDGES = (0x00, 0x01, 0x5F, 0x60, 0x7F, 0x80, 0x81, 0xFE, 0xFF)


def _round_trip(song: Gt1Song) -> str:
    """The song's text, checking it parses back to the typed song and the song."""
    typed = G.Gt1TypedSong.from_song(song)
    out = text.dumps(typed)
    assert text.loads(out) == typed
    assert typed.to_song(song) == song
    return out


def _chunks(rows: bytes, size: int, end: bytes) -> list:
    return [rows[k : k + size] + end for k in range(0, len(rows), size)]


def _byte_space_song(player: str, layout: str | None) -> Gt1Song:
    """A song holding every value of every column the form stores."""
    editor = layout is not None
    rows3 = bytes(
        b
        for note, fx in itertools.product(range(0xFF if editor else 0x60), ALL)
        for b in (note, fx, (note * 7 + fx) & 0xFF)
    )
    if editor:
        patterns = _chunks(rows3, 3 * 128, b"\xff\x00\x00") + [b"\xff\x12\x34"]
    else:
        single = bytes(range(0x60, 0xFF))
        patterns = _chunks(rows3 + single, 240, b"\xff") + [
            b"\x30\x41\x00\xc0\xff\x99",
            b"",
            b"\x30\x41",
            b"\xbf\x30",
        ]
    no_restart = layout == LAYOUT_V093 or (not editor and player == "1.1")
    if no_restart:
        ends = (b"\xfe", b"\xff")
    else:
        ends = tuple(bytes((0xFF, r)) for r in (0, 1, 0xFF)) + (
            (b"\xfe",) if editor else ()
        )
    body = bytes(b for b in range(0xFE) if b != 0xFF)
    orders = [
        body[k : k + 17] + end
        for k, end in zip(range(0, 0xFE, 17), itertools.cycle(ends))
    ]
    orders += ends
    names = (b"song \x00\xe9", b"me", b"") if editor else (b"",) * 3
    instruments = [
        Gt1Instrument(
            *((n * 37 + k * 11) & 0xFF for k in range(8)),
            name=(b"i%d\x00x" % n).ljust(16, b"\0") if editor else b"",
        )
        for n in range(31)
    ]
    filt = bytes(b for c in ALL for b in (c, c ^ 0x55, 255 - c, c >> 2))
    return Gt1Song(
        player=player,
        address=0x1000,
        params=(4, 2, 0x0F, 0x00),
        instruments=instruments,
        wavetable=list(itertools.product(ALL, EDGES)),
        filtertable=filt + (b"\x01\x02" if not editor else b""),
        orderlists=[orders[k : k + 3] for k in range(0, len(orders) - 2, 3)],
        patterns=patterns,
        segments=SEGMENTS_V11 if G.family(player) == "11" else SEGMENTS_V15,
        sng=Gt1Sng(*names, layout) if editor else None,
    )


@pytest.mark.parametrize(
    "player,layout",
    [
        ("1.53", None),
        ("1.50", None),
        ("1.4b", None),
        ("11.3", None),
        ("1.1", None),
        ("1.53", LAYOUT_V153),
        ("11.3", LAYOUT_V125),
        ("0.93", LAYOUT_V093),
    ],
)
def test_every_byte_of_every_column_prints_and_parses_back(player, layout):
    _round_trip(_byte_space_song(player, layout))


def test_the_grammar_names_every_typed_constructor():
    grammar = text.parser("gt1.lark").rules
    names = {rule.origin.name for rule in grammar} | {
        rule.alias for rule in grammar if rule.alias
    }
    assert (
        set(text._Gt1Build.classes)
        - {  # pylint: disable=protected-access
            "gt1typed_song",
            "pattern",
            "orderlist",
            "instrument",
        }
        <= names
    )


def test_family_and_release_read_bytes_as_their_playroutine_does():
    assert G.entry_of(0xEE, True, G.transup("1.53")) == G.Transpose(-2)
    assert G.entry_of(0xEE, True, G.transup("1.50")) == G.Transpose(-1)
    assert G.wave_row(0x03, 0x80, delays=True) == G.WaveStep(
        G.Delay(3), G.WaveNote(0, False)
    )
    assert G.wave_row(0x03, 0x80) == G.WaveStep(3, G.WaveNote(0, False))
    assert G.wave_row(0x00, 0x05).wave is None


def test_each_family_reads_command_numbers_as_its_own_commands():
    assert G.commands("1.53")[1:3] == (G.PortamentoUp, G.PortamentoDown)
    assert G.commands("1.4b")[5] is G.SetFilter
    assert G.commands("11.1")[1:3] == (G.FreqSlide, G.CutoffSlide)
    assert G.commands("0.93") is G.MUSICROUTINE


@pytest.mark.parametrize(
    "make,players",
    [(make_song, V15_PLAYERS), (make_song11, M11_PLAYERS), (make_song14, V14_PLAYERS)],
)
def test_each_playroutine_s_packed_song(make, players):
    for player in players:
        _round_trip(make(player))


@pytest.mark.parametrize("name", sorted(GOATOLD_SONGS))
def test_v125_editor_song_and_its_relocation(name):
    data = goatold_song(name)
    song = parse_gts1(data)
    out = _round_trip(song)
    assert write_gts1(text.loads(out).to_song()) == data
    _round_trip(relocate(song))


@pytest.mark.parametrize("name", sorted(GOATTRK_SONGS))
def test_v153_editor_song_and_its_relocation(name):
    data = goattrk_song(name)
    song = parse_gts1(data)
    assert write_gts1(text.loads(_round_trip(song)).to_song()) == data
    _round_trip(relocate(song))


@pytest.mark.parametrize("name", sorted(GOATTRK093_SONGS))
def test_v093_editor_song(name):
    data = goattrk093_song(name)
    assert write_gts1(text.loads(_round_trip(parse_gts1(data))).to_song()) == data


def test_a_hand_written_packed_song_plays_as_it_reads():
    song = text.loads("""
        player "1.53" packed params $04 $02 $0F $00
        subtune 0
          orderlist Transpose(-2) Repeat(1) PlayPattern(0) Loop(0)
          orderlist PlayPattern(0) Loop(0)
          orderlist PlayPattern(0) Loop(0)
        instrument 1 "" ad $09 sr 0 pulse $08 speed $20 low $01 high $0E filter 0 wave 1
        wavetable
          1: WaveStep($41, WaveNote(0, relative))
          2: Stop
        filtertable
          0: FilterSet($F1, $1F, $40, 4)
          1: FilterSlide(16, 1, 0)
        pattern 0
          C-4 1 Vibrato($24)
          D-4
          ... *4
          --- SetTempo(5)
        """)
    assert song.patterns[0].rows[2] == Row(Rest(), None, None, 4)
    raw = song.to_song()
    assert raw.patterns[0] == bytes((0x30, 0x0C, 0x24, 0x92, 0xFC, 0x5E, 0x07, 5, 0xFF))
    assert raw.orderlists[0][0] == bytes((0xEE, 0xD1, 0x00, 0xFF, 0x00))
    sid = raw.to_sid()
    assert G.Gt1TypedSong.from_song(decompile_gt1(sid)) == song


@pytest.mark.parametrize(
    "src,match",
    [
        (
            'player "1.53" packed params subtune 0 orderlist Loop(0) orderlist '
            "Loop(0) orderlist Loop(0) wavetable filtertable 1: FilterSlide(1, 1, 0) "
            "pattern 0 ...",
            "filtertable step 1 where 0 is due",
        ),
        ('player "1.53" packed params subtune 0', "Unexpected"),
    ],
)
def test_malformed_text_is_a_parse_error(src, match):
    with pytest.raises(SngParseError, match=match):
        text.loads(src)


_GT1_SONG = (
    'player "{player}" {form} params '
    "subtune 0 orderlist {order} orderlist Loop(0) orderlist Loop(0) "
    "wavetable {wave} pattern 0 {row}"
)
_EDITOR = 'editor "1.4b-V1.53" name "" author "" copyright ""'


@pytest.mark.parametrize(
    "fill,match",
    [
        (
            {"player": "11.3", "row": "C-4 PortamentoUp(1)"},
            r"PortamentoUp\(data=1\), length=1\) reads back as .*FreqSlide",
        ),
        (
            {"player": "1.4b", "wave": "1: WaveStep(Delay(3), WaveNote(0))"},
            "reads back as",
        ),
        (
            {"wave": "1: WaveStep(3, WaveNote(0))"},
            r"reads back as WaveStep\(wave=Delay",
        ),
        ({"wave": "1: WaveStep($100, WaveNote(0))"}, "has no bytes"),
        ({"wave": "1: Jump(0)"}, "reads back as Stop"),
        ({"row": "C-4 Arpeggio($100)"}, "has no bytes"),
        ({"row": "C-4 $20 Arpeggio(0)"}, "has no bytes"),
        ({"row": "... *$41"}, "reads back as"),
        ({"form": _EDITOR, "row": "... Arpeggio(0) *2"}, "reads back as"),
        ({"form": _EDITOR, "row": "C-4"}, "needs a command"),
        ({"order": "Transpose(15) Loop(0)"}, "std_loop ends after 2 of its 3 bytes"),
        ({"order": "PlayPattern(0) End($FE)"}, "std_loop has no terminator"),
        (
            {"player": "1.1", "form": "packed", "order": "PlayPattern($FE) End($FF)"},
            "m11_end ends after 1 of its 2 bytes",
        ),
    ],
)
def test_a_value_that_does_not_fit_its_field_is_refused(fill, match):
    values = {"player": "1.53", "form": "packed", "order": "PlayPattern(0) Loop(0)"}
    values.update({"wave": "", "row": "..."}, **fill)
    with pytest.raises(SngValidationError, match=match):
        text.loads(_GT1_SONG.format(**values))


def test_bytes_the_typed_form_cannot_state():
    with pytest.raises(SngParseError, match=r"unexpected byte \$ff at offset 0"):
        G.Gt1TypedSong.from_song(make_song(patterns=[b"\xff\xff"]))
    with pytest.raises(SngParseError, match="std_loop has no terminator"):
        G.Gt1TypedSong.from_song(make_song(orderlists=[[b"\x00\x01"] * 3]))
    held = G.Pattern((Row(Rest(), 3),))
    with pytest.raises(SngParseError, match="needs a command"):
        G.Gt1TypedSong("1.53", patterns=(held,)).to_song()


def test_cli_text_and_back_for_v1x(tmp_path, capsys):
    data = goattrk_song(sorted(GOATTRK_SONGS)[0])
    sng, txt, out = tmp_path / "a.sng", tmp_path / "a.txt", tmp_path / "b.sng"
    sng.write_bytes(data)
    assert cli.main(["text", str(sng), str(txt)]) == 0
    assert cli.main(["fromtext", str(txt), str(out)]) == 0
    assert out.read_bytes() == data
    sid = tmp_path / "c.sid"
    sid.write_bytes(make_song11("11.3").to_sid())
    assert cli.main(["text", str(sid)]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith('player "11.3"\npacked')
    txt.write_text(printed)
    assert cli.main(["fromtext", str(txt), str(out)]) == 0
    assert decompile_gt1(out.read_bytes()).patterns == make_song11("11.3").patterns


def test_cli_grammar_lists_every_stream_s_parse_tree(tmp_path, capsys):
    sid = tmp_path / "c.sid"
    sid.write_bytes(make_song11("11.3").to_sid())
    assert cli.main(["grammar", str(sid)]) == 0
    out = capsys.readouterr().out
    assert "subtune 0 orderlist 0:\n  m11_loop PATTERN_ANY=$00" in out
    assert "pattern 0:\n  pattern\n    first_row" in out
    sng = tmp_path / "a.sng"
    sng.write_bytes(goattrk_song(sorted(GOATTRK_SONGS)[0]))
    assert cli.main(["grammar", str(sng)]) == 0
    assert "editor_pattern" in capsys.readouterr().out
