import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sp_digest as d  # noqa: E402


def peps(seq, enz, mc=0):
    return [seq[s:e] for s, e, _ in d.digest(seq, enz, mc)]


def test_trypsin_proline_rule():
    assert peps("AAKPBBRCCK", "trypsin") == ["AAKPBBR", "CCK"]
    assert peps("AAKPBBRCCK", "trypsin_noP") == ["AAK", "PBBR", "CCK"]


def test_missed_cleavages():
    assert peps("AKBKCK", "trypsin", 1) == ["AK", "AKBK", "BK", "BKCK", "CK"]


def test_aspn_cuts_before_d():
    assert peps("AADBBDC", "aspn") == ["AA", "DBB", "DC"]


def test_mass():
    # GG: 2*57.02146 + water
    assert abs(d.peptide_mass("GG") - 132.05348) < 1e-4
    assert d.peptide_mass("GXG") is None


def test_isoform_collapse():
    recs = [("a gene=X", "AAA"), ("b gene=X", "AAAAA"), ("c", "C")]
    out = d.keep_longest_isoform(recs, r"gene=(\S+)")
    assert [h for h, _ in out] == ["b gene=X", "c"]
