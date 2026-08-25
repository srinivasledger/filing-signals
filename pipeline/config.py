"""Central configuration. Values are read from the environment so the
GitHub Actions workflow can override them without code changes."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EVENTS_DIR = DATA / "events"
STATE_DIR = DATA / "state"
CACHE_DIR = ROOT / ".cache"
PUBLIC = ROOT / "public"
TEMPLATES = ROOT / "site" / "templates"
STATIC = ROOT / "site" / "static"

# --- SEC access -------------------------------------------------------------
# The SEC rejects requests whose User-Agent does not identify a real contact.
# A descriptive-but-unconventional UA returns HTTP 403 "Undeclared Automated
# Tool". The accepted shape is "Some Name some@email.tld".
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "").strip()

# The documented ceiling is 10 requests/second per IP. We deliberately run at
# half that: the tracker is never in a hurry, and a block costs us a whole day.
SEC_RATE_LIMIT = float(os.getenv("SEC_RATE_LIMIT", "5"))
SEC_TIMEOUT = int(os.getenv("SEC_TIMEOUT", "45"))
SEC_MAX_RETRIES = int(os.getenv("SEC_MAX_RETRIES", "4"))

ARCHIVES = "https://www.sec.gov/Archives"
DAILY_INDEX = "https://www.sec.gov/Archives/edgar/daily-index"
SUBMISSIONS = "https://data.sec.gov/submissions"

# --- Pipeline behaviour -----------------------------------------------------
# How many business days a single run will backfill. Bounds the blast radius of
# a long outage so one run cannot try to fetch a year of filings.
MAX_BACKFILL_DAYS = int(os.getenv("MAX_BACKFILL_DAYS", "10"))

# Earliest date the tracker will ever reach back to on a cold start.
COLD_START_DAYS = int(os.getenv("COLD_START_DAYS", "5"))

# --- Site -------------------------------------------------------------------
SITE_TITLE = os.getenv("SITE_TITLE", "Filing Signals")
SITE_TAGLINE = os.getenv(
    "SITE_TAGLINE",
    "Daily tracking of restatements, SEC comment letters, material weaknesses, "
    "auditor and CFO changes, and going-concern language in SEC filings.",
)
SITE_URL = os.getenv("SITE_URL", "").rstrip("/")

# Where corrections go. The methodology page invited people to "open an issue"
# without linking anywhere, which is worse than offering nothing: it implies a
# route that does not exist.
REPO_URL = os.getenv("REPO_URL", "").rstrip("/")

# Serving from a custom domain. GitHub Pages stores the domain in repository
# settings, but an Actions deploy replaces the whole site directory on every
# run - and a deployed artifact without a CNAME file can clear that setting,
# silently unbinding the domain. So the build writes the file every time.
CUSTOM_DOMAIN = os.getenv("CUSTOM_DOMAIN", "").strip().lstrip("https://").lstrip("http://").rstrip("/")

# --- Analysis layer ---------------------------------------------------------
# Absent key => NullAnalyzer => the pipeline runs end to end at zero cost.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
TRIAGE_MODEL = os.getenv("TRIAGE_MODEL", "claude-haiku-4-5")
WRITEUP_MODEL = os.getenv("WRITEUP_MODEL", "claude-opus-5")


def require_user_agent() -> str:
    """Fail loudly and early rather than collecting a day of 403s."""
    if not SEC_USER_AGENT:
        raise SystemExit(
            "SEC_USER_AGENT is not set.\n"
            "The SEC rejects unidentified automated requests with HTTP 403.\n"
            'Set it to a real contact, e.g. SEC_USER_AGENT="Jane Doe jane@example.com"'
        )
    return SEC_USER_AGENT
