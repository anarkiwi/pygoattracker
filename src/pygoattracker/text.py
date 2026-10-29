"""A song as text in the grammar of ``song.lark``: parse and print a :class:`TypedSong`.

``loads(dumps(song)) == song`` for every :class:`~pygoattracker.typed.TypedSong`,
so the grammar states everything the ``.sng`` and the packed player data hold.
"""

import dataclasses
import json
import re
from functools import cache
from importlib.resources import files

from lark import Lark, Token, Transformer
from lark.exceptions import LarkError, VisitError

from pygoattracker import constants as C
from pygoattracker import model, typed
from pygoattracker.errors import SngParseError

_CLASSES = {
    re.sub(r"(?<=[a-z])(?=[A-Z])", "_", cls.__name__).lower(): cls
    for cls in (
        *(
            v
            for v in vars(typed).values()
            if dataclasses.is_dataclass(v) and v.__module__ == typed.__name__
        ),
        model.PlayPattern,
        model.Repeat,
        model.Transpose,
    )
}
_NOTES = range(C.LASTNOTE - C.FIRSTNOTE + 1)


@cache
def parser() -> Lark:
    """The LALR parser of ``song.lark``."""
    grammar = files("pygoattracker").joinpath("song.lark").read_text()
    return Lark(grammar, parser="lalr")


def _seq(items, base: int, what: str) -> tuple:
    """The values of ``(index, value)`` items, checking they count up from ``base``."""
    for want, (got, _) in enumerate(items, base):
        if got != want:
            raise SngParseError(f"{what} {got} where {want} is due")
    return tuple(value for _, value in items)


class _Build(Transformer):
    """Each constructor rule to its dataclass; the rest to the song's parts."""

    # pylint: disable=missing-function-docstring,invalid-name

    def __default__(self, data, children, _meta):
        cls = _CLASSES[data]
        given = {c.type.lower() for c in children if isinstance(c, Token)}
        flags = {
            f.name: f.name in given
            for f in dataclasses.fields(cls)
            if f.type in (bool, "bool")
        }
        return cls(*(c for c in children if not isinstance(c, Token)), **flags)

    def INT(self, tok):
        neg, digits = tok.startswith("-"), tok.lstrip("-")
        value = int(digits[1:], 16) if digits.startswith("$") else int(digits)
        return -value if neg else value

    def STRING(self, tok):
        return json.loads(tok)

    def none(self, _):
        return None

    def yes(self, _):
        return True

    def no(self, _):
        return False

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

    def _entry(self, children):
        return tuple(children)

    wave_entry = pulse_entry = filter_entry = speed_entry = _entry

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
        header, parts = children[0], {"subtune": [], "instrument": [], "pattern": []}
        tables = []
        for child in children[1:]:
            if isinstance(child, list):
                tables.append(child)
            else:
                parts[child[0]].append(child[1])
        wave, pulse, filt, speed = (
            _seq(t, 1, f"{n}table row")
            for t, n in zip(tables, ("wave", "pulse", "filter", "speed"))
        )
        return typed.TypedSong(
            subtunes=_seq(parts["subtune"], 0, "subtune"),
            patterns=_seq(parts["pattern"], 0, "pattern"),
            instruments=_seq(parts["instrument"], 1, "instrument"),
            wavetable=wave,
            pulsetable=pulse,
            filtertable=filt,
            speedtable=speed,
            **header,
        )


def loads(text: str) -> typed.TypedSong:
    """Parse song text into a :class:`~pygoattracker.typed.TypedSong`."""
    lalr = parser()
    try:
        return _Build().transform(lalr.parse(text))
    except VisitError as exc:
        raise SngParseError(str(exc.orig_exc)) from exc.orig_exc
    except LarkError as exc:
        raise SngParseError(str(exc)) from exc


def _int(value: int) -> str:
    return f"-${-value:02X}" if value < 0 else f"${value:02X}"


def _term(value) -> str:
    """A constructor term: ``Name``, or ``Name(arg, ...)`` with true flags by name."""
    if value is None:
        return "none"
    if isinstance(value, int):
        return _int(value)
    args = []
    for f in dataclasses.fields(value):
        arg = getattr(value, f.name)
        if isinstance(arg, bool):
            if arg:
                args.append(f.name)
        else:
            args.append(_term(arg))
    name = type(value).__name__
    return f"{name}({', '.join(args)})" if args else name


def _note(note) -> str:
    if isinstance(note, typed.Note):
        if note.pitch in _NOTES:
            return C.note_name(C.FIRSTNOTE + note.pitch)
        return _term(note)
    return C.note_name(typed.note_byte(note))


def _row(row: typed.Row) -> str:
    parts = [_note(row.note)]
    if row.instrument is not None:
        parts.append(_int(row.instrument))
    if row.command is not None:
        parts.append(_term(row.command))
    if row.length != 1:
        parts.append(f"*{_int(row.length)}")
    return " ".join(parts)


def _instrument(num: int, ins: typed.Instrument) -> str:
    return (
        f"instrument {_int(num)} {json.dumps(ins.name)} ad {_int(ins.attack_decay)}"
        f" sr {_int(ins.sustain_release)} wave {_int(ins.wave_ptr)}"
        f" pulse {_int(ins.pulse_ptr)} filter {_int(ins.filter_ptr)}"
        f" vibrato {_int(ins.vibrato_speed)} delay {_int(ins.vibrato_delay)}"
        f" gate {_int(ins.gate_timer)}"
        f" {'hardrestart' if ins.hard_restart else 'nohardrestart'}"
        f"{' legato' if ins.legato else ''} first {_term(ins.first_wave)}"
    )


def dumps(song: typed.TypedSong) -> str:
    """Print a :class:`~pygoattracker.typed.TypedSong` in the grammar of ``song.lark``."""
    out = [
        f"name {json.dumps(song.name)}",
        f"author {json.dumps(song.author)}",
        f"copyright {json.dumps(song.copyright)}",
    ]
    if song.simplepulse:
        out.append("simplepulse")
    if song.freq or song.firstnote:
        out.append(
            f"firstnote {_int(song.firstnote)} freq "
            + " ".join(f"${f:04X}" for f in song.freq)
        )
    if song.speed0 is not None:
        out.append(f"speed0 {_term(song.speed0)}")
    for num, sub in enumerate(song.subtunes):
        out.append(f"\nsubtune {_int(num)}")
        out += [
            f"  orderlist {' '.join(_term(e) for e in o.entries)}"
            f" restart {_int(o.restart)}"
            for o in sub.channels
        ]
    out.append("")
    out += [_instrument(n, i) for n, i in enumerate(song.instruments, 1)]
    for name in ("wavetable", "pulsetable", "filtertable", "speedtable"):
        out.append(f"\n{name}")
        out += [
            f"  {_int(n)}: {_term(r)}" for n, r in enumerate(getattr(song, name), 1)
        ]
    for num, rows in enumerate(song.patterns):
        out.append(f"\npattern {_int(num)}")
        out += [f"  {_row(r)}" for r in rows]
    return "\n".join(out) + "\n"
