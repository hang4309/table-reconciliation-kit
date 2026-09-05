# Validation scope — 2026-09-05

The standalone publication copy was run on Windows with Python 3.14.6 using `python -B run_example.py` after copying the source into its own directory. No third-party runtime packages were installed for the example.

Observed result:

```json
{
  "source_rows": 6,
  "entity_status": {
    "A001": "matched",
    "B002": "conflict",
    "C003": "unmatched",
    "D004": "unmatched"
  },
  "inputs_unchanged": true
}
```

The runner checks both aggregate status counts and each SKU's status against explicit expectations. It hashes the three example inputs before and after execution. It also invokes the inherited engine's `verify` command; that internal check is separate from the independent example expectations.

A second analysis agent read the example, license, and relevant matching, ingest, configuration, engine and local-web source. No confirmed private-data leak, credential, external-service dependency or blocking exact-key matching defect was identified in that bounded review. This does not establish whole-engine security or correctness.

Not validated by this release: real customer files, scale, fuzzy-match quality, full browser review actions, complete XLSX round trips, Linux/macOS execution, or accounting/financial correctness. No customer savings or revenue were measured.

Reproduce the declared example with `python -B run_example.py`. The included Python code retains the original engine's Python 3.10+ syntax requirement; the stated minimum is not a claim that every supported Python version was tested.
