"""The playroutine builds whose 6502 branch coverage the spec proofs measure.

A build is one playroutine as a packed song runs it; its cases are the songs of
its proof set (:mod:`tests._proof_songs`) and, for a baseline, of the corpus.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property, lru_cache

import sys
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from jennings.coverage.listing import Listing
from jennings.coverage.smc import analyze
from pysidtracker import SidImage
from pysidtracker.testing import song_frames, whole_song

from pygoattracker._gt1_builds import PLAYERS
from pygoattracker.gt1 import find_player
from pygoattracker.gt1 import PLAYER_ORG
from pygoattracker.gt1player import Gt1Player
from pygoattracker.packed import Build
from pygoattracker.player import Player
from pygoattracker.songimage import BASE
from tests import _gt1_code, _proof_songs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import spec_memcheck  # noqa: E402  pylint: disable=wrong-import-position
from tests._gt2asm import AssemblyError, assemble
from tests._gt2refs import sources
from tests.test_gt2_inplace import MEMORY
from tests.test_oracle_hvsc import TUNES
from tests._gt2pack import pack
from tests.conftest import pass_frames


@dataclass(frozen=True)
class Case:
    """Subtune ``subtune`` of song ``song`` of set ``songs``, played by ``build``."""

    build: str
    songs: str
    song: str
    subtune: int

    @property
    def name(self) -> str:
        """``build:song/subtune``."""
        return f"{self.build}:{self.song}/{self.subtune}"


@dataclass(frozen=True)
class Played:
    """A case ready to play: its PSID ``sid``, the ``packed`` song whose spec
    player it makes and the ``frames`` of its whole song."""

    name: str
    sid: bytes
    subtune: int
    frames: int
    packed: object

    def spec(self):
        """A fresh spec player of the case."""
        return self.packed.spec(self.subtune)


@dataclass(frozen=True)
class Gt2Build:
    """A GoatTracker 2 release ``source`` assembled with ``build``'s options and
    the relocator ``defines``, playing proof set ``songs``."""

    name: str
    source: str
    songs: str
    build: Build = Build()
    defines: tuple = ()

    def pack(self, song):
        """``song`` packed with this build."""
        return pack(song, self.source, self.build, **dict(self.defines))

    def frames(self, song, subtune: int) -> int:
        """The calls of one whole pass of ``song``, to its last channel's loop."""
        return whole_song(pass_frames(Player(song, subtune, self.build)))

    @staticmethod
    def corpus() -> str:
        """The corpus song set."""
        return "gt2_corpus"

    @cached_property
    def reference(self):
        """The build packed with its proof set's first song."""
        song, _ = next(iter(_proof_songs.songs(self.songs).values()))
        return self.pack(song)

    def view(self) -> bytes:
        """The 64 KiB the reference song loads into."""
        image = self.reference.image
        return bytes(BASE) + image + bytes(0x10000 - BASE - len(image))

    def span(self) -> tuple:
        """The playroutine's ``[start, end)``."""
        return self.reference.found.start, self.reference.found.end

    def entries(self) -> tuple:
        """Where the playroutine's code is entered: its labelled instructions."""
        return self.reference.entries

    def fits(self, packed) -> bool:
        """Whether ``packed`` holds this build's code layout."""
        return packed.entries == self.reference.entries

    def labels(self) -> dict:
        """``{address: label}`` of the playroutine's code."""
        found = self.reference.found
        return {
            found[k]: k
            for k in sorted(found.passed)
            if found.start <= found.get(k, -1) < found.end
        }


@dataclass(frozen=True)
class Gt1Packed:
    """A GoatTracker V1.x song as its packer saves it."""

    song: object

    def sid(self) -> bytes:
        """The packed song as a PSID."""
        return self.song.to_sid()

    def spec(self, subtune: int = 0) -> Gt1Player:
        """The spec playing ``subtune``."""
        return Gt1Player(self.song, subtune)


