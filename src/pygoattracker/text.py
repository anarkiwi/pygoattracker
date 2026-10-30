"""Songs as text in the grammars ``song.lark`` (GT2) and ``gt1.lark`` (V1.x).

``loads(dumps(song)) == song`` for every :class:`~pygoattracker.typed.TypedSong`
and :class:`~pygoattracker.gt1typed.Gt1TypedSong`, so each grammar states
everything its song files and packed player data hold.
"""

import json
import re
from functools import partial

from pysidtracker.textgrammar import (
    ModelTransformer,
    TextGrammar,
    classes as rule_classes,
    hexint,
    seq,
    term,
)

from pygoattracker import constants as C
from pygoattracker import gt1typed, model, typed
from pygoattracker.errors import SngParseError

_ORDER = (model.PlayPattern, model.Repeat, model.Transpose)
_NOTES = range(C.LASTNOTE - C.FIRSTNOTE + 1)
_GT1 = re.compile(r"(\s|#[^\n]*)*player\b")


_seq = partial(seq, error=SngParseError)


def _parts(children) -> dict:
    """``(kind, item)`` children grouped by kind."""
    out = {}
    for kind, item in children:
        out.setdefault(kind, []).append(item)
    return out


class _Build(ModelTransformer):
    """Rows and table entries as both grammars share them."""

    # pylint: disable=missing-function-docstring

    def note_name(self, children):
        return typed.Note(C.note_value(children[0]) - C.FIRSTNOTE)

    def length(self, children):
        return ("*", children[0])

    def row(self, children):
        note, ins, cmd, length = children[0], None, None, 1
        for c in children[1:]:
            if isinstance(c, int):
                ins = c
            elif isinstance(c, tuple):
                length = c[1]
            else:
                cmd = c
        return typed.Row(note, ins, cmd, length)

    def _entry(self, children):
        return tuple(children)

    wave_entry = pulse_entry = filter_entry = speed_entry = _entry


class _Gt2Build(_Build):
    """``song.lark`` to a :class:`~pygoattracker.typed.TypedSong`."""

    # pylint: disable=missing-function-docstring
    classes = rule_classes(typed, *_ORDER)

    def yes(self, _):
        return True

    def no(self, _):
        return False

    def pattern(self, children):
        return "pattern", (children[0], tuple(children[1:]))

    def orderlist(self, children):
        return model.Orderlist(list(children[:-1]), children[-1])

    def subtune(self, children):
        return "subtune", (children[0], model.Subtune(list(children[1:])))

    def instrument(self, children):
        num, name, *params, hard, legato, first = children
        ad, sr, wave, pulse, filt, vib, delay, gate = params
        ins = typed.Instrument(
            ad, sr, wave, pulse, filt, vib, delay, gate, hard, legato, first, name
        )
        return "instrument", (num, ins)

    def _table(self, children):
        return children

    wavetable = pulsetable = filtertable = speedtable = _table

    def simplepulse(self, _):
        return {"simplepulse": True}

    def tuning(self, children):
        return {"firstnote": children[0], "freq": tuple(children[1:])}

    def speed0(self, children):
        return {"speed0": children[0]}

    def header(self, children):
        name, author, copyright_, *opts = children
        out = {"name": name, "author": author, "copyright": copyright_}
        for opt in opts:
            out.update(opt)
        return out

    def start(self, children):
        tables = [c for c in children[1:] if isinstance(c, list)]
        parts = _parts(c for c in children[1:] if not isinstance(c, list))
        wave, pulse, filt, speed = (
            _seq(t, 1, f"{n}table row")
            for t, n in zip(tables, ("wave", "pulse", "filter", "speed"))
        )
        return typed.TypedSong(
            subtunes=_seq(parts["subtune"], 0, "subtune"),
            patterns=_seq(parts["pattern"], 0, "pattern"),
            instruments=_seq(parts.get("instrument", []), 1, "instrument"),
            wavetable=wave,
            pulsetable=pulse,
            filtertable=filt,
            speedtable=speed,
            **children[0],
        )


