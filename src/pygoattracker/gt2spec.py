"""The GoatTracker 2 packed playroutine as a ``pysidtracker.playspec`` spec.

``specs/goattracker.play`` states the play call of every V2.34-V2.76 build;
:func:`layout` binds it to a playroutine's :class:`Match`: its labels, tables and
operands, and its relocator defines. :class:`Binder` matches the playroutine in a
decompiled tune's own image and binds the spec to it in place; :func:`spec_binding`
is that as the model's :class:`~pysidtracker.formats.SpecBinding`.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

from pysidtracker.formats import SpecBinding
from pysidtracker.playspec import parse as parse_spec

from pygoattracker.errors import SidParseError
from pygoattracker.gt2match import Match, match

# Release behaviour no define states (see the spec's flags of the same names).
FEATURES = {
    "gt2_235": {"pulse2x", "livegate", "zeroskip", "initloadregs"},
    "gt2_255": {"zeroskip", "initloadregs"},
    "gt2_261": {"initloadregs"},
    "gt2_265": {"initloadregs"},
    "gt2_276": {"vibzero"},
    "gt2_sf11": {"vibzero"},
    "gt2stereo_259": {"initloadregs"},
    "gt2stereo_274": {"initloadregs"},
    "gt2stereo_277": {"initloadregs", "vibzero"},
}
# A define a release does not have, as the code it has behaves.
_ABSENT = {
    "NOCALCULATEDSPEED": 1,
    "NOWAVECMD": 1,
    "NOFIRSTWAVECMD": 1,
    "REALTIMEOPTIMIZATION": 1,
    "NODIRECTWAVE": 1,
}
_CHN = (
    "songptr",
    "trans",
    "repeat",
    "pattptr",
    "packedrest",
    "newfx",
    "newparam",
    "fx",
    "param",
    "newnote",
    "waveptr",
    "wave",
    "pulseptr",
    "pulsetime",
    "songnum",
    "pattnum",
    "tempo",
    "counter",
    "note",
    "instr",
    "gate",
    "vibtime",
    "vibdelay",
    "wavetime",
    "gatetimer",
    "lastnote",
)
# Operand cells: layout name -> the label whose instruction's operand it is.
_OPERANDS = {
    "initsongnum": "mt_initsongnum",
    "filtstep": "mt_filtstep",
    "filttime": "mt_filttime",
    "filtcutoff": "mt_filtcutoff",
    "filtctrl": "mt_filtctrl",
    "filttype": "mt_filttype",
    "filtstep2": "mt_filtstep_sid2",
    "filttime2": "mt_filttime_sid2",
    "filtcutoff2": "mt_filtcutoff_sid2",
    "filtctrl2": "mt_filtctrl_sid2",
    "filttype2": "mt_filttype_sid2",
    "masterfader": "mt_masterfader",
    "tick0jump1": "mt_tick0jump1",
    "tick0jump2": "mt_tick0jump2",
    "effectjump": "mt_effectjump",
    "effectnum": "mt_effectnum",
    "speedcmp": "mt_effect_4_speedcmp",
    "cscount": "mt_cscount",
    "csresty": "mt_csresty",
    "wavetick0jump": "mt_execwavetick0jump",
}
# 1-based tables (the code indexes label-1,y): layout name -> label.
_TABLES = {
    "ins_ad": "mt_insad",
    "ins_sr": "mt_inssr",
    "ins_wave": "mt_inswaveptr",
    "ins_pulse": "mt_inspulseptr",
    "ins_filt": "mt_insfiltptr",
    "ins_vibparam": "mt_insvibparam",
    "ins_vibdelay": "mt_insvibdelay",
    "ins_gate": "mt_insgatetimer",
    "ins_first": "mt_insfirstwave",
    "wavetbl": "mt_wavetbl",
    "notetbl": "mt_notetbl",
    "pulsetimetbl": "mt_pulsetimetbl",
    "pulsespdtbl": "mt_pulsespdtbl",
    "filttimetbl": "mt_filttimetbl",
    "filtspdtbl": "mt_filtspdtbl",
    "speedleft": "mt_speedlefttbl",
    "speedright": "mt_speedrighttbl",
}
_FLAGS = {
    "noeffects": "NOEFFECTS",
    "noinstrvib": "NOINSTRVIB",
    "novib": "NOVIB",
    "notoneporta": "NOTONEPORTA",
    "noportamento": "NOPORTAMENTO",
    "nocalcspeed": "NOCALCULATEDSPEED",
    "nonormalspeed": "NONORMALSPEED",
    "nozerospeed": "NOZEROSPEED",
    "nowavedelay": "NOWAVEDELAY",
    "nowavecmd": "NOWAVECMD",
    "nopulse": "NOPULSE",
    "nopulsemod": "NOPULSEMOD",
    "nofilter": "NOFILTER",
    "nofiltermod": "NOFILTERMOD",
    "nofunktempo": "NOFUNKTEMPO",
    "nochanneltempo": "NOCHANNELTEMPO",
    "noglobaltempo": "NOGLOBALTEMPO",
    "notrans": "NOTRANS",
    "norepeat": "NOREPEAT",
    "nogate": "NOGATE",
    "fixedparams": "FIXEDPARAMS",
    "nofirstwavecmd": "NOFIRSTWAVECMD",
    "noauthorinfo": "NOAUTHORINFO",
    "simplepulse": "SIMPLEPULSE",
    "pulseopt": "PULSEOPTIMIZATION",
    "realtimeopt": "REALTIMEOPTIMIZATION",
}
_FEATURES = ("pulse2x", "livegate", "zeroskip", "initloadregs", "vibzero")
_TICK0 = ("0", "12", "34", "5", "6", "7", "8", "9", "a", "b", "c", "d", "e", "f")
# Releases whose global tempo stores three channels' tempos whatever the count.
_TEMPO3 = {"gt2_235", "gt2_255", "gt2_261", "gt2_265"}


@lru_cache(maxsize=None)
def spec():
    """The parsed GoatTracker 2 spec."""
    text = resources.files(__package__).joinpath("specs/goattracker.play")
    return parse_spec(text.read_text())


def _define(found: Match, name: str) -> int:
    return found.get(name, _ABSENT.get(name, 0))


def _freq_base(found: Match, label: str) -> int:
    """The base the note loads index: ``label - FIRSTNOTE``."""
    sym = found.symbols
    if "FIRSTNOTE" in sym and label in sym:
        return sym[label] - sym["FIRSTNOTE"]
    value = found.exprs.get(repr(("-", label, "FIRSTNOTE")))
    if value is None:
        raise SidParseError(f"{found.name}: no note load of {label}")
    return value


def _outputs(found: Match, ghosted: bool, buffered: bool, stereo: bool) -> dict:
    """Where each channel's envelope and control byte go, for channels 0 and 3."""
    sid = found.get("SIDBASE", 0xD400)
    sid2 = found.get("SID2BASE", sid + 0x20) if stereo else sid
    if ghosted:
        ghost = found["ghostfreqlo"]
        ad, sr, ctrl = ghost + 5, ghost + 6, ghost + 4
        return dict(
            adout=ad, adout2=ad, srout=sr, srout2=sr, ctrlout=ctrl, ctrlout2=ctrl
        )
    if buffered:
        ad, sr = found["mt_chnad"], found["mt_chnsr"]
        return dict(
            adout=ad,
            adout2=ad + 21,
            srout=sr,
            srout2=sr + 21,
            ctrlout=sid + 4,
            ctrlout2=sid2 + 4,
        )
    return dict(
        adout=sid + 5,
        adout2=sid2 + 5,
        srout=sid + 6,
        srout2=sid2 + 6,
        ctrlout=sid + 4,
        ctrlout2=sid2 + 4,
    )