@dataclass(frozen=True)
class Gt1Build:
    """A GoatTracker V1.x playroutine ``name`` playing proof set ``songs``; its
    code is entered at the jump table and every target its self-modified jumps
    take on the proof songs (:func:`jennings.coverage.smc.analyze`)."""

    name: str
    player: str
    songs: str

    @staticmethod
    def pack(song) -> Gt1Packed:
        """``song`` as packed."""
        return Gt1Packed(song)

    @staticmethod
    def frames(song, subtune: int) -> int:
        """The calls of one whole pass of ``song``, to its last channel's loop."""
        return whole_song(pass_frames(Gt1Player(song, subtune)))

    def corpus(self) -> str:
        """The corpus song set of this build."""
        return f"gt1_corpus@{self.player}"

    @cached_property
    def _image(self) -> SidImage:
        song, _ = next(iter(_proof_songs.songs(self.songs).values()))
        return SidImage.from_bytes(song.to_sid())

    def view(self) -> bytes:
        """The 64 KiB the reference song loads into."""
        return bytes(self._image.mem)

    def span(self) -> tuple:
        """The playroutine's ``[start, end)``."""
        song, _ = next(iter(_proof_songs.songs(self.songs).values()))
        return song.address, song.address + song.ref.code_end

    @cached_property
    def _entries(self) -> tuple:
        head = self._image.header
        jumps = (head.init_address, head.play_address)
        mem = np.frombuffer(self.view(), np.uint8)
        smc = analyze(mem, jumps)
        targets = {t for c in smc.unresolved_transfers.values() for t in c}
        return tuple(sorted({*jumps, *smc.starts, *targets}))

    def entries(self) -> tuple:
        """The jump table and the statically reached instructions."""
        return self._entries

    def fits(self, packed) -> bool:
        """Whether ``packed`` is played by this build."""
        return packed.song.player == self.player

    def labels(self) -> dict:
        """``{address: label}`` of the build's source (as assembled from its
        archive), else ``player`` at its start."""
        song, _ = next(iter(_proof_songs.songs(self.songs).values()))
        if self.player in _gt1_code.ARCHIVE:
            return _source_labels(self.player, song.address)
        return {song.address: "player"} | _aligned(self)


_RELEASES = ("235", "255", "261", "265", "268", "273", "276")
_ALT = ("261", "265", "268", "273", "276")
_STEREO = ("259", "274", "277")
_P276 = "gt2_276/player"
_NOAUTHOR = (("NOAUTHORINFO", 1),)
# The defines greloc sets for a song without pattern or wavetable commands, and
# those it sets for one without gates, tables, orderlist commands or vibrato.
_UNCOMMANDED = (
    "NOPORTAMENTO",
    "NOTONEPORTA",
    "NOSETAD",
    "NOSETSR",
    "NOSETWAVE",
    "NOSETWAVEPTR",
    "NOSETPULSEPTR",
    "NOSETFILTPTR",
    "NOSETFILTCTRL",
    "NOSETFILTCUTOFF",
    "NOSETMASTERVOL",
    "NOFUNKTEMPO",
    "NOGLOBALTEMPO",
    "NOCHANNELTEMPO",
)
_UNUSED = (
    "NOGATE",
    "NOFILTER",
    "NOFILTERMOD",
    "NOPULSE",
    "NOPULSEMOD",
    "NOWAVEDELAY",
    "NOREPEAT",
    "NOTRANS",
    "NOVIB",
    "NOINSTRVIB",
    "NOFIRSTWAVECMD",
    "NOCALCULATEDSPEED",
    "NONORMALSPEED",
    "NOZEROSPEED",
)
GT2_BUILDS = (
    *(Gt2Build(f"gt2_{r}", f"gt2_{r}/player", "gt2") for r in _RELEASES),
    *(Gt2Build(f"gt2_{r}_alt", f"gt2_{r}/altplayer", "gt2") for r in _ALT),
    *(
        Gt2Build(f"stereo_{r}", f"gt2stereo_{r}/player", "stereo", defines=_NOAUTHOR)
        for r in _STEREO
    ),
    *(
        Gt2Build(
            f"stereo_{r}_alt", f"gt2stereo_{r}/altplayer", "stereo", defines=_NOAUTHOR
        )
        for r in _STEREO
    ),
    Gt2Build("gt2_255_directwave", "gt2_255/player", "gt2", Build(nohr_wave=True)),
    Gt2Build("gt2_276_buffered", _P276, "gt2", defines=(("BUFFEREDWRITES", 1),)),
    Gt2Build(
        "gt2_276_ghost", _P276, "gt2", defines=(("BUFFEREDWRITES", 1), ("GHOSTREGS", 1))
    ),
    Gt2Build(
        "gt2_276_zpghost",
        _P276,
        "gt2",
        defines=(("BUFFEREDWRITES", 1), ("GHOSTREGS", 1), ("ZPGHOSTREGS", 1)),
    ),
    Gt2Build("gt2_276_simplepulse", _P276, "gt2", Build(simplepulse=True)),
    Gt2Build(
        "gt2_276_noskip",
        _P276,
        "gt2",
        Build(optimize_pulse=False, optimize_realtime=False),
    ),
    Gt2Build(
        "gt2_276_noeffects",
        _P276,
        "gt2_plain",
        Build(live_vibrato=True),
        defines=tuple((d, 1) for d in _UNCOMMANDED),
    ),
    Gt2Build("gt2_276_nohr", _P276, "gt2_nohr"),
    Gt2Build("gt2_276_nolegato", _P276, "gt2_nolegato"),
    Gt2Build("gt2_276_nowavecmd", _P276, "gt2_nowavecmd"),
    Gt2Build(
        "gt2_276_minimal",
        _P276,
        "gt2_minimal",
        Build(live_vibrato=True),
        defines=(
            *((d, 1) for d in _UNCOMMANDED + _UNUSED),
            ("FIXEDPARAMS", 1),
            ("FIRSTWAVEPARAM", 0x09),
            ("GATETIMERPARAM", 2),
        ),
    ),
    Gt2Build("gt2_276_multispeed", _P276, "gt2", Build(calls=3, default_tempo=17)),
)


