# sp_digest: whole-proteome in silico digest (S. purpuratus)

Answers: how well does trypsin (vs. other proteases) cut the sea urchin proteome into
peptides that bottom-up MS can detect?

> This folder is unrelated to WoW Goldmaker and lives here only temporarily; move it to
> its own repository when convenient. It has no dependencies on the rest of the repo.

## Parts

| Path | What it does |
|---|---|
| `sp_digest.py` | CLI. Reads a protein FASTA (`.fa` / `.gz`), digests every protein with each enzyme, writes TSV + JSON results. Pure Python 3.9+, no packages. |
| `enzymes.py` | Cleavage rules (sites, side, blocking residue). Add enzymes here. |
| `config/default.json` | All digest parameters (missed cleavages, length/mass window, isoform handling). Kept separate from the code; copy it to make a run-specific config. |
| `web/sp_digest.html` | Same rules in the browser: drop the FASTA, get the enzyme comparison, charts and a per-protein coverage map. Published as a private Claude artifact. |
| `tests/test_digest.py` | Unit tests for the cleavage rules, masses and isoform collapsing. |

## Getting the data

Echinobase → Download → *S. purpuratus* genome Sp5.0 → protein FASTA
(https://www.echinobase.org/echinobase/static-echinobase/ftpDatafiles.jsp, e.g. `sp5_0_GCF_protein.fa.gz`).
UniProt reference proteome UP000007110 is an alternative (`GN=` headers allow isoform collapsing).

## Run

```bash
python sp_digest.py sp5_0_GCF_protein.fa.gz --out results/sp5_trypsin
python sp_digest.py proteins.fa --config my_run.json --enzymes trypsin,lysc
python -m pytest sp_digest/tests -c /dev/null      # tests
```

Outputs: `enzyme_summary.tsv` (one row per enzyme), `per_protein_<enzyme>.tsv`
(length, cleavage sites, detectable peptides, unique peptides, coverage with and without
missed cleavages) and `summary.json` (adds coverage and peptide-length histograms, composition).

## Definitions

- **Detectable peptide**: length 7–30 aa and monoisotopic mass 600–4600 Da (config), up to N missed cleavages.
- **Coverage**: % of a protein's residues inside at least one detectable peptide.
- **Unique**: peptide occurs in exactly one protein of the analysed set (I/L folded).
- Unmodified masses; theoretical upper bound, not a prediction of what an instrument sees.