def _handlers(found: Match) -> dict:
    """Each tick-0 handler's low address byte (``t0_<n>``) and whether code
    starts there (``h0_<n>``: a removed handler's label is the next one's); the
    effect handlers' (``e_<n>``, ``he_<n>``)."""
    sym = found.symbols
    end = sym.get("mt_effect_0_delay", sym.get("mt_effect_0", 0))
    addrs = [sym.get(f"mt_tick0_{k}", 0) for k in _TICK0] + [end]
    out = {}
    for k, here, after in zip(_TICK0, addrs, addrs[1:]):
        out[f"t0_{k}"] = here & 0xFF
        out[f"h0_{k}"] = after != here
    for k in ("0", "4", "3", "12"):
        out[f"e_{k}"] = sym.get(f"mt_effect_{k}", 0) & 0xFF
    out.update(
        he_4=not _define(found, "NOVIB"),
        he_3=not _define(found, "NOTONEPORTA"),
        he_12=True,
    )
    return out


def layout(found: Match, exec_channels=None, chip_writes=False) -> dict:
    """The spec layout of the playroutine ``found`` (``exec_channels``: the
    channels its play runs, default all; ``chip_writes``: a build without ghost
    registers writes the chips directly, whatever its channel count)."""
    sym = found.symbols

    def lab(name, offset=0):
        return sym[name] + offset if name in sym else 0

    nchan = _define(found, "NUMCHANNELS") or 3
    ghosted = bool(found.get("GHOSTREGS", found.get("ZPGHOSTREGS", 0)))
    stereo = nchan > 3
    buffered = bool(_define(found, "BUFFEREDWRITES")) or ghosted
    buffered = buffered or (stereo and not chip_writes)
    sid = found.get("SIDBASE", 0xD400)
    sid2 = found.get("SID2BASE", sid + 0x20) if stereo else sid
    ghost = lab("ghostfreqlo")
    regs = ghost if ghosted else sid
    out = {name: lab("mt_chn" + name) for name in _CHN}
    out.update({name: lab(label, 1) for name, label in _OPERANDS.items()})
    for name in ("filtstep", "filttime", "filtcutoff", "filtctrl", "filttype"):
        out[name + "2"] = out[name + "2"] or out[name] + 0x100
    out.update({name: lab(label, -1) for name, label in _TABLES.items()})
    out.update({name: bool(_define(found, d)) for name, d in _FLAGS.items()})
    features = FEATURES.get(found.name.split("/")[0], set())
    out.update({name: name in features for name in _FEATURES})
    out.update(_outputs(found, ghosted, buffered, stereo))
    out.update(_handlers(found))
    out.update(
        freqlo=ghost if ghosted else lab("mt_chnfreqlo"),
        freqhi=ghost + 1 if ghosted else lab("mt_chnfreqhi"),
        pulselo=ghost + 2 if ghosted else lab("mt_chnpulselo"),
        pulsehi=ghost + 3 if ghosted else lab("mt_chnpulsehi"),
        adv=lab("mt_chnad"),
        srv=lab("mt_chnsr"),
        author=lab("mt_author"),
        songlo=lab("mt_songtbllo"),
        songhi=lab("mt_songtblhi"),
        pattlo=lab("mt_patttbllo"),
        patthi=lab("mt_patttblhi"),
        freqlo_t=_freq_base(found, "mt_freqtbllo"),
        freqhi_t=_freq_base(found, "mt_freqtblhi"),
        tick0tbl=lab("mt_tick0jumptbl"),
        effecttbl=lab("mt_effectjumptbl"),
        funktbl=lab("mt_funktempotbl"),
        temp=sym.get("mt_temp1", 0),
        sid=sid,
        ghost=ghost,
        regs=regs,
        regs2=sid2 if stereo else regs,
        voice=sid,
        voice2=sid2,
        nchan=nchan,
        nfilt=(nchan + 2) // 3,
        ntempo=3 if found.name.split("/")[0] in _TEMPO3 else nchan,
        resetlen=nchan * 14 - 1,
        deftempo=found.get("DEFAULTTEMPO", 5),
        adparam=found.get("ADPARAM", 0x0F),
        srparam=found.get("SRPARAM", 0),
        firstnohr=found.get("FIRSTNOHRINSTR", 0),
        firstlegato=found.get("FIRSTLEGATOINSTR", 0),
        firstwave=found.get("FIRSTWAVEPARAM", 0),
        gateparam=found.get("GATETIMERPARAM", 0),
        buffered=buffered,
        ghosted=ghosted,
        fullghost="mt_copyregs" in found.passed,
        directwave=not _define(found, "NODIRECTWAVE"),
        songs=_define(found, "NUMSONGS") > 1,
        hrinstr=_define(found, "NUMHRINSTR") > 0,
        nohrinstr=_define(found, "NUMNOHRINSTR") > 0,
        legato=_define(found, "NUMLEGATOINSTR") > 0,
        effectnumop="mt_effectnum" in found.passed,
        alt=found.name.endswith("altplayer"),
        stereo=stereo,
        vibstop=False,
        novibinit=False,
        exec=tuple(range(nchan) if exec_channels is None else exec_channels),
    )
    return out