class _Gt1Build(_Build):
    """``gt1.lark`` to a :class:`~pygoattracker.gt1typed.Gt1TypedSong`."""

    # pylint: disable=missing-function-docstring
    classes = rule_classes(
        gt1typed,
        *_ORDER,
        typed.Note,
        typed.Rest,
        typed.KeyOff,
        typed.WaveNote,
        typed.Delay,
        typed.Jump,
        typed.Stop,
    )

    def packed(self, _):
        return {"layout": None}

    def editor(self, children):
        return dict(zip(("layout", "name", "author", "copyright"), children))

    def header(self, children):
        player, form, *params = children
        return {"player": player, **form, "params": tuple(params)}

    def subtune(self, children):
        return "subtune", (children[0], tuple(children[1:]))

    def orderlist(self, children):
        return gt1typed.Orderlist(tuple(children[:-1]), children[-1])

    def instrument(self, children):
        num, name, *params = children
        return "instrument", (num, gt1typed.Instrument(*params, name))

    def pattern_end(self, children):
        return ("end", bytes(children))

    def pattern(self, children):
        num, *rows = children
        end = rows.pop()[1] if rows and isinstance(rows[-1], tuple) else None
        return "pattern", (num, (tuple(rows), end))

    def wavetable(self, children):
        return "wavetable", children

    def filter_tail(self, children):
        return ("tail", bytes(children))

    def filtertable(self, children):
        tail = children.pop()[1] if children and children[-1][0] == "tail" else b""
        return "filtertable", (children, tail)

    def start(self, children):
        header, parts = children[0], _parts(children[1:])
        steps, tail = parts.get("filtertable", [([], b"")])[0]
        default = b"\xff\x00\x00" if header["layout"] is not None else b"\xff"
        patterns = tuple(
            gt1typed.Pattern(rows, default if end is None else end)
            for rows, end in _seq(parts["pattern"], 0, "pattern")
        )
        return gt1typed.Gt1TypedSong(
            subtunes=_seq(parts["subtune"], 0, "subtune"),
            instruments=_seq(parts.get("instrument", []), 1, "instrument"),
            wavetable=_seq(parts["wavetable"][0], 1, "wavetable row"),
            filtertable=_seq(steps, 0, "filtertable step"),
            filtertail=tail,
            patterns=patterns,
            **header,
        )


_GRAMMARS = {
    name: TextGrammar("pygoattracker", name, build, SngParseError)
    for name, build in (("song.lark", _Gt2Build), ("gt1.lark", _Gt1Build))
}


def parser(grammar: str = "song.lark"):
    """The LALR parser of a grammar shipped with the package."""
    return _GRAMMARS[grammar].parser


def loads(text: str):
    """Parse song text: a :class:`~pygoattracker.gt1typed.Gt1TypedSong` when it
    opens with ``player``, else a :class:`~pygoattracker.typed.TypedSong`.

    :raises SngParseError: if the text is not in the grammar.
    :raises SngValidationError: if a value does not fit its field.
    """
    song = _GRAMMARS["gt1.lark" if _GT1.match(text) else "song.lark"].loads(text)
    song.check()
    return song


def _ints(values) -> str:
    return " ".join(hexint(v) for v in values)


def _note(note) -> str:
    if isinstance(note, typed.Gate) or (
        isinstance(note, typed.Note) and note.pitch not in _NOTES
    ):
        return term(note)
    return C.note_name(typed.note_byte(note))


def _row(row: typed.Row) -> str:
    parts = [_note(row.note)]
    if row.instrument is not None:
        parts.append(hexint(row.instrument))
    if row.command is not None:
        parts.append(term(row.command))
    if row.length != 1:
        parts.append(f"*{hexint(row.length)}")
    return " ".join(parts)


