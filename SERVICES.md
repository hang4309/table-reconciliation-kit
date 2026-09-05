# One-batch reconciliation service

An initial, outcome-based service offer for teams with two exports that should agree.

## Pilot scope

**Pilot quote: US$49 for one agreed small batch.** This is a test offer, not a claim of market demand or past sales. Final scope is confirmed before accepting work or payment.

Included scope:

- Two CSV or JSON files, up to 10,000 rows combined.
- One agreed unique record key, which may be composed of multiple columns.
- Up to five fields to compare, with explicit column mapping and simple whitespace/case normalization agreed in advance.
- A matched/conflict/one-sided record report, field provenance, and a concise written explanation.
- One correction round if the delivered report does not follow the agreed mapping and sample expectations.

The client receives files, not a hosted application. Original inputs remain unchanged. Ambiguous records remain visible for the client's decision.

## Scope discussion

1. [Open a request](https://github.com/hang4309/table-reconciliation-kit/issues/new?template=reconciliation-request.yml) with column names, approximate row counts, and made-up sample rows only.
2. Agree on keys, comparison rules, expected exceptions, deliverables, and timing.
3. Arrange a private, authorized file handoff and payment method separately. Do not send files or money through a public issue.

No order, payment or delivery deadline is automatically accepted. A real dataset must be reviewed before a quote is confirmed.

## Outside this pilot

Scanned documents/OCR; direct access to accounts or databases; fuzzy entity resolution; undocumented keys; accounting judgments; automatic changes to source systems; sensitive regulated datasets; recurring hosting and operational support. These require a separate decision and are not silently included in the small-batch price.

## 中文

试运营报价为每个约定小批次 49 美元：两份 CSV/JSON、合计不超过一万行、一个约定记录键、最多五个比较字段，交付差异与来源报告。先确认数据适配和范围再接受订单及付款。这个价格是用于验证需求的初始提议，当前没有成交或收入记录。
