"""6502 branch coverage of each playroutine build over songs, and its gaps.

Each song plays whole on the spec and on the build's 6502 (a jennings
``CoverageMPU``); the gaps are the player's instructions never run and its branch
directions never taken or never fallen through.
"""

from __future__ import annotations

import argparse
import bisect
import sys
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

import numpy as np
from jennings.coverage import EXEC, FALLTHRU, TAKEN, CoverageMap, CoverageMPU
from jennings.coverage.listing import Listing
from jennings import memory as jennings_memory
from jennings.devices import mpu6502
from jennings.isa import is_branch
from tqdm import tqdm
from vagrant_moses import machine as vagrant_machine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spec_memcheck import Host, compare  # noqa: E402  pylint: disable=C0413

_BRANCH = np.array([is_branch(op) for op in range(256)], bool)


@contextmanager
def recording(cov: CoverageMap):
    """Every MPU built inside records its execution into ``cov``; the coverage
    engine is Python, so the native cores are off."""

    class Recording(CoverageMPU):  # pylint: disable=too-few-public-methods
        """A ``CoverageMPU`` sharing ``cov``."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.cov = cov

    with (
        mock.patch.object(mpu6502, "MPU", Recording),
        mock.patch.object(jennings_memory, "NATIVE", False),
        mock.patch.object(vagrant_machine, "NATIVE", False),
    ):
        yield


class Covered(Host):
    """:class:`spec_memcheck.Host` recording its 6502's execution into ``cov``."""

    def __init__(self, data: bytes, subtune: int, cov: CoverageMap):
        self.cov = cov
        super().__init__(data, subtune)

    def _init(self, subtune: int) -> None:
        with recording(self.cov):
            super()._init(subtune)


@dataclass(frozen=True)
class Gaps:
    """Coverage of a build's code: ``directions`` covered of ``total`` branch
    directions; the ``unexecuted`` instructions and the branches never taken
    (``untaken``) or never fallen through (``unfallen``), as address tuples."""

    unexecuted: tuple
    untaken: tuple
    unfallen: tuple
    directions: int
    total: int

    def named(self) -> dict:
        """``{address: kinds}`` of every gap, in address order."""
        out: dict = {}
        for kind in ("unexecuted", "untaken", "unfallen"):
            for addr in getattr(self, kind):
                out.setdefault(addr, []).append(kind)
        return dict(sorted(out.items()))


def gaps(view: bytes, cov: CoverageMap, span: tuple, entries) -> Gaps:
    """The gaps of the code in ``span`` ``[start, end)`` of ``view`` reached by
    recursive descent from ``entries`` and every instruction ``cov`` ran."""
    start, end = span
    mem = np.frombuffer(bytes(view), np.uint8)
    addrs = Listing.disassemble(mem, entries, cov).lines[:, 1]
    addrs = addrs[(addrs >= start) & (addrs < end)]
    flags = cov.flags[addrs]
    branch = _BRANCH[mem[addrs]]
    ran = (flags & EXEC) != 0
    taken = branch & ((flags & TAKEN) != 0)
    fell = branch & ((flags & FALLTHRU) != 0)
    return Gaps(
        tuple(addrs[~ran].tolist()),
        tuple(addrs[branch & ran & ~taken].tolist()),
        tuple(addrs[branch & ran & ~fell].tolist()),
        int(taken.sum() + fell.sum()),
        int(2 * branch.sum()),
    )


def where(addr: int, labels: dict, marks=None) -> str:
    """``label+offset`` of ``addr`` from the nearest ``labels`` entry at or
    before it (``marks``: ``sorted(labels)``)."""
    marks = sorted(labels) if marks is None else marks
    k = bisect.bisect_right(marks, addr) - 1
    return f"{labels[marks[k]]}+{addr - marks[k]}" if k >= 0 else f"{addr:04X}"


