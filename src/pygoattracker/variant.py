"""Player variants as pysidtracker facets.

GT1 releases differ by named behaviours (:data:`RELEASE_FACETS`); a packed GT2
player's :class:`~pygoattracker.packed.PlayerBuild` is a tri-state facet set whose
unshown defines fall back to ``default``; the song-data heuristics that stand in for
an unshown define are ``inferred``.
"""

from pysidtracker.facets import DEFAULT_SOURCE, Entry, inferred

# Playroutine releases that differ in behaviour, by facet. Releases before V1.25 are
# musicroutine 11.1 with edits, oldest first, each later one changing one behaviour.
PRE125 = ("0.96", "1.01", "1.1", "1.15", "1.16", "1.21", "1.22")
RELEASE_FACETS = {
    # V1.4x playroutines that start a note one frame after the hard restart.
    "hardrestart_1frame": ("1.4b-hr1",),
    "filter_step0": ("1.50", "1.50b"),
    "orderlist_loop_after": ("1.50",),
    "sfx": ("11.2", "11.4"),
    "timing": ("11.3", "11.4", "11.5"),
    "orderlist_endsong": PRE125[:3],
    "pulse_always": PRE125[:4],
    "init_wave": PRE125[:5],
    "init_gate": PRE125[:6],
    "pulsedir_reset": PRE125,
    "strip_timing": ("11.1", "11.2"),
    "author": ("11.3", "11.4", "11.5"),
}


def release_refs(facet):
    """The releases whose behaviour ``facet`` is on."""
    return frozenset(RELEASE_FACETS[facet])


def tristate_entry(value, default):
    """A facet entry: ``value`` where the player shows it (``inferred`` from the
    probe), else ``default`` with ``default`` provenance."""
    if value is None:
        return Entry(default, DEFAULT_SOURCE)
    return Entry(value, inferred("probe"))


def heuristic_entry(shown, guess, evidence):
    """The entry for a define: ``shown`` where the player shows it, else ``guess()``
    from song data, ``inferred`` from ``evidence``."""
    if shown is not None:
        return Entry(shown, inferred("probe"))
    return Entry(guess(), inferred(evidence))
