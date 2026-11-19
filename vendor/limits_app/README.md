# Limits app (compiled copy)

`index.html` is the compiled Limits app ("Risk Tools": Risk and periodic audit, and Account limit upload) from
the `jdan24/limit_compare` repo, copied byte for byte from its `spa/dist/index.html`.
Lookout's **Limits** page shows it, one tool per tab (`reports/limits.py`).

- Source commit: `813d496` (Risk uploads: gross position column names, fixed
  Wholesale columns; IsvTeName first).
- **Never edit `index.html` here.** Change the Limits repo instead.
- Lookout's real-data check scans this file: the Limits app must keep client-derived
  names out of its labels, routes, storage keys and export file names.

To refresh it:

1. In the Limits repo, run `npm run build` in `spa/` (its own workflow commits
   `spa/dist/index.html`).
2. Copy `spa/dist/index.html` over `vendor/limits_app/index.html` in Lookout.
3. Update the source commit above, run the tests and commit.
