# sp_digest TODO

- [ ] Run on the real Echinobase Sp5.0 protein FASTA (sandbox could not reach echinobase.org; run locally).
- [ ] Check the Sp5.0 header format; if headers carry no gene tag, add a protein→gene map (from the GFF) so isoform collapsing works.
- [ ] Optional: fixed carbamidomethyl-C / variable Met-ox in the mass window.
- [ ] Optional: compare against a human/mouse proteome as a reference point.
- [ ] Combined coverage for two-enzyme strategies (e.g. trypsin + Glu-C run separately).
- [ ] Digest QC script: read MaxQuant/FragPipe peptide tables, report missed-cleavage rate, specificity, observed vs. predicted coverage.
- [ ] Move folder to its own repository.

## Done
- [x] EVALUATING_PROTEASES.md explainer.
- [x] CLI + config + tests; browser version cross-checked against the CLI (identical median coverage on a synthetic set).
