# Filing Signals

**Live: <https://srinivasledger.github.io/filing-signals>** ·
[RSS](https://srinivasledger.github.io/filing-signals/feed.xml) ·
[JSON](https://srinivasledger.github.io/filing-signals/events.json)

A self-updating public tracker that reads new SEC filings every weekday and
publishes six things that are otherwise hard to see. It runs on GitHub Actions
and GitHub Pages: no server, no database, and **no API key required** — the
default configuration produces the full deterministic feed at zero cost.

| Signal | Source | Sub-classification |
|---|---|---|
| **Restatements** | 8-K Item 4.02 | **(a)** management concluded vs **(b)** the auditor told them |
| **Auditor changes** | 8-K Item 4.01 | resigned vs dismissed; disagreements disclosed; predecessor → successor firm, and whether that is a tier downgrade |
| **Late filings** | Form 12b-25 (NT 10-K / NT 10-Q) | graded on the stated reason; routine deadline-week notices separated from substantive ones |
| **Going concern** | ASC 205-40 note vs the previous filing | ladder: no conclusion → doubt alleviated → substantial doubt |
| **Accounting policy** | Newly *adopted* accounting standards (ASU) | adoption vs merely-issued |
| **Revenue recognition** | ASC 606 policy note vs the previous filing | beta |

The item code says an event happened; the sub-classification says which kind,
and the kind is usually the signal. An auditor resigning is not a company
rotating firms, and 4.02(b) — where the auditor raised it — is not 4.02(a).

## What makes it different

Publishing "this filing mentions going concern" would be worthless: thousands
do, every quarter, unchanged for years. This tracker publishes **state
transitions** — the moment a company's disclosure actually changes — which
requires fetching and comparing the previous filing.

A worked example: Cyclerion Therapeutics' latest 10-Q contains 17 matches for
"going concern"; its prior 10-Q contains 16. A keyword tracker fires on this.
Nothing changed, so this one correctly reports nothing.

Every entry carries the filing text it was derived from and a link to the
original, so the claim is always checkable against the source.

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
./.venv/bin/python -m pipeline.run --no-render         # collect without building
./.venv/bin/python -m pipeline.render                  # rebuild site only
./.venv/bin/python -m pytest tests/ -q                 # 51 tests, no network
```

## How it runs itself

Each run processes every business day from the last recorded date through the
last complete filing day — not just "today". A missed cron, an SEC block, or a
runner outage is repaired by the next run rather than leaving a permanent gap.
Events are keyed by `(accession, signal_type)`, so re-running a date never
duplicates anything.

```
daily index ──► filing headers ──► universe filter ──┬─► 8-K item codes ──────┐
   (1 req)      (~1 KB each)      (form + SIC)       │   + sub-classification │
                                                     ├─► Form 12b-25 parse ───┤
                                                     └─► prior-filing compare ┤
                                                         (going concern, ASU, │
                                                          revenue policy)     │
                                                                              ▼
   site ◄── render ◄── self-checks ◄── follow-on rates ◄── size index ◄── optional AI
```

Eleven **self-checks** run after every pass and publish to the
[status page](https://srinivasledger.github.io/filing-signals/status.html)
rather than to a log — that every entry cites a filing, that comparisons name
what they were compared against, that re-running never duplicates, that quotes
begin at a sentence. They have caught real regressions.

## Deployment

Served directly from **GitHub Pages** at `srinivasledger.github.io`, built and
deployed by the workflow itself — there is no external host and no custom
domain in front of it.

The workflow runs weekdays at **23:30 UTC** (after EDGAR's 17:30 ET cutoff), or
on demand via *Actions → Daily filing scan → Run workflow*, which accepts a
`days` input to reprocess recent dates.

Configured: secret `SEC_USER_AGENT`; variables `SITE_URL` and `REPO_URL`; Pages
source *GitHub Actions*; workflow permissions **write**, so each run commits
that day's events back to `data/`.

> GitHub disables scheduled workflows in repositories with no commit activity
> for 60 days. Every run writes its state file, which keeps the schedule alive
> on its own.

> **Actions quotas.** Scheduled workflows on public repositories are free and
> unmetered, but a new account firing many manual runs in a short window can
> trip GitHub's abuse detection — which happened during development and cost
> the site its deployments. After the first build, let the schedule do the work.

### Serving from a custom domain (optional, not in use)

The site runs on the `github.io` address by choice. The capability below exists
if that ever changes.

Set the `CUSTOM_DOMAIN` variable and the build writes `public/CNAME` on **every**
run. That repetition is the point: Pages keeps the domain in repository
settings, but an Actions deploy replaces the whole site directory, and an
artifact missing that file can silently clear the binding. Committing it once
does not survive, because `public/` is regenerated from scratch.

Note that renaming a GitHub account does **not** redirect Pages — the old URL
returns 404 outright — while setting a custom domain **does** 301-redirect the
`github.io` address.

### Hosting it elsewhere (e.g. Hostinger)

The build output in `public/` is ~250 files, ~3 MB, and uses only relative
paths, so it works from any web root. Keep GitHub Actions as the scheduler and
replace the three `actions/*-pages` steps in `daily.yml` with an FTP upload of
`public/`. Running the pipeline itself on shared hosting is not recommended:
the SEC blocks abusive IPs, and a shared address is one you do not control.

### Optional: plain-English analysis

Add an `ANTHROPIC_API_KEY` secret and the analysis layer activates with **no
code change** — the workflow installs the SDK only when the key is present. It
adds a summary to each event and screens beta text-diff findings for cosmetic
rewrites. Detection stays entirely deterministic either way: no model decides
whether something is a signal.

| Setting | Default | Purpose |
|---|---|---|
| `SEC_USER_AGENT` | *(required)* | Contact string sent to SEC — 403 without it |
| `SITE_URL` | *(unset)* | Absolute base for RSS links |
| `REPO_URL` | *(unset)* | Where the corrections link points |
| `CUSTOM_DOMAIN` | *(unset)* | Written to `public/CNAME` on every build |
| `ANTHROPIC_API_KEY` | *(unset)* | Enables analysis; unset = free |
| `SEC_RATE_LIMIT` | `5` | Requests/sec (SEC's ceiling is 10) |
| `MAX_BACKFILL_DAYS` | `10` | Cap on catch-up work per run |
| `COLD_START_DAYS` | `5` | Reach-back on a first run |
| `MAX_PERIODIC_PER_DAY` | `120` | Bound on the expensive comparison path |
| `POLICY_SIMILARITY_THRESHOLD` | `0.60` | Revenue-note rewrite sensitivity |

## Design notes

Everything below was found by checking output against real filings, not by
reasoning about the code. Each one changed the implementation.

### Reading EDGAR

- **Range requests do not work.** `sec.gov` answers a ranged Archives request
  with `200` and the full body. Reading `<accession>.hdr.sgml` instead is ~1 KB
  versus ~195 KB and yields *numeric* item codes rather than prose titles.
- **Inline tags split words.** Filings wrap word fragments in `<span>`/XBRL
  tags; substituting a space produced `Item 1A. Ri sk Factors` and silently
  broke every text match. Inline tags must collapse to nothing.
- **XBRL `frames` is numeric-only** — 404 for `TextBlock` tags, so policy text
  has to come from documents. It *is* the cheap route for numeric facts:
  ~5,900 filers' public float in eleven requests.
- **The daily index is not whitespace-separated.** Form types contain spaces
  (`DEF 14A`, `NT 10-K/A`), and the column header spans two lines and does not
  align with the data. Parse right-anchored.

### Reading the disclosures

- **Negation reverses the meaning and nothing else does.** ChronoScale Holdings
  wrote "substantial doubt … **is not raised**" and was published as disclosing
  the opposite. Worse, the classifier *defaulted* to substantial doubt when it
  could not read the wording — turning "cannot tell" into an affirmative claim
  about a named company. Negations are now masked before anything positive is
  matched, because position cannot resolve it: in "do not raise substantial
  doubt" the negation begins **before** the phrase it governs.
- **Removing that default destroyed seven of eight true positives**, which had
  been relying on it. Safe defaults and a narrow detector are not independent
  choices.
- **ASC 205-40 notes recite both outcomes** as methodology before concluding,
  so classification keys on the conclusion sentence, not proximity.
- **"Short line" is not a heading test.** A bullet fragment, "ability to
  continue as a going concern;", was read as a note heading while the real one
  sat 286,000 characters further down.
- **Reverse mergers fake transitions.** BOXABL (CIK 1906364, formerly FG Merger
  II Corp.) appeared to go from no disclosure to substantial doubt; the prior
  filing was a SPAC shell. Registrant changes are detected via `formerNames`
  and reported as new disclosures, not changes.
- **Listing an issued standard is not adopting it.** Diffing ASU mentions
  flagged 8 of 18 filings in one day; requiring adoption language fixed it.

### Revenue recognition, the hardest signal

- **Any passage can mention revenue.** The extractor was lifting MD&A
  performance commentary and the auditor's critical-audit-matter paragraph. It
  now requires a heading-shaped match, outside MD&A and the audit report, whose
  body carries at least three distinct ASC 606 terms.
- **The ASC 606 five-step model is recited verbatim by thousands of filers** and
  is exactly what a text diff surfaces as "new language". It is stopped before
  similarity is computed. Stripping the matched *substring* was itself a defect:
  it left mangled remnants that read as novel — one filer was flagged on
  "Revenue is recognized to in exchange for transferring goods or services",
  the core principle with its middle removed by that very function. Whole
  boilerplate sentences are dropped instead.

### Grading and ranking

- **Late filings follow the filing calendar, not distress.** 14 August 2026 is
  the 10-Q due date for the June quarter for non-accelerated filers, and 101 of
  that day's notices landed on it. Statutory due dates are computed from the
  report period and filer size tier; a notice within three days of its due date
  with no substantive cause is marked routine and hidden by default.
- **Checkboxes do not discriminate.** "Anticipates a significant change" was
  `True` for a boilerplate notice and for one disclosing an identified
  revenue-recognition error alike. Severity now keys on the stated reason;
  the checkboxes contribute but do not decide. `high` is ~6% of late filings.
- **Filers make units errors in their own XBRL, undetectable by magnitude.**
  NVIDIA's reported $4.0T public float is correct; Universal Display's $6.8T is
  its real $6.8B tagged a thousand times too large. Each value is screened
  against the filer's own total assets *and* its shares outstanding, which must
  imply a believable share price — a bank's mis-tagged $961B float is only 53×
  its assets but implies $15,929 per share. Both cross-checks come from frame
  unions, so verifying ~5,900 filers costs nine extra requests.

### Presentation defects worth remembering

- **`html_to_text` preserves single newlines**, so form instruction text arrives
  broken across lines and boilerplate filters fail to match it.
- **A dict comprehension filtering falsey values** deleted
  `disagreements_disclosed: False` — the informative common case.
- **CSS shorthand beats element selectors.** `<main class="wrap">` with
  `.wrap { padding: 0 … }` silently reset `padding-bottom` to zero regardless of
  the `main { … }` rule.
- **`justify-content: flex-end` pushes overflow off the left edge**, where
  scrolling cannot reach it. Four of seven mobile nav links were unreachable.

## What the derived pages do and don't claim

**Sequences** shows companies that reached more than one signal, in order. A
recognisable progression runs late filing → auditor change → going concern →
non-reliance, but reaching one step does not imply the next.

**Follow-on rates** come from each company's full EDGAR history —
`submissions/CIK*.json` returns up to a thousand filings with 8-K `items`
populated, so one request per company buys a decade rather than walking the
daily index back years. They are **conditional rates inside a population this
tracker already flagged**, not population base rates: with no matched control
group they cannot show that one event makes another more likely, only how often
one followed the other here.

**Audit firm movement** counts firms named in flagged Item 4.01 filings. Not a
market-share measure; the population is small and skewed small-cap.

**Company size** is public float from the 10-K cover — the SEC's own size test,
not a revenue ranking. "Fortune 500" includes private companies and cannot be
derived from EDGAR.

## Known limitations

- **History is shallow.** Eight filing days is not enough to give the flag rate
  a reference range, or to show a sequence completing. A one-to-three-year
  backfill is the next substantial piece of work.
- **Revenue recognition is beta** and remains the signal most likely to produce
  a wrong entry, being the only one resting on text similarity.
- **Section extraction depends on filing structure.** Unusual formatting causes
  a note to be missed; the pipeline reports nothing rather than guessing.
- **No prior comparable filing means no comparison signal.** A change cannot be
  shown without a baseline.

## Caveats

Not investment advice, and deliberately so: the site reports what was disclosed,
links the source, and declines to interpret. It carries no view on what any
signal means for a share price. An Item 4.02 means previously issued statements
should not be relied upon, which is not by itself evidence of misconduct.

Entries are generated automatically and are **not reviewed by a person before
publication**, so mistakes reach the page. Corrections are welcome from anyone,
including the filer, via the repository's issue tracker.

## Rights

Filings on EDGAR are the registrants' own documents, not works of the US
Government, so they are **not** public domain — an earlier version of this file
said otherwise and was wrong. What holds is narrower and enough: the facts drawn
from filings (dates, item codes, disclosure states) are not copyrightable, and
the quoted passages are short excerpts reproduced with a link to the source.

The site carries no copyright assertion. Copyright in the code and design is
automatic and does not need declaring, and an ownership claim sitting under a
page of filing extracts reads as claiming more than it does. The footer states
the useful half instead: where the quotes come from, and that the facts in them
are not owned by anyone.

Data from [SEC EDGAR](https://www.sec.gov/edgar).