def _source_labels(player: str, address: int) -> dict:
    """``{address: label}`` of archive build ``player`` assembled at ``address``."""
    named = _gt1_code.assemble(_gt1_code.source_of(player))[1]
    return {v + address - PLAYER_ORG: k for k, v in sorted(named.items()) if k}


def _opcodes(build) -> tuple:
    """``(addresses, opcodes)`` of ``build``'s instructions in its span."""
    view = np.frombuffer(build.view(), np.uint8)
    lines = Listing.disassemble(view, build.entries()).lines[:, 1]
    start, end = build.span()
    addrs = np.sort(lines[(lines >= start) & (lines < end)])
    return addrs.tolist(), view[addrs].tobytes()


def _aligned(build) -> dict:
    """The labels of the archive build of ``build``'s family whose instruction
    stream aligns best with ``build``'s, moved to the aligned instructions."""
    mine, ops = _opcodes(build)
    best = None
    for other in GT1_BUILDS:
        if other.player in _gt1_code.ARCHIVE and other.songs.split("@")[0] == (
            build.songs.split("@")[0]
        ):
            theirs, their_ops = _opcodes(other)
            match = SequenceMatcher(None, ops, their_ops, autojunk=False)
            if best is None or match.ratio() > best[0]:
                best = (match.ratio(), match, theirs, other)
    _, match, theirs, other = best
    names = _source_labels(other.player, other.span()[0])
    moved = {}
    for a, b, size in match.get_matching_blocks():
        for k in range(size):
            if theirs[b + k] in names:
                moved[mine[a + k]] = names[theirs[b + k]]
    return moved


_FAMILY_SETS = {"1.5": "gt1_15", "1.4": "gt1_14", "11": "gt1_11"}
GT1_BUILDS = tuple(
    Gt1Build(f"gt1_{name}", name, f"{_FAMILY_SETS[entry['family']]}@{name}")
    for name, entry in PLAYERS.items()
)


@lru_cache(maxsize=None)
def _by_name() -> dict:
    return {b.name: b for b in GT2_BUILDS + GT1_BUILDS + TUNE_BUILDS}


def builds(names=None) -> list:
    """The builds named (default: every build the proof songs play)."""
    if names:
        return [_by_name()[n] for n in names]
    return list(GT2_BUILDS + GT1_BUILDS)


def cases(build, corpus: bool = False, proof: bool = True) -> list:
    """The cases of ``build``: its ``proof`` set's songs and the ``corpus``'s (a
    real tune's: the tune itself)."""
    if isinstance(build, TuneBuild):
        return [Case(build.name, "tune", build.tune, 0)]
    sets = (build.songs,) * proof + (build.corpus(),) * corpus
    return [
        Case(build.name, name, song, subtune)
        for name in sets
        for song, (_, subtunes) in _proof_songs.songs(name).items()
        for subtune in subtunes
    ]


def played(case: Case) -> Played | None:
    """``case`` packed, None for a corpus song that assembles another build."""
    build = _by_name()[case.build]
    if isinstance(build, TuneBuild):
        return build.played()
    song, _ = _proof_songs.songs(case.songs)[case.song]
    corpus = case.songs == build.corpus()
    try:
        packed = build.pack(song)
    except AssemblyError:
        if corpus:
            return None
        raise
    if not build.fits(packed):
        if corpus:
            return None
        raise ValueError(f"{case.name} assembles another build than {build.name}")
    frames = build.frames(song, case.subtune)
    return Played(case.name, packed.sid(), case.subtune, frames, packed)


