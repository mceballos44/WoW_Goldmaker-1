"""Protease cleavage rules (C-terminal or N-terminal to the listed residues).

Each rule: residues where the enzyme cuts, which side, and residues that block
the cut when they sit on the other side (e.g. Pro after K/R for trypsin).
"""

ENZYMES = {
    # Trypsin, classic Keil rule: after K/R, not before P
    "trypsin":      {"sites": "KR",  "side": "C", "blocked_by": "P", "label": "Trypsin (K/R, not before P)"},
    # Trypsin ignoring the proline rule (closer to what MS search engines often allow)
    "trypsin_noP":  {"sites": "KR",  "side": "C", "blocked_by": "",  "label": "Trypsin/P (K/R, any)"},
    "lysc":         {"sites": "K",   "side": "C", "blocked_by": "",  "label": "Lys-C (K)"},
    "argc":         {"sites": "R",   "side": "C", "blocked_by": "P", "label": "Arg-C (R, not before P)"},
    "gluc":         {"sites": "E",   "side": "C", "blocked_by": "P", "label": "Glu-C (E, bicarbonate)"},
    "aspn":         {"sites": "D",   "side": "N", "blocked_by": "",  "label": "Asp-N (before D)"},
    "chymotrypsin": {"sites": "FWYL", "side": "C", "blocked_by": "P", "label": "Chymotrypsin (F/W/Y/L, not before P)"},
}
