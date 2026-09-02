"""HTTP client for SEC EDGAR.

Everything the tracker knows comes through here, so this module owns the three
things that make unattended SEC access survivable: a compliant User-Agent, a
polite rate limit, and an on-disk cache so backfills never refetch.
"""
from __future__ import annotations

import gzip
import datetime as _dt
import hashlib
import logging
import random
import zlib
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from . import config

log = logging.getLogger(__name__)


class SECBlocked(RuntimeError):
    """Raised when the SEC refuses us (403). Treated as expected, not fatal:
    the run stops cleanly and the next one backfills the gap."""


class NotFound(RuntimeError):
    """404 - for daily indexes this normally just means a weekend or holiday."""


class _RateLimiter:
    """Simple monotonic spacing between requests. Not a token bucket on
    purpose: bursts are exactly what gets an IP blocked."""

    def __init__(self, per_second: float) -> None:
        self._min_interval = 1.0 / per_second if per_second > 0 else 0.0
        self._last = 0.0

    def wait(self) -> None:
        if self._min_interval <= 0:
            return
        delta = time.monotonic() - self._last
        if delta < self._min_interval:
            time.sleep(self._min_interval - delta)
        self._last = time.monotonic()


_limiter = _RateLimiter(config.SEC_RATE_LIMIT)


def _cache_path(url: str, byte_range: Optional[str]) -> Path:
    key = hashlib.sha256(f"{url}|{byte_range or ''}".encode()).hexdigest()[:32]
    return config.CACHE_DIR / key[:2] / f"{key}.gz"


# An archived filing never changes: once written to
# /Archives/edgar/data/... it is immutable, so caching it forever is correct
# and is what keeps a backfill from refetching the same documents.
#
# A company's submissions index is NOT immutable - it gains a row every time
# the company files. Caching it forever meant a filing newer than the cached
# copy could not be resolved to its document at all: the copy for CIK 1645155
# stopped at 14 August while the filing being read was from the 24th, so
# current_document() returned None and the 8-K was silently never read.
#
# Only in CI is this invisible, because a fresh runner has no cache. Locally
# it silently degrades every re-extraction.
_MUTABLE_TTL = _dt.timedelta(hours=6)


def _is_stale(url: str, cache_file: Path) -> bool:
    """True when a cached copy of a document that can change is too old."""
    if "/submissions/" not in url and "/api/xbrl/" not in url:
        return False                     # immutable archive document
    try:
        age = _dt.datetime.now() - _dt.datetime.fromtimestamp(cache_file.stat().st_mtime)
    except OSError:
        return True
    return age > _MUTABLE_TTL


def get(
    url: str,
    byte_range: Optional[str] = None,
    use_cache: bool = True,
    accept_404: bool = False,
) -> Optional[str]:
    """Fetch a URL as text.

    `byte_range` takes an HTTP Range value such as "0-4000" - used heavily to
    read filing headers without downloading whole documents.

    Returns None only when `accept_404` is set and the resource is missing.
    """
    cache_file = _cache_path(url, byte_range)
    if use_cache and cache_file.exists() and not _is_stale(url, cache_file):
        try:
            with gzip.open(cache_file, "rt", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            cache_file.unlink(missing_ok=True)  # corrupt entry, refetch

    headers = {"User-Agent": config.require_user_agent()}
    if byte_range:
        # A Range request must not be gzipped. The range would apply to the
        # compressed stream rather than the document, and the slice we get
        # back is an incomplete gzip member that cannot be decompressed.
        headers["Accept-Encoding"] = "identity"
        headers["Range"] = f"bytes={byte_range}"
    else:
        headers["Accept-Encoding"] = "gzip, deflate"

    last_error: Optional[Exception] = None
    for attempt in range(config.SEC_MAX_RETRIES):
        _limiter.wait()
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=config.SEC_TIMEOUT) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    try:
                        raw = gzip.decompress(raw)
                    except (OSError, EOFError):
                        # Truncated stream: recover what decoded cleanly.
                        raw = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw)
                body = raw.decode("utf-8", errors="ignore")

            if use_cache:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                with gzip.open(cache_file, "wt", encoding="utf-8") as fh:
                    fh.write(body)
            return body

        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 404:
                if accept_404:
                    return None
                raise NotFound(url) from exc
            if exc.code == 403:
                # Either a bad UA or a rate/IP block. Both mean: back off hard.
                raise SECBlocked(
                    f"HTTP 403 from {url}. Either SEC_USER_AGENT is not an "
                    "acceptable 'Name email' contact string, or this IP is "
                    "temporarily blocked."
                ) from exc
            if exc.code in (429, 500, 502, 503, 504):
                backoff = (2 ** attempt) + random.uniform(0, 1)
                log.warning("HTTP %s from %s, retry in %.1fs", exc.code, url, backoff)
                time.sleep(backoff)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            backoff = (2 ** attempt) + random.uniform(0, 1)
            log.warning("network error %s on %s, retry in %.1fs", exc, url, backoff)
            time.sleep(backoff)

    raise RuntimeError(f"giving up on {url}: {last_error}")


def get_json(url: str, accept_404: bool = False):
    import json

    body = get(url, accept_404=accept_404)
    if body is None:
        return None
    try:
        return json.loads(body)
    except ValueError as exc:
        raise RuntimeError(f"non-JSON response from {url}") from exc
