"""A song written into a packed GoatTracker 2 tune's own player image."""

from dataclasses import replace

from pysidtracker import SidImage

from pygoattracker.errors import GoatTrackerError
from pygoattracker.model import Pattern, Song
from pygoattracker.packed import pack
from pygoattracker.sid import decompile_sid
from pygoattracker.typed import TypedSong, packed_rows, raw_rows, rows_of


def song_into_sid(data: bytes, song: Song | TypedSong) -> bytes:
    """The PSID ``data`` with its song data replaced by ``song``'s, packed at the
    tune's layout as its greloc did (first-row commands stated if each pattern
    states one); the tuning and speed row 0, which are the image's, stay.

    Raises:
      GoatTrackerError: the file does not hold the song data the player reads (its
        init unpacks it), or a piece of ``song`` is larger than the one it replaces.
    """
    dec = decompile_sid(data)
    old = dec.typed
    if isinstance(song, Song):
        song = TypedSong.from_song(song, old.simplepulse)
    first = all(p[0].command is not None for p in old.patterns if p)
    typed = replace(
        song,
        patterns=tuple(
            packed_rows(rows_of(Pattern(raw_rows(p))), first) for p in song.patterns
        ),
        freq=old.freq,
        firstnote=old.firstnote,
        speed0=old.speed0,
    )
    image = SidImage.from_bytes(data)
    try:
        pieces = list(zip(pack(typed, dec.info), pack(old, dec.info), strict=True))
    except ValueError as exc:
        raise GoatTrackerError(f"the song does not fit the tune: {exc}") from exc
    held = image.mem if dec.memory is None else dec.memory
    for (addr, new), (at, was) in pieces:
        if image.mem[at : at + len(was)] != held[at : at + len(was)]:
            raise GoatTrackerError(f"the tune unpacks its song data at ${at:04X}")
        if addr != at or len(new) > len(was):
            raise GoatTrackerError(f"the song's data at ${at:04X} does not fit")
        image.poke_bytes(addr, new)
    return image.container + bytes(image.mem[image.load : image.end])
