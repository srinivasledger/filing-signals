# Filing Signals

**Live: <https://srinivasledger.github.io/filing-signals>** ·
[RSS](https://srinivasledger.github.io/filing-signals/feed.xml) ·
[JSON](https://srinivasledger.github.io/filing-signals/events.json)

A self-updating public tracker that reads new SEC filings every weekday and
publishes nine things that are otherwise hard to see. It runs on GitHub Actions
and GitHub Pages: no server, no database, and **no API key required** — the
default configuration produces the full deterministic feed at zero cost.

| Signal | Source | Sub-classification |
|---|---|---|
| **Restatements** | 8-K Item 4.02 | **(a)** management concluded vs **(b)** the auditor told them |
| **SEC comment letters** | UPLOAD (staff) and CORRESP (company) | classified by accounting topic; only periodic-report reviews, not registration statements |
| **Material weakness** | Item 9A conclusion vs the previous filing | newly reported vs remediated |
| **Finance chief departures** | 8-K Item 5.02 | CFO/CAO/controller only; successor named, interim, or none |
| **Auditor changes** | 8-K Item 4.01 | resigned vs dismissed; disagreements disclosed; predecessor → successor firm, and whether that is a tier downgrade |
| **Late filings** | Form 12b-25 (NT 10-K / NT 10-Q) | graded on the stated reason; routine deadline-week notices separated from substantive ones |
| **Going concern** | ASC 205-40 note vs the previous filing | ladder: no conclusion → doubt alleviated → substantial doubt |
| **Accounting standard newly cited** | An ASU present in this filing and not the previous one | adoption wording vs merely-issued; stated adoption year published, and a first citation of a standard adopted years earlier is marked as such |
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
./.venv/bin/python -m pytest tests/ -q                 # 84 tests, no network
```

## How it runs itself

Each run processes every business day from the last recorded date through the
last complete filing day — not just "today". A missed cron, an SEC block, or a
runner outage is repaired by the next run rather than leaving a permanent gap.
Events are keyed by `(accession, signal_type)`, so re-running a date never
duplicates anything.

```
daily index ──► filing headers ──► universe filter ──┬─► 8-K item codes ──────┐
   (1 req)      (~1 KB each)      (form + SIC)       │   4.02 / 4.01 / 5.02   │
                                                     ├─► Form 12b-25 parse ───┤
                                                     ├─► UPLOAD / CORRESP ────┤
                                                     │   (topic + "Re:" line) │
                                                     └─► prior-filing compare ┤
                                                         (going concern,      │
                                                          Item 9A control,    │
                                                          ASU, revenue policy)│
                                                                              ▼
   site ◄── render ◄── self-checks ◄── follow-on rates ◄── size index ◄── optional AI
```

Fourteen **self-checks** run after every pass and publish to the
[status page](https://srinivasledger.github.io/filing-signals/status.html)
rather than to a log — that every entry cites a filing, that comparisons name
what they were compared against, that re-running never duplicates, that a
quote never contradicts the state it is filed under, and that no page has
grown heavy enough to feel slow. They have caught real regressions.

## Deployment

Served directly from **GitHub Pages** at `srinivasledger.github.io`, built and
deployed by the workflow itself — there is no external host and no custom
domain in front of it.

Two workflows deploy. **Daily filing scan** runs at **03:30 UTC Tuesday to
Saturday** — 23:30 ET on weekday evenings — or on demand via *Actions → Daily
filing scan → Run workflow*, which accepts a `days` input to reprocess recent
dates. The time is set by when EDGAR publishes the day's index, about 22:00 ET,
**not** by the 17:30 ET filing cutoff: an earlier schedule asked for an index
that did not exist yet and left every day to be backfilled by the next run.
**Publish site** runs on any push touching `site/` or `pipeline/`, rebuilding
from the data already committed.

The second exists because the scan used to be the only thing that deployed, so a
template fix sat unpublished until the next night — a page could be committed,
tested and still 404 on the live site. It never contacts SEC, so it adds no load
there and a block cannot affect it. Both share one concurrency group: two Pages
deployments must never run at once, and a push landing mid-scan queues behind
it rather than racing it.

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

The build output in `public/` is ~320 files, ~4 MB, and uses only relative
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
| `HISTORY_FROM` | *(unset)* | Fill history back to this date, a chunk per run |
| `HISTORY_CHUNK` | `12` | Older days added per run while filling |
| `HISTORY_BUDGET_SECONDS` | `10800` | Fill stops after this; the day's own filings never do |
| `COLD_START_DAYS` | `5` | Reach-back on a first run |
| `MAX_PERIODIC_PER_DAY` | `120` | Bound on the expensive comparison path |
| `POLICY_SIMILARITY_THRESHOLD` | `0.60` | Revenue-note rewrite sensitivity |

`pypdf` is required: SEC staff comment letters are PDFs, unlike every other
filing the pipeline reads.

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
- **EDGAR answers a missing daily index with 403, not 404** — the same status
  it uses for a blocked client. Weekends, holidays and days not yet published
  all come back 403, so a holiday skip that waits for a 404 is dead code. The
  two cases are separated by probing a URL known to exist: if that answers, the
  index is simply absent; if it does not, the client really is blocked.
- **A refusal must not be catchable as an ordinary failure.** `SECBlocked`
  subclasses `RuntimeError`, so five `except Exception` handlers in the fetch
  path swallowed it — including the two every comparison signal is built on.
  Nothing crashed, which is why it lasted: a block simply made every filing
  unreadable, so the day produced no events and was recorded as processed and
  quiet. The history fill made it serious, because it writes days unattended
  and never revisits them. An error that means *stop* has to be raised past
  handlers written for errors that mean *skip this one*.
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

### The newer signals

- **Most comment letters are not about accounting.** Of thirty staff letters
  sampled, seventeen reviewed registration statements and four reviewed a
  periodic report. Only the last group is the signal, so the "Re:" line is
  parsed and everything else discarded. Roughly nine letters a week survive,
  covering about three companies — staff letter and company reply are separate
  filings and both are published.
- **UPLOAD documents are PDFs**, unlike everything else on EDGAR that this
  pipeline reads. CORRESP is HTML.
- **"lease" matched every letter in the first sample**, because "please"
  contains it. Every topic pattern is word-bounded.
- **Item 5.02 covers every officer and director change** and is among the
  commonest 8-K items, so the code alone is noise. The role and the departure
  must appear in the same sentence: a 400-character window was wide enough to
  join "a director resigned" to the CFO named in the signature block.
- **"Item 9A" appears on the cover page** beside the Section 404(b) checkbox
  and again in the contents, so the first match scoped a 312,000-character 10-K
  to the wrong 30,000. Use the first occurrence that actually reaches a
  conclusion.
- **A walk-back loop counted the report it stopped on.** Measuring how long a
  material weakness had been reported means walking back until control was last
  reported *effective* — and that final clean report was being counted among the
  reports affected, overstating every count by one. Aviat Networks read three
  annual reports; it is two. The days figure was right and the count beside it
  was not, which is the harder kind to notice.
- **Lowercasing a label to fit a sentence destroys acronyms.** Topic labels are
  capitalised for display and dropped mid-headline, so `.lower()` published
  "non-gaap measures" on sixteen letters and would have made MD&A "md&a". Only
  the leading capital may drop, and not even that when the first word is itself
  an acronym.

### Negation, three times over

The same defect appeared in three unrelated places, and is worth stating as a
rule: **the phrase that triggers detection is usually present in full, and only
the negation governing it distinguishes the two readings.**

- "substantial doubt … **is not raised**" — published as its opposite.
- "**no** disagreements with the Company" — the standard Item 5.02 sentence,
  which labelled routine departures at Boston Beer, Synaptics and Marqeta as
  departures amid disagreement.
- "a material weakness **is a deficiency**…" — the definition every filer
  recites, which is not a disclosure of one.

Two lessons. Negations must be masked *before* anything positive is matched,
because position cannot resolve an overlap. And the negator usually sits a
clause away from the noun — "is **not** related to any **disagreement**" —
so adjacency is not enough.

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
- **Passing tests are not a deployment.** The Pages deploy lived inside the
  daily scan, which only runs on a schedule, so a push ran the test suite, went
  green, and published nothing. A new page was committed, tested and still 404'd
  live. Whatever builds the site has to be reachable from the event that changes
  it.

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

**Comment letters** are grouped into reviews and by accounting topic, neither
of which EDGAR does — it shows letters one filing at a time, so a single review
appeared as eight near-identical entries. Two caveats are on the page itself
because both change how it reads: a letter is not a finding (the SEC must review
every issuer at least every three years, so getting one is routine), and the
letters are historic — the median lag between a letter being written and
appearing on EDGAR is computed from the data and shown on both the letters and
methodology pages, rather than written into prose here where it goes stale.
The topic counts describe what the staff asked this small set of companies, not
what the SEC focuses on generally.

**Material weakness duration** — where a weakness clears, how long it had been
reported. It is derived by walking back through the company's own annual reports
until control was last reported effective, so it is a count of *reported*
duration, not of how long the weakness existed. Where that history runs out or
cannot be read, no number is published.

**Per-signal pages** hold the complete record for one signal, grouped by year.
The overview previews the most recent twelve of each. Nothing is pruned; the
split exists so that no single page grows without bound, and the page-weight
check reports when the largest one is getting close.

**Audit firm movement** counts firms named in flagged Item 4.01 filings. Not a
market-share measure; the population is small and skewed small-cap.

**Company size** is public float from the 10-K cover — the SEC's own size test,
not a revenue ranking. "Fortune 500" includes private companies and cannot be
derived from EDGAR.

## Known limitations

- **History is being filled in.** `HISTORY_FROM` is set to 2026-01-01, and
  each nightly run adds twelve older days until it reaches that date, then
  stops. Until it finishes, counts describe a partial period. This runs
  unattended rather than as one long job because it cannot exceed a job time
  limit that way, and a blocked or failed night simply resumes the next night.
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
said otherwise and was wrong. The one real exception is the staff comment letter
(UPLOAD), which *is* written by SEC employees and so is a US Government work
under 17 U.S.C. §105; the company's reply (CORRESP) is not. What holds is narrower and enough: the facts drawn
from filings (dates, item codes, disclosure states) are not copyrightable, and
the quoted passages are short excerpts reproduced with a link to the source.

The site carries no copyright assertion. Copyright in the code and design is
automatic and does not need declaring, and an ownership claim sitting under a
page of filing extracts reads as claiming more than it does. The footer states
the useful half instead: where the quotes come from, and that the facts in them
are not owned by anyone.

## Licence

Code: MIT, see [LICENSE](LICENSE). The filings it reads are the registrants'
own documents and are not covered by it — except staff comment letters
(UPLOAD), which are US Government works under 17 U.S.C. §105.

## On the word "signal"

It means an observable change in what a company disclosed, not a prediction.
The site reports that a disclosure changed, quotes the filing, and links the
source. It does not forecast outcomes, and the association rates on the
sequences page are descriptive, not causal.

Data from [SEC EDGAR](https://www.sec.gov/edgar).
