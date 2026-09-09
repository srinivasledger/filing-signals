# What to do when it stops

This site scans SEC filings every weekday and publishes itself with nobody at
the controls. It is built to keep doing that, and it costs nothing to run. It
is not, however, immortal: four things can stop it, three of them come from
outside this repository, and each one needs a different response.

Nothing here requires you to write code. Where a fix does, it says so and tells
you what to ask for.

---

## First: how the site tells you

You do not need to watch the Actions tab. The site reports on itself.

**In the bar at the top of every page.** Normally it shows the source, when the
pages were compiled, and how the self-checks went. If the data has stopped
moving, an amber line appears next to them:

> **Data 15 business days behind**

That line is worked out in your browser from today's date, not written when the
page was built — so it keeps counting up even if nothing has run for a month.
It stays hidden at one business day behind, which is the normal gap between
scans. **If you can see it, something has stopped.**

**On the [status page](https://srinivasledger.github.io/filing-signals/status.html).**
Four figures at the top: the newest filing day held, how current that is, when a
scan last succeeded, and the overall result of the self-checks. When the first
two disagree with the third — data from a week ago, but a scan "succeeded" last
night — the scans are running and failing.

**A 30-second check once a month is enough.** Open the site. If the header is
quiet, it is running.

---

## 1. GitHub switches the schedule off

**Most likely of the four.**

### What you will see
An email from GitHub saying scheduled workflows have been disabled. The site
header starts counting up. The Actions tab shows no new runs.

### Why
GitHub turns off scheduled workflows in a public repository after **60 days
with no repository activity**, to avoid running jobs for abandoned projects.
The nightly scan commits data every day; whether GitHub counts commits made by
its own token as "activity" is not something I can promise either way.

### What to do
1. Open **[the Actions tab](https://github.com/srinivasledger/filing-signals/actions)**.
2. Click **Daily filing scan** in the left-hand list.
3. A banner at the top says the workflow was disabled. Click **Enable workflow**.
4. Click **Run workflow** on the right, leave the box blank, confirm.

It will catch up on every day it missed on its own — that is what the backfill
is for. Nothing is lost by having been off.

### To make it less likely
Any commit of your own resets the 60-day clock for certain. Editing this file
and saving it on GitHub counts.

---

## 2. GitHub retires the tooling the workflow uses

**Happened on 8 September 2026. Will happen again, roughly yearly.**

### What you will see
A yellow warning triangle on otherwise-successful runs, saying something like
*"Node.js 20 is deprecated. The following actions target Node.js 20 but are
being forced to run on Node.js 24."* Eventually, a red failure.

### Why
The workflow uses building blocks GitHub publishes (`actions/checkout` and
friends). GitHub retires the runtime those are built on every year or so. The
warning is advance notice — usually months of it — and things keep working
until it turns into an error.

### What to do
This one needs a code change: each new major version has to be checked for
changed inputs before it is adopted. It is a five-minute job, not a rebuild.

**Ask for:** *"the GitHub Actions in the workflows need bumping to current
major versions."* That is the whole task.

Not urgent when the warning first appears. Do not leave it a year.

---

## 3. The SEC changes something, or blocks the runner

**The likeliest thing to end this over a long horizon, and the one no amount of
engineering prevents.**

### What you will see
Runs failing, and the data not advancing. The failure in the run log mentions
the SEC, a 403, or a parsing error.

### Why, and what it means
Two quite different cases:

**(a) Blocked.** The SEC restricts requests from cloud providers, and GitHub's
runners are cloud machines. The pipeline treats a refusal as expected: it stops
cleanly, commits nothing, and the next run picks up where it left off. **These
usually clear on their own within a day.**

**(b) EDGAR changed.** A URL, a file format, or the rules about identifying
yourself. This needs code changes.

### What to do
**Wait one day.** If the next scheduled run succeeds, it was (a) and it has
already fixed itself.

If it fails three runs in a row, it is (b). **Ask for:** *"the SEC scan has
failed three runs running"* and include the link to a failed run.

---

## 4. A dependency releases a breaking version

**Now unlikely — the version ceilings in `requirements.txt` exist for this.**

### What you will see
A run failing at the **Install dependencies** step, or immediately after it.

### Why
The pipeline uses three outside libraries. They are pinned below their next
major version, so a breaking release cannot arrive on its own. This can now
only happen if someone raises a ceiling.

### What to do
**Ask for:** *"revert the requirements.txt change"*. If nobody changed it,
something stranger is going on — send the run log.

---

## 5. A self-check fails and the run goes red

This is not a fault. It is the design.

### What you will see
An email titled "Run failed". The site keeps showing the last good data and
does not update.

### Why
Every run ends by asserting the things the site depends on — that no entry
contradicts its own quoted filing text, that nothing is duplicated, that every
entry links to the filing it reports. **If any of those fail, the run commits
nothing and publishes nothing.** You are looking at the last state that passed
every check, rather than a partial or doubtful one.

### What to do — and this is the important distinction

**Ask: does it fail every single run, or was it once?**

- **Once, then the next run is green.** Nothing to do. Something transient.
- **Every run, without exception.** It is stuck and cannot clear itself. This
  happened on 9 September 2026: two filings the SEC had listed on two different
  days were being written twice, the check stopped the run, nothing was
  committed — so the next run did exactly the same thing and failed identically.
  The data would have sat frozen forever.

**Ask for:** *"the daily scan has failed the same check on every run since
[date]"*, with a link to one of them. Naming the check is what makes it quick.

---

## 6. A run says it discarded its work

### What you will see
A green run carrying a warning: *"another run committed first and this one
could not be rebased onto it; its work is discarded and the next run will redo
it."*

### What to do
**Nothing.** Two runs finished close together and one lost the race. It threw
away its own copy rather than publish pages that do not match the repository.
The next run does the work again.

---

## The settings that have to stay as they are

All correct as of 9 September 2026. If Pages stops updating or the scan cannot
commit, check these first — in **Settings** on the repository:

| Where | Setting | Must be |
|---|---|---|
| General | Visibility | **Public** — private means paid minutes and paid Pages |
| Actions → General | Actions permissions | **Allow all actions** |
| Pages | Source | **GitHub Actions** |
| Secrets and variables → Actions | Secret `SEC_USER_AGENT` | A real name and email. The SEC refuses unidentified automation. |
| Secrets and variables → Actions | Variables `SITE_URL`, `REPO_URL`, `HISTORY_FROM`, `HISTORY_CHUNK` | present |

Do not archive the repository. An archived repository runs nothing.

---

## Running it yourself, any time

The status page carries two links, and both need you to be signed in to GitHub
as the owner. Nobody else can use them: GitHub only shows the run button to
accounts with write access, and a visitor's page does not contain it at all.

- **Scan for new filings** — catches up on everything not yet scanned, re-runs
  every check, republishes. This is also the repair.
- **Rebuild the pages** — re-renders from data already held. Never contacts the
  SEC, so it is quick and cannot be blocked.

Pressing either repeatedly is safe. At most one run executes and one waits;
anything beyond that is cancelled automatically.