@dataclass(frozen=True)
class TunePacked:
    """A real tune: its PSID bytes, played by the spec bound in place."""

    data: bytes
    song: int

    def sid(self) -> bytes:
        """The tune's PSID."""
        return self.data

    def spec(self, subtune: int):  # pylint: disable=unused-argument
        """The spec playing the tune's 1-based ``song``."""
        return spec_memcheck.spec_player(self.data, self.song)


@dataclass(frozen=True)
class TuneBuild:
    """The playroutine a real oracle tune ``tune`` was packed with, played by
    the tune's own song only."""

    name: str
    tune: str

    @cached_property
    def _data(self) -> tuple:
        relpath, song = TUNES[self.tune]
        return Path(_proof_songs.resolved(relpath)).read_bytes(), song

    def played(self) -> Played:
        """The tune's whole song."""
        data, song = self._data
        return Played(
            self.name, data, song - 1, song_frames(data, song), TunePacked(data, song)
        )

    def view(self) -> bytes:
        """The 64 KiB the tune loads into."""
        return bytes(SidImage.from_bytes(self._data[0]).mem)

    @cached_property
    def _code(self) -> tuple:
        """``(span, entries)``: a GoatTracker 2 tune's matched source assembled
        again over its defines and data labels, else the V1.x player's
        jump table and statically reached instructions."""
        data, song = self._data
        player = TunePacked(data, song).spec(0)
        if hasattr(player, "found"):
            found = player.found(player.view())
            symbols = {
                k: v
                for k, v in found.symbols.items()
                if k not in found.passed or not found.start <= v < found.end
            }
            for lab in ("mt_freqtbllo", "mt_freqtblhi"):
                value = found.exprs.get(repr(("-", lab, "FIRSTNOTE")))
                if value is not None and lab not in symbols:
                    symbols[lab] = value
            symbols.setdefault("FIRSTNOTE", 0)
            symbols["base"] = found.start
            entries = assemble(found.name, sources()[found.name], symbols)[2]
            return (found.start, found.end), entries
        found = find_player(self.view())
        start = found.address
        head = SidImage.from_bytes(data).header
        jumps = (head.init_address, head.play_address)
        smc = analyze(np.frombuffer(self.view(), np.uint8), jumps)
        targets = {t for c in smc.unresolved_transfers.values() for t in c}
        span = (start, start + found.ref.code_end)
        return span, tuple(sorted({*jumps, *smc.starts, *targets}))

    def span(self) -> tuple:
        """The playroutine's ``[start, end)``."""
        return self._code[0]

    def entries(self) -> tuple:
        """Where the playroutine's code is entered."""
        return self._code[1]

    def labels(self) -> dict:
        """The playroutine's start."""
        return {self.span()[0]: "player"}


TUNE_BUILDS = tuple(
    TuneBuild(f"tune_{name}", name)
    for name in (*MEMORY, *(k for k in TUNES if k.startswith("gt1_")))
)


