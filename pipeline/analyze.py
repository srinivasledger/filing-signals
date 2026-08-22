"""Optional Claude enrichment.

The tracker is designed to be complete without this module. Deterministic
evidence, quotes and citations are produced upstream; everything here is
commentary layered on top. Two consequences, both deliberate:

  * With no ANTHROPIC_API_KEY the pipeline selects NullAnalyzer and runs end to
    end at zero cost.
  * If the API errors, rate-limits or returns junk, enrichment is skipped for
    that event and the run continues. A publishing pipeline must not fail
    because a commentary layer did.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional

from . import config
from .models import POLICY_CHANGE, REVENUE_RECOGNITION, Event

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You explain SEC filing disclosures to a financially literate reader.

Rules you must follow exactly:
- Describe only what the filing SAYS. Never infer fraud, misconduct, or motive.
- Never predict share price or give investment advice.
- Prefer the filing's own terms. If the filing says "substantial doubt", say that.
- A restatement means the company said earlier statements should not be relied
  upon. It does not by itself mean anything was falsified.
- If the supplied evidence is too thin to explain confidently, say so in the
  summary rather than speculating.
- Write plainly. No hype, no adjectives like "shocking" or "alarming"."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "2-3 plain-English sentences on what the filing disclosed.",
        },
        "why_it_matters": {
            "type": "string",
            "description": "One sentence on why a reader should care.",
        },
        "materiality": {"type": "string", "enum": ["high", "medium", "low"]},
        "is_substantive": {
            "type": "boolean",
            "description": "False if this is a cosmetic or purely formatting change.",
        },
    },
    "required": ["summary", "why_it_matters", "materiality", "is_substantive"],
    "additionalProperties": False,
}


class NullAnalyzer:
    """The zero-cost default."""

    enabled = False

    def enrich(self, events: List[Event]) -> List[Event]:
        return events


class ClaudeAnalyzer:
    """Adds plain-English commentary, and screens out cosmetic beta signals."""

    enabled = True

    def __init__(self) -> None:
        import anthropic  # imported lazily so the package stays optional

        self._client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    # -- prompt ---------------------------------------------------------------
    @staticmethod
    def _user_prompt(event: Event) -> str:
        parts = [
            f"Company: {event.company}",
            f"Form: {event.form} filed {event.filed}",
            f"Signal detected: {event.label}",
            f"How it was detected: {event.evidence.get('source', 'n/a')}",
        ]
        if event.evidence.get("item_title"):
            parts.append(f"SEC item: {event.evidence['item_title']}")
        if event.evidence.get("prior_state_label"):
            parts.append(f"Previous position: {event.evidence['prior_state_label']}")
            parts.append(f"Current position: {event.evidence['current_state_label']}")
        if event.evidence.get("new_language"):
            parts.append("Language present now and absent from the prior filing:")
            for line in event.evidence["new_language"]:
                parts.append(f"  - {line}")
        if event.quote:
            parts.append(f"\nVerbatim text from the filing:\n\"\"\"\n{event.quote}\n\"\"\"")
        parts.append(
            "\nExplain this disclosure for a reader who has not seen the filing."
        )
        return "\n".join(parts)

    # -- API ------------------------------------------------------------------
    def _call(self, model: str, prompt: str) -> Optional[Dict]:
        try:
            resp = self._client.messages.create(
                model=model,
                max_tokens=1200,
                system=[{
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            )
            text = "".join(
                b.text for b in resp.content if getattr(b, "type", "") == "text"
            )
            return json.loads(text)
        except Exception as exc:                 # noqa: BLE001
            log.warning("structured call failed (%s); retrying as plain text", exc)

        # Fallback: some deployments/models may not accept output_config.
        try:
            resp = self._client.messages.create(
                model=model,
                max_tokens=1200,
                system=SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": prompt + "\n\nReply with JSON only, matching: "
                               + json.dumps(_SCHEMA["properties"]),
                }],
            )
            text = "".join(
                b.text for b in resp.content if getattr(b, "type", "") == "text"
            )
            m = re.search(r"\{.*\}", text, re.S)
            return json.loads(m.group(0)) if m else None
        except Exception as exc:                 # noqa: BLE001
            log.warning("analysis unavailable for this event: %s", exc)
            return None

    # -- public ---------------------------------------------------------------
    def enrich(self, events: List[Event]) -> List[Event]:
        kept: List[Event] = []
        for event in events:
            # Beta text-diff signals get screened by the cheap model first, so
            # cosmetic rewrites never reach the expensive one or the site.
            model = (
                config.TRIAGE_MODEL
                if event.signal_type in (POLICY_CHANGE, REVENUE_RECOGNITION)
                else config.WRITEUP_MODEL
            )
            result = self._call(model, self._user_prompt(event))

            if result is None:
                kept.append(event)               # publish without commentary
                continue

            if event.beta and result.get("is_substantive") is False:
                log.info("dropping cosmetic %s for %s", event.signal_type, event.company)
                continue

            event.ai = {
                "summary": result.get("summary", ""),
                "why_it_matters": result.get("why_it_matters", ""),
                "materiality": result.get("materiality", ""),
                "model": model,
            }
            kept.append(event)
        return kept


def get_analyzer():
    if not config.ANTHROPIC_API_KEY:
        log.info("no ANTHROPIC_API_KEY: running deterministic-only (zero cost)")
        return NullAnalyzer()
    try:
        analyzer = ClaudeAnalyzer()
        log.info("Claude analysis enabled (%s / %s)",
                 config.TRIAGE_MODEL, config.WRITEUP_MODEL)
        return analyzer
    except ImportError:
        log.warning("anthropic package not installed; skipping analysis")
        return NullAnalyzer()
    except Exception as exc:                     # noqa: BLE001
        log.warning("could not initialise Claude analyzer (%s); continuing without", exc)
        return NullAnalyzer()
