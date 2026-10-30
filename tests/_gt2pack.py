"""A song packed with a GoatTracker 2 playroutine assembled at test time.

:func:`pack` lays the song's data out as :func:`~pygoattracker.songimage.song_image`
does and assembles a release's playroutine source after it with the image's
defines and data labels, so the spec and the 6502 run one memory image.
"""

from __future__ import annotations

from dataclasses import dataclass

from pysidtracker.header import write_psid
from pysidtracker.playspec import SpecPlayer

from pygoattracker import gt2spec
from pygoattracker.packed import Build
from pygoattracker.songimage import BASE, song_image
from tests._gt2asm import AssemblyError, assemble, referenced
from tests._gt2refs import sources

_DATA = (
    "mt_insad",
    "mt_inssr",
    "mt_inswaveptr",
    "mt_inspulseptr",
    "mt_insfiltptr",
    "mt_insvibparam",
    "mt_insvibdelay",
    "mt_insgatetimer",
    "mt_insfirstwave",
    "mt_wavetbl",
    "mt_notetbl",
    "mt_pulsetimetbl",
    "mt_pulsespdtbl",
    "mt_filttimetbl",
    "mt_filtspdtbl",
    "mt_speedlefttbl",
    "mt_speedrighttbl",
    "mt_freqtbllo",
    "mt_freqtblhi",
    "mt_songtbllo",
    "mt_songtblhi",
    "mt_patttbllo",
    "mt_patttblhi",
)
CODE = 0x8000
_ZP = 0xFC
_ZPGHOST = 0xE0
_JSR, _RTS = 0x20, 0x60
# Handlers a jump-table low byte selects, and the label whose page the jump keeps.
_DISPATCH = (
    (
        "mt_tick0_0",
        ("0", "12", "34", "5", "6", "7", "8", "9", "a", "b", "c", "d", "e", "f"),
    ),
    ("mt_effect_0", ("0", "12", "3", "4")),
)


@dataclass(frozen=True)
class Packed:
    """A packed song: its memory ``image`` at ``BASE`` (the playroutine at
    :data:`CODE`), the 6502 ``init`` and
    ``play`` (a frame's ``mt_play`` calls), the playroutine ``found``, the
    channels its play runs and the ``entries`` (its labelled instructions)."""

    image: bytes
    init: int
    play: int
    found: gt2spec.Match
    channels: tuple
    entries: tuple

    def sid(self) -> bytes:
        """The image as a PSID."""
        return write_psid(
            load=BASE, init=self.init, play=self.play, image=self.image, songs=2
        )

    def spec(self, subtune: int = 0) -> SpecPlayer:
        """The spec playing ``subtune``, bound to the assembled playroutine."""
        lay = gt2spec.layout(self.found, self.channels)
        return SpecPlayer(
            gt2spec.spec(),
            self.image,
            BASE,
            self.init,
            self.play,
            subtune,
            bind=lambda view, entry: lay,
            entries=lambda view: {self.found["mt_play"]},
        )


def pack(song, source: str, build: Build | None = None, **defines) -> Packed:
    """``song`` packed with playroutine ``source`` (a :func:`tests._gt2refs.sources`
    name), ``build``'s options and the relocator ``defines`` given."""
    build = build or Build()
    img = song_image(song, build)
    used = referenced(sources()[source][0])
    sym = img.found.symbols | defines
    symbols = {
        k: v for k, v in sym.items() if k in used and (k.isupper() or k in _DATA)
    }
    zp = _ZPGHOST if symbols.get("ZPGHOSTREGS") else _ZP
    if BASE + len(img.data) > CODE:
        raise ValueError(f"song data runs past the playroutine at ${CODE:04X}")
    symbols.update(base=CODE, zpbase=zp)
    code, found, entries = assemble(source, sources()[source], symbols)
    for base, names in _DISPATCH:
        prefix = base[:-1]
        pages = {found.get(prefix + n, found[base]) >> 8 for n in names}
        if pages != {found[base] >> 8}:
            raise AssemblyError(f"{source}: the {prefix}* handlers span pages")
    image = bytearray(img.data)
    image += bytes(CODE - BASE - len(image)) + code
    play = found["mt_play"]
    if build.calls > 1:
        play = BASE + len(image)
        call = bytes((_JSR,)) + found["mt_play"].to_bytes(2, "little")
        image += call * build.calls + bytes((_RTS,))
    channels = tuple(c for c in range(symbols["NUMCHANNELS"]) if c not in build.idle)
    return Packed(bytes(image), found["mt_init"], play, found, channels, entries)
