#!/usr/bin/env python3
"""In silico digest of a whole proteome FASTA (e.g. the S. purpuratus protein set
from Echinobase) and summary of how well each protease covers it for bottom-up MS.

Usage:
    python sp_digest.py PROTEINS.fa [--config config/default.json] [--out results/]

Outputs (in --out):
    per_protein_<enzyme>.tsv   one row per protein: length, peptides, coverage, unique peptides
    enzyme_summary.tsv         one row per enzyme: proteome-wide statistics
    summary.json               the same plus histograms, for the web viewer / reports

No third-party dependencies (Python 3.9+).
"""
import argparse
import gzip
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from enzymes import ENZYMES

# Monoisotopic residue masses (Da)
MONO = {
    "G": 57.02146, "A": 71.03711, "S": 87.03203, "P": 97.05276, "V": 99.06841,
    "T": 101.04768, "C": 103.00919, "L": 113.08406, "I": 113.08406, "N": 114.04293,
    "D": 115.02694, "Q": 128.05858, "K": 128.09496, "E": 129.04259, "M": 131.04049,
    "H": 137.05891, "F": 147.06841, "R": 156.10111, "Y": 163.06333, "W": 186.07931,
    "U": 150.95364, "O": 237.14773,
}
WATER = 18.01056


def read_fasta(path):
    """Yield (header, sequence). Handles .gz and stray '*' stop symbols."""
    opener = gzip.open if str(path).endswith(".gz") else open
    header, chunks = None, []
    with opener(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if line.startswith(">"):
                if header is not None:
                    yield header, "".join(chunks)
                header, chunks = line[1:], []
            else:
                chunks.append(line.upper().replace("*", ""))
    if header is not None:
        yield header, "".join(chunks)


def keep_longest_isoform(records, isoform_regex):
    """Collapse isoforms to the longest sequence per gene, when the header names a gene."""
    pat = re.compile(isoform_regex) if isoform_regex else None
    best, order, no_gene = {}, [], []
    for header, seq in records:
        m = pat.search(header) if pat else None
        if not m:
            no_gene.append((header, seq))
            continue
        gene = m.group(1)
        if gene not in best:
            order.append(gene)
            best[gene] = (header, seq)
        elif len(seq) > len(best[gene][1]):
            best[gene] = (header, seq)
    return [best[g] for g in order] + no_gene


def cleavage_sites(seq, enzyme):
    """Return sorted cut positions (index of the first residue of each new fragment)."""
    rule = ENZYMES[enzyme]
    sites, blocked, n = rule["sites"], rule["blocked_by"], len(seq)
    cuts = []
    if rule["side"] == "C":
        for i in range(n - 1):
            if seq[i] in sites and seq[i + 1] not in blocked:
                cuts.append(i + 1)
    else:  # N-terminal side: cut before the residue
        for i in range(1, n):
            if seq[i] in sites and seq[i - 1] not in blocked:
                cuts.append(i)
    return cuts


def digest(seq, enzyme, missed_cleavages):
    """Yield (start, end, n_missed) for every peptide with up to `missed_cleavages`."""
    bounds = [0] + cleavage_sites(seq, enzyme) + [len(seq)]
    for i in range(len(bounds) - 1):
        for mc in range(missed_cleavages + 1):
            j = i + 1 + mc
            if j >= len(bounds):
                break
            yield bounds[i], bounds[j], mc


def peptide_mass(pep):
    """Monoisotopic neutral mass, or None if the peptide has non-standard residues (X, B, Z)."""
    try:
        return sum(MONO[a] for a in pep) + WATER
    except KeyError:
        return None


def analyse_enzyme(proteins, enzyme, p):
    """Digest every protein; return per-protein rows and the proteome summary."""
    # pass 1: detectable peptides per protein, and which proteins each peptide occurs in
    pep_owner_count = Counter()
    per_prot_peps = []
    all_lengths, mc0_lengths = Counter(), Counter()
    for _, seq in proteins:
        detect = {}
        for s, e, mc in digest(seq, enzyme, p["missed_cleavages"]):
            pep = seq[s:e]
            L = e - s
            if mc == 0:
                mc0_lengths[min(L, 60)] += 1
            if not (p["min_length"] <= L <= p["max_length"]):
                continue
            mass = peptide_mass(pep)
            if mass is None or not (p["min_mass"] <= mass <= p["max_mass"]):
                continue
            # I/L are isobaric, so treat them as the same for uniqueness
            key = pep.replace("I", "L")
            detect.setdefault(key, []).append((s, e, mc))
        for key in detect:
            pep_owner_count[key] += 1
        per_prot_peps.append(detect)
        for key, spans in detect.items():
            if any(mc == 0 for *_, mc in spans):
                all_lengths[len(key)] += 1

    rows = []
    for (header, seq), detect in zip(proteins, per_prot_peps):
        covered = bytearray(len(seq))
        covered_mc0 = bytearray(len(seq))
        n_unique = n_mc0 = 0
        for key, spans in detect.items():
            if pep_owner_count[key] == 1:
                n_unique += 1
            for s, e, mc in spans:
                covered[s:e] = b"\x01" * (e - s)
                if mc == 0:
                    covered_mc0[s:e] = b"\x01" * (e - s)
                    n_mc0 += 1
        L = len(seq) or 1
        rows.append({
            "id": header.split()[0],
            "length": len(seq),
            "n_sites": len(cleavage_sites(seq, enzyme)),
            "detectable_peptides_mc0": n_mc0,
            "detectable_peptides": len(detect),
            "unique_peptides": n_unique,
            "coverage_mc0_pct": round(100 * sum(covered_mc0) / L, 2),
            "coverage_pct": round(100 * sum(covered) / L, 2),
            "description": header[len(header.split()[0]):].strip(),
        })

    cov = [r["coverage_pct"] for r in rows]
    thr = p["unique_peptide_threshold"]
    total_res = sum(r["length"] for r in rows) or 1
    summary = {
        "enzyme": enzyme,
        "label": ENZYMES[enzyme]["label"],
        "proteins": len(rows),
        "median_coverage_pct": round(statistics.median(cov), 2) if cov else 0,
        "mean_coverage_pct": round(statistics.mean(cov), 2) if cov else 0,
        "median_coverage_mc0_pct": round(statistics.median(r["coverage_mc0_pct"] for r in rows), 2) if rows else 0,
        "residue_coverage_pct": round(100 * sum(r["coverage_pct"] * r["length"] / 100 for r in rows) / total_res, 2),
        "pct_proteins_ge_thr_unique": round(100 * sum(r["unique_peptides"] >= thr for r in rows) / max(len(rows), 1), 2),
        "pct_proteins_no_detectable": round(100 * sum(r["detectable_peptides"] == 0 for r in rows) / max(len(rows), 1), 2),
        "distinct_detectable_peptides": len(pep_owner_count),
        "pct_peptides_unique": round(100 * sum(v == 1 for v in pep_owner_count.values()) / max(len(pep_owner_count), 1), 2),
        "coverage_histogram": histogram(cov, 0, 100, 5),
        "mc0_length_histogram": dict(sorted(mc0_lengths.items())),
    }
    return rows, summary


def histogram(values, lo, hi, step):
    edges = list(range(lo, hi + step, step))
    counts = [0] * (len(edges) - 1)
    for v in values:
        idx = min(int((v - lo) // step), len(counts) - 1)
        counts[idx] += 1
    return {"edges": edges, "counts": counts}


def composition(proteins):
    c = Counter()
    for _, seq in proteins:
        c.update(seq)
    total = sum(c.values()) or 1
    return {aa: round(100 * n / total, 3) for aa, n in c.most_common()}


def write_tsv(path, rows):
    if not rows:
        return
    cols = list(rows[0])
    with open(path, "w") as fh:
        fh.write("\t".join(cols) + "\n")
        for r in rows:
            fh.write("\t".join(str(r[c]) for c in cols) + "\n")


def main(argv=None):
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fasta", help="protein FASTA (.fa/.fasta, optionally .gz)")
    ap.add_argument("--config", default=here / "config" / "default.json")
    ap.add_argument("--out", default="sp_digest_results")
    ap.add_argument("--enzymes", help="comma-separated override of config enzymes")
    args = ap.parse_args(argv)

    p = json.loads(Path(args.config).read_text())
    enzymes = args.enzymes.split(",") if args.enzymes else p["enzymes"]
    unknown = [e for e in enzymes if e not in ENZYMES]
    if unknown:
        sys.exit(f"Unknown enzyme(s): {unknown}. Known: {list(ENZYMES)}")

    proteins = [(h, s) for h, s in read_fasta(args.fasta) if s]
    n_raw = len(proteins)
    if p.get("longest_isoform_only"):
        proteins = keep_longest_isoform(proteins, p.get("isoform_regex"))
    print(f"{n_raw} sequences read, {len(proteins)} after isoform collapsing", file=sys.stderr)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    summaries = []
    for enz in enzymes:
        rows, summ = analyse_enzyme(proteins, enz, p)
        write_tsv(out / f"per_protein_{enz}.tsv", rows)
        summaries.append(summ)
        print(f"{enz:13s} median coverage {summ['median_coverage_pct']:6.2f}%  "
              f">= {p['unique_peptide_threshold']} unique peptides: {summ['pct_proteins_ge_thr_unique']:6.2f}%",
              file=sys.stderr)

    flat = [{k: v for k, v in s.items() if not isinstance(v, dict)} for s in summaries]
    write_tsv(out / "enzyme_summary.tsv", flat)
    (out / "summary.json").write_text(json.dumps({
        "input": str(args.fasta), "sequences_read": n_raw, "proteins_analysed": len(proteins),
        "parameters": p, "composition_pct": composition(proteins), "enzymes": summaries,
    }, indent=1))
    print(f"Results written to {out}/", file=sys.stderr)


if __name__ == "__main__":
    main()
