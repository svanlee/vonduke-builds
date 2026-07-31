"""
safety/core.py — Supervisor, Ruling, Verdict.

Domain-agnostic. No Minecraft constants here.
"""

import json
import time
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional

MESH_LLM_URL     = "http://127.0.0.1:9337/v1/chat/completions"
MESH_LLM_TIMEOUT = 2.0
MESH_LLM_MODEL   = "mesh-llm"


class Ruling(str, Enum):
    ALLOW    = "allow"
    DENY     = "deny"
    ESCALATE = "escalate"


@dataclass
class Verdict:
    ruling:     Ruling
    reason:     str
    tier:       int
    proposed:   str
    substitute: Optional[str] = None
    latency_ms: float         = 0.0
    meta:       dict          = field(default_factory=dict)

    def to_row(self) -> dict:
        return {
            "ts":         time.time(),
            "ruling":     self.ruling.value,
            "reason":     self.reason,
            "tier":       self.tier,
            "proposed":   self.proposed,
            "substitute": self.substitute,
            "latency_ms": round(self.latency_ms, 2),
            **self.meta,
        }


class Supervisor:
    """
    Generic supervisory veto layer for any FSM-driven agent.

    Usage
    -----
    sup = Supervisor(invariants=MY_INVARIANTS, enable_tier2=False)
    verdict = sup.review(proposed_state, current_state, belief)
    if verdict.ruling is not Ruling.ALLOW:
        use_substitute(verdict.substitute)

    Parameters
    ----------
    invariants   : list of callables (proposed, current, belief) → Verdict | None
    fast_path    : frozenset of state names that skip Tier 2
    escalate_cb  : optional human-in-the-loop callback; receives Verdict,
                   returns True to override (allow anyway)
    enable_tier2 : if True, non-fast-path transitions also go to mesh-llm
    """

    def __init__(self,
                 invariants:    Optional[List] = None,
                 fast_path:     Optional[frozenset] = None,
                 escalate_cb:   Optional[Callable[["Verdict"], bool]] = None,
                 enable_tier2:  bool = False):
        from safety.invariants import INVARIANTS, FAST_PATH
        self._invariants  = invariants if invariants is not None else INVARIANTS
        self._fast_path   = fast_path  if fast_path  is not None else FAST_PATH
        self.escalate_cb  = escalate_cb
        self.enable_tier2 = enable_tier2
        self.stats        = {"allow": 0, "deny": 0, "escalate": 0,
                             "tier2_calls": 0}

    def review(self, proposed: str, current: str, belief) -> Verdict:
        t0 = time.perf_counter()

        for inv in self._invariants:
            v = inv(proposed, current, belief)
            if v is not None:
                v.latency_ms = (time.perf_counter() - t0) * 1000
                return self._finish(v)

        if self.enable_tier2 and proposed not in self._fast_path:
            v = self._tier2(proposed, current, belief)
            if v is not None:
                v.latency_ms = (time.perf_counter() - t0) * 1000
                return self._finish(v)

        return self._finish(Verdict(
            Ruling.ALLOW, "pass", 1, proposed,
            latency_ms=(time.perf_counter() - t0) * 1000))

    # ── private ───────────────────────────────────────────────────────────────

    def _tier2(self, proposed: str, current: str, belief) -> Optional[Verdict]:
        self.stats["tier2_calls"] += 1
        prompt = (
            "You are the safety supervisor for an autonomous agent. "
            "Approve or refuse the proposed state transition.\n\n"
            f"current_state: {current}\n"
            f"proposed_state: {proposed}\n"
            f"belief_summary: {belief.summary() if hasattr(belief, 'summary') else str(belief)}\n\n"
            'Reply JSON only: {"ruling":"allow|deny|escalate",'
            '"reason":"<12 words max>","substitute":"<state or null>"}'
        )
        body = json.dumps({
            "model": MESH_LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 80,
            "temperature": 0.0,
        }).encode()
        try:
            req = urllib.request.Request(
                MESH_LLM_URL, data=body,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=MESH_LLM_TIMEOUT) as r:
                raw = json.loads(r.read())
            txt = (raw["choices"][0]["message"]["content"]
                   .replace("```json", "").replace("```", "").strip())
            out    = json.loads(txt)
            ruling = Ruling(out.get("ruling", "allow"))
            if ruling is Ruling.ALLOW:
                return None
            return Verdict(ruling, out.get("reason", "mesh-llm refusal"),
                           2, proposed, substitute=out.get("substitute"))
        except Exception as e:
            print(f"[supervisor] tier2 unavailable ({e}) — failing open")
            return None

    def _finish(self, v: Verdict) -> Verdict:
        self.stats[v.ruling.value] += 1
        if v.ruling is not Ruling.ALLOW:
            print(f'[SUPERVISOR] {v.ruling.value.upper()} {v.proposed}'
                  f' → {v.substitute or "—"} | {v.reason}'
                  f' (tier={v.tier}, {v.latency_ms:.2f}ms)')
        if v.ruling is Ruling.ESCALATE and self.escalate_cb is not None:
            try:
                if self.escalate_cb(v):
                    v.ruling = Ruling.ALLOW
                    v.reason += " | human override"
            except Exception:
                pass
        return v
