"""``scripts/survey_tally.py``: verdict tallies and baseline transitions."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import survey_tally  # noqa: E402  pylint: disable=wrong-import-position

FIELDS = "key,path,song,verdict,min_bad\n"


def _results(tmp_path, name, rows):
    path = tmp_path / name
    path.write_text(FIELDS + "".join(f"{r},p{r},1,{v},{b}\n" for r, v, b in rows))
    return path


def test_transitions_count_moves_and_list_lost_exacts(tmp_path, capsys):
    base = _results(
        tmp_path,
        "base.csv",
        [("a", "exact", ""), ("b", "differs", "3"), ("c", "exact", "")],
    )
    new = _results(
        tmp_path,
        "new.csv",
        [
            ("a", "exact", ""),
            ("b", "exact", ""),
            ("c", "differs", "1"),
            ("d", "exact", ""),
        ],
    )
    keys = tmp_path / "keys.txt"
    keys.write_text("b c\n")
    survey_tally.main([str(new), str(keys), "--base", str(base)])
    out = capsys.readouterr().out
    assert "| differs | exact | 1 |" in out
    assert "| missing | exact | 1 |" in out
    assert "exact -> non-exact: 1" in out and "  c pc 1 differs" in out
    assert "{'differs': 1, 'exact': 1}" in out
    moves, lost = survey_tally.transitions(
        survey_tally._read(base), survey_tally._read(new)
    )
    assert moves[("exact", "exact")] == 1 and lost == ["c"]
