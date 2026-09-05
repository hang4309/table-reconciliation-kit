# Table Reconciliation Kit

**Find differences between two data exports—even when their totals match.**

Compare small CSV exports in your browser, or use the Python workflow for configured reconciliation and reports. No account, API key, model or subscription is required.

## Compare two CSV files without installing anything

[Open the browser tool / 浏览器直接使用](https://hang4309.github.io/table-reconciliation-kit/csv-diff/) · [Download the offline browser package](https://github.com/hang4309/table-reconciliation-kit/releases/download/v0.2.0/csv-diff-browser-v0.2.0.zip)

The interface is in Chinese. Select two UTF-8 CSV files, map their key columns and up to three comparison-column pairs, then review matched, different, one-sided and ambiguous records. Files stay in the current browser. Blank or duplicate keys require manual review; values are compared as exact text. Maximum declared input: 5 MiB and 10,000 data records per file.

The browser tool is a separate, narrow JavaScript implementation. It does not support XLSX, JSON, fuzzy matching, numeric tolerances or the Python engine’s review workflow. [Instructions and validation limits](docs/csv-diff/README.md) · [Browser tool provenance](docs/csv-diff/PROVENANCE.md).

## Python workflow

The original reproducible Python example remains available. Its runtime uses the Python standard library.

## Try the example

Install Python 3.10 or later, download this repository, and run from its directory:

```sh
python -B run_example.py
```

The command creates a new output directory, compares the included synthetic inventory CSV and JSON, checks the expected classification, and prints the local HTML report path. It does not change the input files.

| SKU | Warehouse export | ERP export | Result |
|---|---:|---:|---|
| A001 | 10 | 10 | Matched |
| B002 | 20 | 18 | Quantity conflict |
| C003 | 5 | — | Only in warehouse export |
| D004 | — | 7 | Only in ERP export |
| **Total** | **35** | **35** | Equal totals do not imply matching records |

Expected result: **1 matched entity, 1 conflict, 2 unmatched entities** from six source rows. The two unmatched entities represent records present in only one source; they are not proof that either source is wrong.

Open `docs/index.html` locally for the Chinese visual case study, or read [the short article](docs/equal-totals.zh-CN.md).

## Use your own exports

Copy `examples/equal-totals/config.json` into a separate working folder. Set source paths, column mappings, field types, comparison fields, and the record keys. Relative source paths are resolved against the configuration directory. Then run:

```sh
python -B -m dqrdesk validate --config path/to/config.json
python -B -m dqrdesk run --config path/to/config.json --output path/to/new-result
python -B -m dqrdesk status --run path/to/new-result
```

The example uses exact and normalized SKU/location key rules, with fuzzy matching disabled. Review key normalization and duplicate keys before using different data. Reports preserve record references and field provenance. A preferred value in a combined entity can come from source priority; **a conflict still requires a human decision and is not an approved correction**.

This release focuses on comparison and reporting. The inherited engine also contains review operations, a local web interface and XLSX support; these are not covered by the published example's acceptance claim. Do not expose the local review server to the internet or treat this release as a hosted financial system.

## Want a result instead of configuring code?

The initial service scope is a **one-batch data reconciliation report**: agree on two CSV/JSON exports and their shared key, receive matched/conflicting/one-sided records, field provenance, and a concise report. The [US$49 pilot offer and scope](SERVICES.md) cover a small agreed batch; no order is accepted automatically. Automatic correction, live integrations, OCR and ongoing hosting are separate work.

For a scope and quote, [open a reconciliation request](https://github.com/hang4309/table-reconciliation-kit/issues/new?template=reconciliation-request.yml). Start with column names, approximate row counts, and a made-up example only. **GitHub issues are public: do not upload private files, customer records, credentials or payment details.** Actual files require a separately agreed private handoff. A request does not book work or trigger a payment.

This is an initial service experiment. No customer outcomes, response SLA, savings or revenue are claimed.

## Evidence and limitations

- On 2026-09-05, a fresh synthetic six-row example was run through the CLI. Its status counts were independently compared with the explicit table above. File outputs and the engine's internal verification were also exercised.
- The standalone package has its own rerun evidence in [VALIDATION.md](VALIDATION.md).
- No real customer dataset, load test, fuzzy-match quality study, full UI acceptance or complete XLSX round-trip acceptance has been performed for this release.
- This project was selected from a larger collection of AI-generated assets. See [PROVENANCE.md](PROVENANCE.md) for what was reused and what was checked.

MIT licensed; preserve the copyright notice in `LICENSE`. See [THIRD_PARTY.md](THIRD_PARTY.md).

## 中文说明

用途是把两份数据里需要核对的地方找出来。可以自行下载运行，也可以通过上方需求入口说明字段、行数和希望得到的结果。页面与案例采用合成库存数据，没有客户交易或虚构的省时成绩。接手已有文件时，先确认字段映射和记录键，再核对样本；有争议的记录保留人工决定。

## 看案例与真实操作

[六图、36秒讲解和实际工具录屏](https://hang4309.github.io/table-reconciliation-kit/learn/equal-totals/) · [内容及输入下载包](https://github.com/hang4309/table-reconciliation-kit/releases/download/v0.3.0/equal-totals-content-v0.3.0.zip)

相同的35合计为何仍有三处明细待处理：六行合成输入、真实导出结果、可改编图文和各渠道投稿稿。视频无音轨；AI辅助制作，没有客户效果或收益声明。
