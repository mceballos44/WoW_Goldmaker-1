# How to evaluate how well a protease works

A protease can be judged at two levels. **In silico** analysis says what the enzyme
*could* give you on this proteome. **Experimental** analysis says what it *did* give you
in a real digest. You need both. A protease can look good on paper and still digest
poorly in practice, for example because of buffer, denaturation, time or enzyme:substrate ratio.

## 1. In silico: is the enzyme a good fit for the proteome?

`sp_digest.py` and `web/sp_digest.html` report these. Compare enzymes on the same settings.

| Metric | What it tells you | Good sign |
|---|---|---|
| Median coverage per protein | How much of a typical protein falls into detectable peptides (7–30 aa, 600–4600 Da) | High, e.g. >70% |
| Coverage with no missed cleavages | Same, if the enzyme cut perfectly | Close to the value with missed cleavages |
| % residues covered | Coverage weighted by protein length | High |
| % proteins with ≥2 unique peptides | Proteins you could identify confidently | Close to 100% |
| % proteins with no detectable peptide | Proteins this enzyme cannot see | Close to 0% |
| % unique peptides | Peptides that point to one protein only (no ambiguity) | High |
| Peptide length distribution | Whether fragments fall in the MS-friendly window | Most peptides between 7 and 30 aa |
| Cleavage sites per protein | Too few gives long peptides; too many gives short, non-unique peptides | Moderate |

What these numbers mean for trypsin: K+R is about 10–12% of residues in most proteomes, so the
average tryptic peptide is about 9–10 aa long. That is why trypsin is the default enzyme. Proteins or
regions with few K/R (membrane proteins, some collagens, Pro-rich or acidic
domains) give long peptides that trypsin misses. Use the one-protein view to find these cases.
A second enzyme (Glu-C, Asp-N, chymotrypsin, Lys-C) then fills the gaps. For two enzymes
used together, coverage of the combination is what matters, not either enzyme alone. That
combined analysis is on the TODO list.

## 2. Experimentally: did the digest work?

Digest a standard first: BSA, a commercial HeLa or yeast digest standard, or a pooled
urchin lysate you reuse every time. Run LC-MS/MS and search with these settings:

1. **Specific search** (e.g. Trypsin/P, up to 2 missed cleavages).
2. **Semi-specific search**, where only one end of the peptide has to match the rule.
3. Optionally a **non-specific search** on the standard only.

Then look at these numbers:

| Metric | How to get it | Typical target for trypsin |
|---|---|---|
| **Missed-cleavage rate** | % of identified peptides with ≥1 missed cleavage. Better: share of MS1 *intensity* in missed-cleavage peptides | <10–20% of peptides |
| **Specificity** | % of PSMs that are fully specific (semi-specific search: fully specific / all) | >90% |
| **Non-specific cleavage** | % semi- and non-specific peptides, which point to chymotryptic activity, in-source fragmentation or degradation | Low; use TPCK-treated trypsin |
| **Peptide and protein IDs** | Numbers at 1% FDR | Compare between enzymes or protocols on the same input amount |
| **Sequence coverage** | Coverage of the identified proteins; compare with the in silico value for the same proteins | Observed close to predicted |
| **Reproducibility** | CV of peptide intensities across replicate digests | CV <20% |
| **Autolysis peptides** | Trypsin's own peptides; a large amount means too much enzyme or degraded enzyme | Small |
| **Artefacts** | Deamidation (N/Q), carbamylation from urea, Met oxidation, as variable modifications | Low |

Useful experiments:
- **Time course** (e.g. 1, 4, 16 h): the missed-cleavage rate should drop and then level off. Choose
  the earliest point where it levels off.
- **Enzyme:substrate ratio** (1:100, 1:50, 1:20) and **denaturant** (urea vs. SDC vs.
  RapiGest vs. S-Trap). These usually matter more than the enzyme itself.
- **SDS-PAGE of an aliquot** before and after digestion: a quick check that no intact
  protein bands are left.
- **Lys-C then trypsin**: Lys-C cuts reliably even in 6–8 M urea, then trypsin finishes the
  digest after dilution. This usually lowers missed cleavages compared with trypsin alone.

## 3. Compare prediction with observation

The most useful view for the urchin work: take the identified peptides from a real run and
compare them with the in silico list from `per_protein_trypsin.tsv`.
- Peptides predicted and observed: the digest is working.
- Predicted but never observed (in abundant proteins): likely ionisation or retention
  limits, PTMs, or cleavage blocked by neighbouring acidic residues (D/E next to K/R).
- Observed but not predicted: missed or non-specific cleavages, or wrong gene models.
  Wrong gene models matter for a non-model organism like *S. purpuratus*.

A script that reads search-engine output (e.g. MaxQuant `peptides.txt`, FragPipe
`peptide.tsv`) and reports these numbers is on the TODO list.
