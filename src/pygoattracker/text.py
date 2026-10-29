"""Songs as text in the grammars ``song.lark`` (GT2) and ``gt1.lark`` (V1.x).

``loads(dumps(song)) == song`` for every :class:`~pygoattracker.typed.TypedSong`
and :class:`~pygoattracker.gt1typed.Gt1TypedSong`, so each grammar states
everything its song files and packed player data hold.
"""

import dataclasses
import json
import re
from functools import cache
from importlib.resources import files

from lark import Lark, Token, Transformer
from lark.exceptions import LarkError, VisitError

from pygoattracker import constants as C
from pygoattracker import gt1typed, model, typed
from pygoattracker.errors import SngParseError


def _classes(module, *extra) -> dict:
    """Each dataclass of ``module`` (and ``extra``) by its grammar rule name."""
    own = (
        v
        for v in vars(module).values()
        if dataclasses.is_dataclass(v) and v.__module__ == module.__name__
    )
    return {
        re.sub(r"(?<=[a-z])(?=[A-Z])", "_", cls.__name__).lower(): cls
        for cls in (*own, *extra)
    }


_ORDER = (model.PlayPattern, model.Repeat, model.Transpose)
_NOTES = range(C.LASTNOTE - C.FIRSTNOTE + 1)
_GT1 = re.compile(r"(\s|#[^\n]*)*player\b")


@cache
def parser(grammar: str = "song.lark") -> Lark:
    """The LALR parser of a grammar shipped with the package."""
    return Lark(files("pygoattracker").joinpath(grammar).read_text(), parser="lalr")


def _seq(items, base: int, what: str) -> tuple:
    """The values of ``(index, value)`` items, checking they count up from ``base``."""
    for want, (got, _) in enumerate(items, base):
        if got != want:
            raise SngParseError(f"{what} {got} where {want} is due")
    return tuple(value for _, value in items)


def _parts(children) -> dict:
    """``(kind, item)`` children grouped by kind."""
    out = {}
    for kind, item in children:
        out.setdefault(kind, []).append(item)
    return out


class _Build(Transformer):
    """Each constructor rule to its dataclass; terminals and rows as both grammars share them."""

    # pylint: disable=missing-function-docstring,invalid-name
    classes: dict = {}

    def __default__(self, data, children, _meta):
        cls = self.classes[data]
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
    classes = _classes(typed, *_ORDER)

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
    classes = _classes(
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


def loads(text: str):
    """Parse song text: a :class:`~pygoattracker.gt1typed.Gt1TypedSong` when it
    opens with ``player``, else a :class:`~pygoattracker.typed.TypedSong`.

    :raises SngParseError: if the text is not in the grammar.
    :raises SngValidationError: if a value does not fit its field.
    """
    gt1 = _GT1.match(text) is not None
    lalr = parser("gt1.lark" if gt1 else "song.lark")
    try:
        song = (_Gt1Build if gt1 else _Gt2Build)().transform(lalr.parse(text))
    except VisitError as exc:
        raise SngParseError(str(exc.orig_exc)) from exc.orig_exc
    except LarkError as exc:
        raise SngParseError(str(exc)) from exc
    song.check()
    return song


def _int(value: int) -> str:
    return f"-${-value:02X}" if value < 0 else f"${value:02X}"


def _ints(values) -> str:
    return " ".join(_int(v) for v in values)


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
        out.append(f"\nsubtune {_int(num)}")
        out += [
            "  orderlist " + " ".join(_term(e) for e in (*o.entries, o.end))
            for o in chans
        ]
    out.append("")
    out += [
        f"instrument {_int(n)} {json.dumps(i.name)} ad {_int(i.attack_decay)}"
        f" sr {_int(i.sustain_release)} pulse {_int(i.pulse)}"
        f" speed {_int(i.pulse_speed)} low {_int(i.pulse_low)}"
        f" high {_int(i.pulse_high)} filter {_int(i.filter)} wave {_int(i.wave_ptr)}"
        for n, i in enumerate(song.instruments, 1)
    ]
    out.append("\nwavetable")
    out += [f"  {_int(n)}: {_term(r)}" for n, r in enumerate(song.wavetable, 1)]
    if song.filtertable or song.filtertail:
        out.append("\nfiltertable")
        out += [f"  {_int(n)}: {_term(s)}" for n, s in enumerate(song.filtertable)]
        if song.filtertail:
            out.append(f"  tail {_ints(song.filtertail)}")
    default = b"\xff\x00\x00" if song.editor else b"\xff"
    for num, pattern in enumerate(song.patterns):
        out.append(f"\npattern {_int(num)}")
        out += [f"  {_row(r)}" for r in pattern.rows]
        if pattern.end != default:
            out.append(f"  end {_ints(pattern.end)}".rstrip())
    return out


def dumps(song) -> str:
    """Print a typed song in its grammar (``gt1.lark`` for V1.x, else ``song.lark``)."""
    gt1 = isinstance(song, gt1typed.Gt1TypedSong)
    return "\n".join(_dumps_gt1(song) if gt1 else _dumps_gt2(song)) + "\n"
