# Filing Signals

A self-updating public tracker that reads new SEC filings every weekday and
publishes four things that are otherwise hard to see:

| Signal | Source | Precision |
|---|---|---|
| **Restatements** | 8-K Item 4.02 — "Non-Reliance on Previously Issued Financial Statements" | SEC's own item code |
| **Auditor changes** | 8-K Item 4.01 — "Changes in Registrant's Certifying Accountant" | SEC's own item code |
| **Going concern** | ASC 205-40 note, compared against the company's previous filing | Derived |
| **Accounting policy / revenue recognition** | Newly *adopted* accounting standards; revenue-note rewrites | Derived (revenue marked beta) |

The whole thing runs on GitHub Actions and GitHub Pages. There is no server, no
database, and **no API key required** — the default configuration produces the
full deterministic feed at zero cost.

## What makes it different

Publishing "this filing mentions going concern" would be worthless: thousands
do, every quarter, unchanged for years. This tracker publishes **state
transitions** — the moment a company's disclosure actually changes — which
requires fetching and comparing the previous filing.

A worked example from development: Cyclerion Therapeutics' latest 10-Q contains
17 matches for "going concern"; its prior 10-Q contains 16. A keyword tracker
fires on this. Nothing changed, so this one correctly reports nothing.

## Quick start

```bash
python -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt
```

The SEC rejects unidentified automated traffic with `HTTP 403`, so a contact
string is mandatory:

```bash
export SEC_USER_AGENT="Your Name you@example.com"
```

Run the last five business days and build the site:

```bash
./.venv/bin/python -m pipeline.run --days 5
```

Open `public/index.html`. Other entry points:

```bash
./.venv/bin/python -m pipeline.run --date 2026-08-21   # one specific day
./.venv/bin/python -m pipeline.render                  # rebuild site only
./.venv/bin/python -m pytest tests/ -q                 # tests (no network)
```

## How it runs itself

Each run processes every business day from the last recorded date through the
last complete filing day — not just "today". A missed cron, an SEC block, or a
runner outage is therefore repaired by the next run instead of leaving a
permanent gap. Events are keyed by `(accession, signal_type)`, so re-running a
date never duplicates anything.

```
daily index  →  filing headers  →  universe filter  →  deterministic triage
                                                    ↘  prior-filing comparison
                                                        ↘  optional AI  →  site
```

## Going live

1. Push to a public GitHub repository.
2. Add repository secret **`SEC_USER_AGENT`** — a real `Name email@domain`.
   Without it every request returns 403.
3. Settings → Pages → Source: **GitHub Actions**.
4. Optionally add repository variable `SITE_URL` for absolute RSS links.
5. The workflow runs weekdays at 23:30 UTC, or on demand via
   *Actions → Daily filing scan → Run workflow*.

### Optional: plain-English analysis

Add an `ANTHROPIC_API_KEY` secret and the analysis layer activates with **no
code change** — the workflow installs the SDK only when the key is present.
It adds a summary to each event and screens beta text-diff findings for
cosmetic rewrites. Detection stays entirely deterministic either way: no model
decides whether something is a signal.

| Setting | Default | Purpose |
|---|---|---|
| `SEC_USER_AGENT` | *(required)* | Contact string sent to SEC |
| `SEC_RATE_LIMIT` | `5` | Requests/sec (SEC's ceiling is 10) |
| `ANTHROPIC_API_KEY` | *(unset)* | Enables analysis; unset = free |
| `MAX_BACKFILL_DAYS` | `10` | Cap on catch-up work per run |
| `POLICY_SIMILARITY_THRESHOLD` | `0.60` | Revenue-note rewrite sensitivity |

## Design notes

Things that were verified against live EDGAR rather than assumed, each of which
changed the implementation:

- **Range requests do not work.** `sec.gov` answers a ranged Archives request
  with `200` and the full body. Reading `<accession>.hdr.sgml` instead is ~1 KB
  versus ~195 KB and yields *numeric* item codes rather than prose titles.
- **Inline tags split words.** Filings wrap word fragments in `<span>`/XBRL
  tags; substituting a space produced `Item 1A. Ri sk Factors` and silently
  broke every text match.
- **XBRL `frames` is numeric-only** — it returns 404 for `TextBlock` tags, so
  policy text has to come from documents.
- **Proximity matching misreads going-concern notes.** Every ASC 205-40 note
  opens by reciting both outcomes as methodology, so classification keys on the
  conclusion sentence.
- **Reverse mergers fake transitions.** BOXABL Inc. (CIK 1906364, formerly
  FG Merger II Corp.) appeared to go from no disclosure to substantial doubt;
  the prior filing was a SPAC shell. Registrant changes are detected via
  `formerNames` and reported as new disclosures, not changes.
- **Listing an issued standard is not adopting it.** Diffing ASU mentions
  flagged 8 of 18 filings in one day; requiring actual adoption language fixed it.

## Caveats

Not investment advice. Every entry links to its source filing — verify there
before relying on anything. Reports only what companies disclosed; an Item 4.02
means previously issued statements should not be relied upon, which is not by
itself evidence of misconduct.

Data from [SEC EDGAR](https://www.sec.gov/edgar). Public domain.
