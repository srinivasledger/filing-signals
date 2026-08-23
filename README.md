# Filing Signals

**Live: <https://srinivasledger.github.io/filing-signals>** ·
[RSS](https://srinivasledger.github.io/filing-signals/feed.xml) ·
[JSON](https://srinivasledger.github.io/filing-signals/events.json)

Deployed elsewhere it lives at `https://<user>.github.io/<repo>/` — nothing in
the code is tied to an account or repository name.

A self-updating public tracker that reads new SEC filings every weekday and
publishes four things that are otherwise hard to see:

| Signal | Source | Sub-classification |
|---|---|---|
| **Restatements** | 8-K Item 4.02 | **(a)** management concluded vs **(b)** the auditor told them |
| **Auditor changes** | 8-K Item 4.01 | resigned vs dismissed; disagreements disclosed; predecessor → successor firm and whether that is a tier downgrade |
| **Late filings** | Form 12b-25 (NT 10-K / NT 10-Q) | whether the company anticipates a significant change in results; whether other reports are also outstanding |
| **Going concern** | ASC 205-40 note vs the previous filing | ladder: no conclusion → doubt alleviated → substantial doubt |
| **Accounting policy** | Newly *adopted* accounting standards (ASU) | adoption vs merely-issued |
| **Revenue recognition** | ASC 606 policy note vs the previous filing | beta |

The item code says an event happened; the sub-classification says which kind,
and the kind is usually the signal. An auditor resigning is not a company
rotating firms, and 4.02(b) — where the auditor raised it — is not 4.02(a).

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

## Deployment

Already live on GitHub Pages. The workflow runs weekdays at **23:30 UTC**
(after EDGAR's 17:30 ET cutoff), or on demand via
*Actions → Daily filing scan → Run workflow*, which also accepts a `days` input
to reprocess recent dates.

Configured: secret `SEC_USER_AGENT`, variable `SITE_URL`, Pages source
*GitHub Actions*, and Actions workflow permissions set to **write** so the run
can commit each day's events back to `data/`.

> GitHub disables scheduled workflows in repositories with no commit activity
> for 60 days. This one commits on any day it finds events, which keeps it
> alive on its own; a quiet stretch longer than that would need a manual run.

### Hosting it elsewhere (e.g. Hostinger)

The build output in `public/` is 87 files, ~816 KB, and uses only relative
paths, so it works from any web root. To serve it from your own domain, keep
GitHub Actions as the scheduler and replace the three `actions/*-pages` steps
in `daily.yml` with an FTP upload of `public/` to your host's `public_html`.
Running the pipeline on shared hosting is not recommended: SEC blocks abusive
IPs, and a shared address is one you do not control.

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
- **Any passage can mention revenue.** The revenue extractor was lifting MD&A
  performance commentary and the auditor's critical-audit-matter paragraph and
  reporting them as policy changes. It now requires a heading-shaped match,
  outside MD&A and the audit report, whose body carries at least three distinct
  ASC 606 terms. That took the signal from 13 events to 2 over the same days.
- **Form 12b-25 checkboxes render in two orders** (`Yes ☐ No ☒` and
  `☐ Yes ☒ No`) with several glyphs; handling one order silently returned
  "unknown" for half the population.
- **`html_to_text` preserves single newlines**, so form instruction text arrives
  broken across lines and boilerplate filters fail to match it.
- **A dict comprehension filtering falsey values** deleted
  `disagreements_disclosed: False` — the informative common case.

## What the derived pages do and don't claim

**Sequences** shows companies that reached more than one signal, in order. A
recognisable progression runs late filing → auditor change → going concern →
non-reliance, but reaching one step does not imply the next.

**Follow-on rates** are computed from each company's full EDGAR history —
`submissions/CIK*.json` returns up to a thousand filings with 8-K `items`
populated, so one request per company buys a decade of history rather than
walking the daily index back years. They are **conditional rates inside a
population this tracker already flagged**, not population base rates: there is
no matched control group, so they cannot show that one event makes another more
likely, only how often one followed the other here.

**Audit firm movement** counts firms named in flagged Item 4.01 filings. It is
not a market-share measure and the population is small and skewed small-cap.

## Caveats

Not investment advice, and deliberately so: the site reports what was
disclosed, links the source, and declines to interpret. It carries no view on
what any signal means for a share price. Every entry links to its source
filing — verify there before relying on anything. Reports only what companies disclosed; an Item 4.02
means previously issued statements should not be relied upon, which is not by
itself evidence of misconduct.

Data from [SEC EDGAR](https://www.sec.gov/edgar). Public domain.