# Each branch direction no song reaches: its key, why, and the builds it is in.
UNREACHABLE = {
    "mt_arp0+2 unfallen": (
        "lda #$00 sets N clear: bpl always branches",
        (
            "gt1_1.53",
            "gt1_1.52",
            "gt1_1.51",
            "gt1_1.50b",
            "gt1_1.50",
            "gt1_1.4b",
            "gt1_1.4b-hr1",
            "gt1_1.4b-multi",
        ),
    ),
    "mt_arp0+5 unfallen": (
        "lda #$ff sets Z clear: bne always branches",
        (
            "gt1_11.1",
            "gt1_11.2",
            "gt1_11.3",
            "gt1_11.4",
            "gt1_11.5",
            "gt1_0.96",
            "gt1_1.01",
            "gt1_1.1",
            "gt1_1.15",
            "gt1_1.16",
            "gt1_1.21",
            "gt1_1.22",
        ),
    ),
    "mt_arp2+5 unfallen": (
        "and #$0f clears N: bpl always branches",
        (
            "gt1_1.53",
            "gt1_1.52",
            "gt1_1.51",
            "gt1_1.50b",
            "gt1_1.50",
            "gt1_1.4b",
            "gt1_1.4b-hr1",
            "gt1_1.4b-multi",
        ),
    ),
    "mt_arp2+9 unfallen": (
        "lda #$00 sets Z: beq always branches",
        (
            "gt1_11.1",
            "gt1_11.2",
            "gt1_11.3",
            "gt1_11.4",
            "gt1_11.5",
            "gt1_0.96",
            "gt1_1.01",
            "gt1_1.1",
            "gt1_1.15",
            "gt1_1.16",
            "gt1_1.21",
            "gt1_1.22",
        ),
    ),
    "mt_chnsongptr+0 unexecuted": (
        "the channel variables after the code's last, always-taken branch: data, never run",
        ("gt2_235", "gt2_276_noeffects", "gt2_276_nowavecmd"),
    ),
    "mt_dosfx_end+3 unfallen": (
        "stores the zero just tested: beq always branches",
        ("gt1_11.2", "gt1_11.4"),
    ),
    "mt_effects2+5 unfallen": (
        "follows a branch on the same flag that did not branch: it always branches",
        ("gt1_1.4b", "gt1_1.4b-multi"),
    ),
    "mt_gate+2 unfallen": (
        "ora #$f0 sets Z clear: bne always branches",
        (
            "gt2_235",
            "gt2_255",
            "gt2_261",
            "gt2_265",
            "gt2_268",
            "gt2_273",
            "gt2_276",
            "gt2_261_alt",
            "gt2_265_alt",
            "gt2_268_alt",
            "gt2_273_alt",
            "gt2_276_alt",
            "stereo_259",
            "stereo_274",
            "stereo_277",
            "stereo_259_alt",
            "stereo_274_alt",
            "stereo_277_alt",
            "gt2_255_directwave",
            "gt2_276_buffered",
            "gt2_276_ghost",
            "gt2_276_zpghost",
            "gt2_276_simplepulse",
            "gt2_276_noskip",
            "gt2_276_noeffects",
            "gt2_276_nohr",
            "gt2_276_nolegato",
            "gt2_276_nowavecmd",
            "gt2_276_multispeed",
        ),
    ),
    "mt_getnewnotes+32 unfallen": (
        "sbc of a byte at least NOCMD less NOCMD-1 with borrow never borrows: bcs always branches",
        ("gt1_1.53", "gt1_1.52", "gt1_1.51", "gt1_1.50b", "gt1_1.50"),
    ),
    "mt_init+10 unexecuted": (
        "the 2.59 stereo init stores the doubled song number over its own adc opcode ($00, brk, for song 0), so it never reaches its store and rts",
        ("stereo_259", "stereo_259_alt"),
    ),
    "mt_init+7 unexecuted": (
        "the 2.59 stereo init stores the doubled song number over its own adc opcode ($00, brk, for song 0), so it never reaches its store and rts",
        ("stereo_259", "stereo_259_alt"),
    ),
    "mt_loadfilt+8 unfallen": (
        "the cpx #21 / bcs choosing the first chip left carry clear: bcc always branches",
        (
            "stereo_259",
            "stereo_274",
            "stereo_277",
            "stereo_259_alt",
            "stereo_274_alt",
            "stereo_277_alt",
        ),
    ),
    "mt_loopsong+3 unfallen": (
        "carry is set by the loop mark's cmp #LOOPSONG: bcs always branches",
        ("gt1_1.4b", "gt1_1.4b-hr1", "gt1_1.4b-multi"),
    ),
    "mt_newnotes+36 unfallen": (
        "sbc of a byte at least NOCMD less NOCMD-1 with borrow never borrows: bcs always branches",
        (
            "gt1_11.1",
            "gt1_11.2",
            "gt1_11.3",
            "gt1_11.4",
            "gt1_11.5",
            "gt1_0.96",
            "gt1_1.01",
            "gt1_1.1",
            "gt1_1.15",
            "gt1_1.16",
            "gt1_1.21",
            "gt1_1.22",
        ),
    ),
    "mt_newpackedrest+6 unfallen": (
        "stores a packed rest byte, never zero: bne always branches",
        ("gt1_1.4b", "gt1_1.4b-hr1", "gt1_1.4b-multi"),
    ),
    "mt_noadsrinit+23 unfallen": (
        "lda #$01 sets Z clear: bne always branches",
        ("gt1_11.2", "gt1_11.4"),
    ),
    "mt_noadsrinit+29 unfallen": (
        "lda #$01 sets Z clear: bne always branches",
        (
            "gt1_11.1",
            "gt1_11.3",
            "gt1_11.5",
            "gt1_0.96",
            "gt1_1.01",
            "gt1_1.1",
            "gt1_1.15",
            "gt1_1.16",
            "gt1_1.21",
            "gt1_1.22",
        ),
    ),
    "mt_nofilterchange+16 unfallen": (
        "lda #$80 sets Z clear: bne always branches",
        ("gt1_11.1", "gt1_11.2", "gt1_11.3", "gt1_11.4", "gt1_11.5"),
    ),
    "mt_nohr_legato+4 unfallen": (
        "follows a branch on the same flag that did not branch: it always branches",
        (
            "gt2_235",
            "gt2_255",
            "gt2_261",
            "gt2_265",
            "gt2_268",
            "gt2_273",
            "gt2_276",
            "gt2_261_alt",
            "gt2_265_alt",
            "gt2_268_alt",
            "gt2_273_alt",
            "gt2_276_alt",
            "stereo_259",
            "stereo_274",
            "stereo_277",
            "stereo_259_alt",
            "stereo_274_alt",
            "stereo_277_alt",
            "gt2_255_directwave",
            "gt2_276_buffered",
            "gt2_276_ghost",
            "gt2_276_zpghost",
            "gt2_276_simplepulse",
            "gt2_276_noskip",
            "gt2_276_noeffects",
            "gt2_276_nohr",
            "gt2_276_nowavecmd",
            "gt2_276_multispeed",
        ),
    ),
    "mt_normalnote+18 unfallen": (
        "with no hard-restart instrument FIRSTNOHRINSTR is 1 and instrument numbers start at 1: bcs always branches",
        ("gt2_276_nohr",),
    ),
    "mt_nowaveloop+5 unfallen": (
        "loads a note masked with and #$7f: bpl always branches",
        (
            "gt1_11.1",
            "gt1_11.2",
            "gt1_11.3",
            "gt1_11.4",
            "gt1_11.5",
            "gt1_0.96",
            "gt1_1.01",
            "gt1_1.1",
            "gt1_1.15",
            "gt1_1.16",
            "gt1_1.21",
            "gt1_1.22",
            "gt1_1.4b",
            "gt1_1.4b-hr1",
            "gt1_1.4b-multi",
        ),
    ),
    "mt_packedrestnonew+7 unfallen": (
        "follows a branch on the same flag that did not branch: it always branches",
        (
            "gt2_235",
            "gt2_255",
            "gt2_261",
            "gt2_265",
            "gt2_268",
            "gt2_273",
            "gt2_276",
            "gt2_261_alt",
            "gt2_265_alt",
            "gt2_268_alt",
            "gt2_273_alt",
            "gt2_276_alt",
            "stereo_259",
            "stereo_274",
            "stereo_277",
            "stereo_259_alt",
            "stereo_274_alt",
            "stereo_277_alt",
            "gt2_255_directwave",
            "gt2_276_buffered",
            "gt2_276_ghost",
            "gt2_276_zpghost",
            "gt2_276_simplepulse",
            "gt2_276_noskip",
            "gt2_276_noeffects",
            "gt2_276_nohr",
            "gt2_276_nolegato",
            "gt2_276_nowavecmd",
            "gt2_276_minimal",
            "gt2_276_multispeed",
        ),
    ),
    "mt_packedrestnotover+3 unfallen": (
        "loads a command number 0-7: bpl always branches",
        (
            "gt1_11.1",
            "gt1_11.2",
            "gt1_11.3",
            "gt1_11.4",
            "gt1_11.5",
            "gt1_0.96",
            "gt1_1.01",
            "gt1_1.1",
            "gt1_1.15",
            "gt1_1.16",
            "gt1_1.21",
            "gt1_1.22",
        ),
    ),
    "mt_patttblhiaccess+23 unfallen": (
        "sbc of a byte at least NOCMD less NOCMD-1 with borrow never borrows: bcs always branches",
        ("gt1_1.4b", "gt1_1.4b-hr1", "gt1_1.4b-multi"),
    ),
    "mt_portamento+5 unfallen": (
        "follows a branch on the same flag that did not branch: it always branches",
        (
            "gt1_11.1",
            "gt1_11.2",
            "gt1_11.3",
            "gt1_11.4",
            "gt1_11.5",
            "gt1_0.96",
            "gt1_1.01",
            "gt1_1.1",
            "gt1_1.15",
            "gt1_1.16",
            "gt1_1.21",
            "gt1_1.22",
        ),
    ),
    "mt_repeat+5 unfallen": (
        "sbc of a repeat byte less REPEAT-1 with borrow never borrows: bcs always branches",
        ("gt1_1.53", "gt1_1.52", "gt1_1.51", "gt1_1.50b", "gt1_1.50"),
    ),
    "mt_repeatdone+5 unfallen": (
        "lda #$00 before the store sets Z: beq always branches",
        (
            "gt2_235",
            "gt2_255",
            "gt2_261",
            "gt2_265",
            "gt2_268",
            "gt2_273",
            "gt2_276",
            "gt2_261_alt",
            "gt2_265_alt",
            "gt2_268_alt",
            "gt2_273_alt",
            "gt2_276_alt",
            "stereo_259",
            "stereo_274",
            "stereo_277",
            "stereo_259_alt",
            "stereo_274_alt",
            "stereo_277_alt",
            "gt2_255_directwave",
            "gt2_276_buffered",
            "gt2_276_ghost",
            "gt2_276_zpghost",
            "gt2_276_simplepulse",
            "gt2_276_noskip",
            "gt2_276_noeffects",
            "gt2_276_nohr",
            "gt2_276_nolegato",
            "gt2_276_nowavecmd",
            "gt2_276_multispeed",
        ),
    ),
    "mt_setsustain+8 unfallen": (
        "follows bmi that did not branch, N unchanged by the store: bpl always branches",
        ("gt1_11.2", "gt1_11.4"),
    ),
    "mt_settempo+11 unfallen": (
        "follows bmi that did not branch, N unchanged by the stores: bpl always branches",
        (
            "gt1_11.1",
            "gt1_11.2",
            "gt1_11.3",
            "gt1_11.4",
            "gt1_11.5",
            "gt1_0.96",
            "gt1_1.01",
            "gt1_1.1",
            "gt1_1.15",
            "gt1_1.16",
            "gt1_1.21",
            "gt1_1.22",
            "gt1_1.4b",
            "gt1_1.4b-hr1",
            "gt1_1.4b-multi",
        ),
    ),
    "mt_settempoone+5 unfallen": (
        "stores a tempo masked with and #$7f: bpl always branches",
        (
            "gt1_11.1",
            "gt1_11.2",
            "gt1_0.96",
            "gt1_1.01",
            "gt1_1.1",
            "gt1_1.15",
            "gt1_1.16",
            "gt1_1.21",
            "gt1_1.22",
        ),
    ),
    "mt_settempoone+9 unfallen": (
        "stores a tempo masked with and #$7f: bpl always branches",
        (
            "gt1_11.3",
            "gt1_11.4",
            "gt1_11.5",
            "gt1_1.4b",
            "gt1_1.4b-hr1",
            "gt1_1.4b-multi",
        ),
    ),
    "mt_sfscutoffskip+2 unfallen": (
        "lda #$00 sets Z: beq always branches",
        (
            "gt1_1.53",
            "gt1_1.52",
            "gt1_1.51",
            "gt1_1.50b",
            "gt1_1.50",
            "gt1_1.4b",
            "gt1_1.4b-hr1",
            "gt1_1.4b-multi",
        ),
    ),
    "mt_starttoneportamento+5 unfallen": (
        "stores lda #$fe, Z clear: bne always branches",
        (
            "gt1_11.1",
            "gt1_11.2",
            "gt1_11.3",
            "gt1_11.4",
            "gt1_11.5",
            "gt1_0.96",
            "gt1_1.01",
            "gt1_1.1",
            "gt1_1.15",
            "gt1_1.16",
            "gt1_1.21",
            "gt1_1.22",
            "gt1_1.4b",
            "gt1_1.4b-hr1",
            "gt1_1.4b-multi",
        ),
    ),
    "mt_tick0_12+0 unexecuted": (
        "NOEFFECTS assembles out the tick-0 dispatch that enters it",
        ("gt2_276_noeffects",),
    ),
    "mt_tick0_12+1 unexecuted": (
        "NOEFFECTS assembles out the tick-0 dispatch that enters it",
        ("gt2_276_noeffects",),
    ),
    "mt_tick0_12+3 unexecuted": (
        "NOEFFECTS assembles out the tick-0 dispatch that enters it",
        ("gt2_276_noeffects",),
    ),
    "mt_tick0_12+6 unexecuted": (
        "NOEFFECTS assembles out the tick-0 dispatch that enters it",
        ("gt2_276_noeffects",),
    ),
    "mt_tick0_e+15 unfallen": (
        "lda #$00 sets Z: the funktempo's jump to the global tempo always branches",
        (
            "gt2_235",
            "gt2_255",
            "gt2_261",
            "gt2_265",
            "gt2_268",
            "gt2_273",
            "gt2_276",
            "gt2_261_alt",
            "gt2_265_alt",
            "gt2_268_alt",
            "gt2_273_alt",
            "gt2_276_alt",
            "stereo_259",
            "stereo_274",
            "stereo_277",
            "stereo_259_alt",
            "stereo_274_alt",
            "stereo_277_alt",
            "gt2_255_directwave",
            "gt2_276_buffered",
            "gt2_276_ghost",
            "gt2_276_zpghost",
            "gt2_276_simplepulse",
            "gt2_276_noskip",
            "gt2_276_nohr",
            "gt2_276_nolegato",
            "gt2_276_nowavecmd",
            "gt2_276_multispeed",
        ),
    ),
    "mt_tick0_f_setglobaltempo+0 unexecuted": (
        "the effect jump that enters it is assembled out with every effect",
        ("gt2_276_minimal",),
    ),
    "mt_ticknportadown+4 unfallen": (
        "sec before it: bcs always branches",
        ("gt1_1.53", "gt1_1.52", "gt1_1.51", "gt1_1.50b", "gt1_1.50"),
    ),
    "mt_ticknportaup+3 unfallen": (
        "the speed's high byte is cleared before the shifts, so the last rol leaves carry clear: bcc always branches",
        ("gt1_1.53", "gt1_1.52", "gt1_1.51", "gt1_1.50b", "gt1_1.50"),
    ),
    "mt_timingmark+2 unfallen": (
        "carry is set by the cmp that chose the timing mark: bcs always branches",
        (
            "gt1_11.3",
            "gt1_11.4",
            "gt1_11.5",
            "gt1_1.4b",
            "gt1_1.4b-hr1",
            "gt1_1.4b-multi",
        ),
    ),
    "mt_tpdown+10 unfallen": (
        "follows a branch on the same flag that did not branch: it always branches",
        (
            "gt1_11.1",
            "gt1_11.2",
            "gt1_11.3",
            "gt1_11.4",
            "gt1_11.5",
            "gt1_0.96",
            "gt1_1.01",
            "gt1_1.1",
            "gt1_1.15",
            "gt1_1.16",
            "gt1_1.21",
            "gt1_1.22",
        ),
    ),
    "mt_tpup+10 unfallen": (
        "follows a branch on the same flag that did not branch: it always branches",
        (
            "gt1_11.1",
            "gt1_11.2",
            "gt1_11.3",
            "gt1_11.4",
            "gt1_11.5",
            "gt1_0.96",
            "gt1_1.01",
            "gt1_1.1",
            "gt1_1.15",
            "gt1_1.16",
            "gt1_1.21",
            "gt1_1.22",
        ),
    ),
    "mt_tpupchecklow+8 unfallen": (
        "follows a branch on the same flag that did not branch: it always branches",
        (
            "gt1_11.1",
            "gt1_11.2",
            "gt1_11.3",
            "gt1_11.4",
            "gt1_11.5",
            "gt1_0.96",
            "gt1_1.01",
            "gt1_1.1",
            "gt1_1.15",
            "gt1_1.16",
            "gt1_1.21",
            "gt1_1.22",
        ),
    ),
    "mt_vibdone+6 unfallen": (
        "follows a branch on the same flag that did not branch: it always branches",
        (
            "gt2_235",
            "gt2_255",
            "gt2_261",
            "gt2_265",
            "gt2_268",
            "gt2_273",
            "gt2_276",
            "gt2_261_alt",
            "gt2_265_alt",
            "gt2_268_alt",
            "gt2_273_alt",
            "gt2_276_alt",
            "stereo_259",
            "stereo_274",
            "stereo_277",
            "stereo_259_alt",
            "stereo_274_alt",
            "stereo_277_alt",
            "gt2_255_directwave",
            "gt2_276_buffered",
            "gt2_276_ghost",
            "gt2_276_zpghost",
            "gt2_276_simplepulse",
            "gt2_276_noskip",
            "gt2_276_noeffects",
            "gt2_276_nohr",
            "gt2_276_nolegato",
            "gt2_276_nowavecmd",
            "gt2_276_multispeed",
            "gt1_1.53",
            "gt1_1.52",
            "gt1_1.51",
            "gt1_1.50b",
            "gt1_1.50",
            "gt1_11.1",
            "gt1_11.2",
            "gt1_11.3",
            "gt1_11.4",
            "gt1_11.5",
            "gt1_0.96",
            "gt1_1.01",
            "gt1_1.1",
            "gt1_1.15",
            "gt1_1.16",
            "gt1_1.21",
            "gt1_1.22",
            "gt1_1.4b",
            "gt1_1.4b-hr1",
            "gt1_1.4b-multi",
        ),
    ),
}


def unreachable(name: str) -> dict:
    """``{key: reason}`` of the branch directions build ``name`` cannot run."""
    return {k: why for k, (why, names) in UNREACHABLE.items() if name in names}