def keys(found: Gaps, labels: dict) -> set:
    """Each gap of ``found`` as ``label+offset kind``."""
    marks = sorted(labels)
    return {
        f"{where(a, labels, marks)} {kind}"
        for a, kinds in found.named().items()
        for kind in kinds
    }


def listing_lines(
    view: bytes, cov: CoverageMap, entries, addrs, labels, context: int = 0
) -> list:
    """The disassembly line of each address in ``addrs`` (and the ``context``
    lines before it), after the nearest ``labels`` (``{address: name}``) entry
    at or before it."""
    listing = Listing.disassemble(np.frombuffer(bytes(view), np.uint8), entries, cov)
    text = listing.source.splitlines()
    marks = sorted(labels)
    out = []
    for a in addrs:
        line = listing.line(a)
        before = [f"{'':49}{t}" for t in text[max(line - 1 - context, 0) : line - 1]]
        out.append(
            "\n".join([*before, f"{where(a, labels, marks):28} {text[line - 1]}"])
        )
    return out


def run(case) -> tuple:
    """Play ``case`` (a :class:`tests._coverage.Case`) whole on the spec and the
    6502: :func:`spec_memcheck.compare`'s result and the 6502's ``(flags, edges,
    entries)``; None for a corpus song another build plays."""
    from tests import _coverage  # pylint: disable=import-outside-toplevel

    ready = _coverage.played(case)
    if ready is None:
        return None
    cov = CoverageMap()
    cpu = Covered(ready.sid, ready.subtune, cov)
    result = compare(ready.spec(), cpu, ready.frames)
    return dict(case=case.name, **result), (cov.flags, cov.edges, cov.entries)


def merged(runs) -> tuple:
    """``(differences, cov)``: the :func:`run` results ``runs`` whose spec and
    6502 differ, and one map of every run's coverage."""
    cov, differ = CoverageMap(), []
    for result, (flags, edges, entries) in filter(None, runs):
        if result["frame"] != "ok":
            differ.append(result)
        np.bitwise_or(cov.flags, flags, out=cov.flags)
        cov.edges |= edges
        cov.entries |= entries
    return differ, cov


def measure(build, cases, jobs: int = 1) -> tuple:
    """``(gaps, differences, cov)`` of ``build`` over ``cases``, played in ``jobs``
    processes."""
    from tests import _proof_songs  # pylint: disable=import-outside-toplevel

    with ProcessPoolExecutor(
        jobs, initializer=_proof_songs.preload, initargs=(_proof_songs.loaded(),)
    ) as pool:
        differ, cov = merged(
            tqdm(pool.map(run, cases), total=len(cases), unit="song", desc=build.name)
        )
    view = build.view()
    return gaps(view, cov, build.span(), build.entries()), differ, cov


def main() -> None:
    """Report each build's branch-direction coverage and list its gaps."""
    from tests import _coverage, _gt1_code  # pylint: disable=C0415

    _gt1_code.register_all()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="append", help="a build (default: all)")
    parser.add_argument("--corpus", action="store_true", help="add the corpus songs")
    parser.add_argument("--baseline", action="store_true", help="the corpus songs only")
    parser.add_argument(
        "--tunes", action="store_true", help="the oracle tunes' own playroutines"
    )
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--context", type=int, default=0, help="lines before a gap")
    args = parser.parse_args()
    chosen = _coverage.TUNE_BUILDS if args.tunes else _coverage.builds(args.build)
    for build in chosen:
        found, differ, cov = measure(
            build,
            _coverage.cases(build, args.corpus or args.baseline, not args.baseline),
            args.jobs,
        )
        print(f"{build.name}: {found.directions}/{found.total} branch directions")
        for result in differ:
            print(f"  differs: {result}")
        named = found.named()
        lines = listing_lines(
            build.view(), cov, build.entries(), named, build.labels(), args.context
        )
        for line, kinds in zip(lines, named.values()):
            print(f"  {','.join(kinds):20} {line}")


if __name__ == "__main__":
    main()
