# Browser CSV comparison tool — provenance and validation

This single-file browser tool was created with AI assistance in 2026. Its workflow references asset 0124, Data Quality Reconciliation Desk: exact keys, at most one record per source for automatic pairing, and separate conflict, one-sided and ambiguous results. Its JavaScript was newly written; it does not copy the Python engine and is not a full port.

The browser tool is released under the MIT notice in this folder. Its built-in inventory example is synthetic. No customer data, original demand text, credentials or private machine paths are included.

On 2026-09-05, the local version underwent a separate-agent Chrome UI check with newly constructed inputs. Checks covered the synthetic example, different left/right column names, quoted commas and embedded newlines, duplicate/blank keys, malformed quotes and invalid UTF-8, record field counts, and invalidating stale results after changed input. A CSV sample from internal asset 1792 also exercised commas, quotes and an embedded newline; it is not included in this package.

The published version changes explanatory HTML only. Its script and style blocks are byte-identical to that verified local version. Publication checks cover the new entry links and a browser smoke test. Declared limits are 5 MiB and 10,000 data records per file, with 500 result rows displayed and all results exported. The maximum capacity and every browser have not been tested. No real customer files, measured savings, accounting certification or business outcomes are claimed.
