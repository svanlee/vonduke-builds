# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Axon Command Parser                ║
# ║  Rule-based fast path + local-LLM fallback for       ║
# ║  turning a voice transcript into a structured intent ║
# ╚══════════════════════════════════════════════════════╝

import json
import re
import urllib.request
import urllib.error

import config

# (regex, goal, priority) — checked in order, first match wins.
# Goal names line up with memory.goals.GOAL_PRIORITIES where possible so
# they slot straight into the existing goal stack / retirement logic.
_RULES = [
    (r"\bmine (some |for )?diamonds?\b|\bgo mine diamonds?\b|\bfind diamonds?\b|\bdig for diamonds?\b", "mine_diamonds", 10),
    (r"\bmine (some |for )?coal\b|\bgo mine coal\b", "mine_coal", 10),
    (r"\b(come back|return|head back|get back)( to)? (the )?base\b|\bcome home\b|\bgo home\b", "return_to_base", 10),
    (r"\bstop\b|\bhalt\b|\bfreeze\b|\bcancel that\b|\bstand down\b", "idle", 99),
    (r"\bfind shelter\b|\bbuild (a )?shelter\b|\btake cover\b|\bhide\b", "find_shelter", 8),
    (r"\bexplore\b|\blook around\b|\bscout\b|\bgo explore\b", "explore", 5),
    (r"\bfind food\b|\bget (some )?food\b|\bgo eat\b|\bfind something to eat\b", "find_food", 8),
    (r"\bcraft (a |an )?(wood|wooden) pickaxe\b", "craft_wood_pickaxe", 6),
    (r"\bcraft (a |an )?stone pickaxe\b", "craft_stone_pickaxe", 6),
    (r"\bcraft (a |an )?iron pickaxe\b", "craft_iron_pickaxe", 6),
    (r"\bcraft (a |an )?diamond pickaxe\b", "craft_diamond_pickaxe", 6),
]
_COMPILED_RULES = [(re.compile(p, re.IGNORECASE), goal, pr) for p, goal, pr in _RULES]

_STATUS_PATTERN = re.compile(
    r"\bwhat are you doing\b|\bwhat'?s your status\b|\bstatus report\b|"
    r"\bwhat'?s the plan\b|\bwhat'?s your goal\b|\bcurrent goal\b",
    re.IGNORECASE,
)


def parse(transcript: str) -> dict:
    """
    Parse a voice command transcript into a structured intent:
      {"type": "goal",  "goal": str, "priority": int, "source": "rule"|"local_llm"}
      {"type": "query", "query": "status"}
      {"type": "unknown"}
    """
    text = (transcript or "").strip()
    if not text:
        return {"type": "unknown"}

    if _STATUS_PATTERN.search(text):
        return {"type": "query", "query": "status"}

    for pattern, goal, priority in _COMPILED_RULES:
        if pattern.search(text):
            return {"type": "goal", "goal": goal, "priority": priority, "source": "rule"}

    return _parse_with_local_llm(text)


def _parse_with_local_llm(text: str) -> dict:
    """Single local-LLM call for free-form commands the rules didn't
    catch. At most one API call per command; never retries."""
    if not config.LOCAL_LLM_ENABLED:
        print('[AXON] local LLM disabled — cannot parse free-form command')
        return {"type": "unknown"}

    prompt = f"""You control a Minecraft AI agent named AKSUMAEL. Turn the voice command
below into a short snake_case goal name (e.g. mine_diamonds, mine_coal,
return_to_base, find_shelter, explore, find_food, idle) and a priority
1-10 (10 = most urgent, drop everything else).

Voice command: "{text}"

Respond with JSON only, no other text: {{"goal": "snake_case_goal", "priority": 1-10}}"""

    payload = json.dumps({
        "model": config.LOCAL_LLM_MODEL,
        # Generous budget — this model 'thinks' before answering, which
        # can burn several hundred tokens before the actual JSON reply.
        "max_tokens": 800,
        "messages": [{"role": "user", "content": prompt}],
    }).encode('utf-8')

    req = urllib.request.Request(
        f"{config.LOCAL_LLM_URL}/chat/completions",
        data=payload,
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read())
        choices = data.get('choices') or []
        content = choices[0]['message'].get('content') if choices else None
        if not content:
            return {"type": "unknown"}
        raw = content.strip()
        if raw.startswith('```'):
            raw = '\n'.join(raw.split('\n')[1:-1])
        parsed = json.loads(raw)
        goal = parsed.get('goal')
        if not goal:
            return {"type": "unknown"}
        priority = int(parsed.get('priority', 5))
        return {"type": "goal", "goal": goal, "priority": priority, "source": "local_llm"}
    except Exception as e:
        print(f'[AXON] local-LLM parse error: {e}')
        return {"type": "unknown"}