class Binder:
    """The spec bound in place to the playroutine in a tune's own memory.

    ``code`` is the ``(start, end)`` of the playroutine in the tune's image and
    ``idle`` the channels its play does not run. A binder is the ``bind`` of a
    :class:`~pysidtracker.playspec.SpecPlayer` (``binder(view, entry)``) and
    ``binder.entries`` its ``entries``; :meth:`match` is the playroutine source
    matched in the code.
    """

    def __init__(self, code, idle=()):
        self.code, self.idle = tuple(code), tuple(idle)
        self._found = None

    def match(self, view) -> Match:
        """The playroutine source matched in ``view``."""
        if self._found is None:
            self._found = match(view, *self.code)
        if self._found is None:
            raise SidParseError("no GoatTracker 2 playroutine source matches the code")
        return self._found

    def __call__(self, view, entry: int) -> dict:  # pylint: disable=unused-argument
        """The spec layout of the playroutine in ``view``."""
        found = self.match(view)
        nchan = found.get("NUMCHANNELS", 3)
        return layout(found, [c for c in range(nchan) if c not in self.idle])

    def entries(self, view) -> set:
        """The play entry of the playroutine in ``view``."""
        return {self.match(view)["mt_play"]}


def binder(model) -> Binder:
    """The :class:`Binder` of a :class:`~pygoattracker.sid.DecompiledSid`'s own image."""
    code = model.info.code
    if code is None or code[0] is None:
        raise SidParseError("the decompiled tune has no playroutine code span")
    return Binder(code, model.info.playback.idle)


def spec_binding(model) -> SpecBinding:
    """The spec of the decompiled ``model`` bound to its own tune image, which
    ``SidFormat.spec_player`` plays in place."""
    if model.image is None:
        raise SidParseError("the decompiled tune has no image")
    bound = binder(model)
    return SpecBinding(
        spec(),
        bound,
        model.image.image,
        bound.entries,
        model.start,
        model.header.is_psid,
    )