def _instrument(num: int, ins: typed.Instrument) -> str:
    return (
        f"instrument {hexint(num)} {json.dumps(ins.name)} ad {hexint(ins.attack_decay)}"
        f" sr {hexint(ins.sustain_release)} wave {hexint(ins.wave_ptr)}"
        f" pulse {hexint(ins.pulse_ptr)} filter {hexint(ins.filter_ptr)}"
        f" vibrato {hexint(ins.vibrato_speed)} delay {hexint(ins.vibrato_delay)}"
        f" gate {hexint(ins.gate_timer)}"
        f" {'hardrestart' if ins.hard_restart else 'nohardrestart'}"
        f"{' legato' if ins.legato else ''} first {term(ins.first_wave)}"
    )


def _dumps_gt2(song: typed.TypedSong) -> list:
    out = [
        f"name {json.dumps(song.name)}",
        f"author {json.dumps(song.author)}",
        f"copyright {json.dumps(song.copyright)}",
    ]
    if song.simplepulse:
        out.append("simplepulse")
    if song.freq or song.firstnote:
        out.append(
            f"firstnote {hexint(song.firstnote)} freq "
            + " ".join(f"${f:04X}" for f in song.freq)
        )
    if song.speed0 is not None:
        out.append(f"speed0 {term(song.speed0)}")
    for num, sub in enumerate(song.subtunes):
        out.append(f"\nsubtune {hexint(num)}")
        out += [
            f"  orderlist {' '.join(term(e) for e in o.entries)}"
            f" restart {hexint(o.restart)}"
            for o in sub.channels
        ]
    out.append("")
    out += [_instrument(n, i) for n, i in enumerate(song.instruments, 1)]
    for name in ("wavetable", "pulsetable", "filtertable", "speedtable"):
        out.append(f"\n{name}")
        out += [
            f"  {hexint(n)}: {term(r)}" for n, r in enumerate(getattr(song, name), 1)
        ]
    for num, rows in enumerate(song.patterns):
        out.append(f"\npattern {hexint(num)}")
        out += [f"  {_row(r)}" for r in rows]
    return out


def _dumps_gt1(song: gt1typed.Gt1TypedSong) -> list:
    out = [f"player {json.dumps(song.player)}"]
    if song.editor:
        out += [
            f"editor {json.dumps(song.layout)}",
            f"name {json.dumps(song.name)}",
            f"author {json.dumps(song.author)}",
            f"copyright {json.dumps(song.copyright)}",
        ]
    else:
        out.append("packed")
    out.append(f"params {_ints(song.params)}".rstrip())
    for num, chans in enumerate(song.subtunes):
        out.append(f"\nsubtune {hexint(num)}")
        out += [
            "  orderlist " + " ".join(term(e) for e in (*o.entries, o.end))
            for o in chans
        ]
    out.append("")
    out += [
        f"instrument {hexint(n)} {json.dumps(i.name)} ad {hexint(i.attack_decay)}"
        f" sr {hexint(i.sustain_release)} pulse {hexint(i.pulse)}"
        f" speed {hexint(i.pulse_speed)} low {hexint(i.pulse_low)}"
        f" high {hexint(i.pulse_high)} filter {hexint(i.filter)} wave {hexint(i.wave_ptr)}"
        for n, i in enumerate(song.instruments, 1)
    ]
    out.append("\nwavetable")
    out += [f"  {hexint(n)}: {term(r)}" for n, r in enumerate(song.wavetable, 1)]
    if song.filtertable or song.filtertail:
        out.append("\nfiltertable")
        out += [f"  {hexint(n)}: {term(s)}" for n, s in enumerate(song.filtertable)]
        if song.filtertail:
            out.append(f"  tail {_ints(song.filtertail)}")
    default = b"\xff\x00\x00" if song.editor else b"\xff"
    for num, pattern in enumerate(song.patterns):
        out.append(f"\npattern {hexint(num)}")
        out += [f"  {_row(r)}" for r in pattern.rows]
        if pattern.end != default:
            out.append(f"  end {_ints(pattern.end)}".rstrip())
    return out


def dumps(song) -> str:
    """Print a typed song in its grammar (``gt1.lark`` for V1.x, else ``song.lark``)."""
    gt1 = isinstance(song, gt1typed.Gt1TypedSong)
    return "\n".join(_dumps_gt1(song) if gt1 else _dumps_gt2(song)) + "\n"
