"""Stage 2 candidate net for call-outs (cheap regex, high recall).

This is the Steam version of the measurement plan's Stage 2. Candidates go on to
LLM coding (Stage 3) and human checks (Stage 4); a regex hit is NOT a call-out.
Merge Muru's YouTube patterns into FAMILIES once they're ported from R.
"""
import re

FAMILIES = {
    "ai_written": [
        r"\b(chat ?gpt|gpt-?\d|claude|gemini|llm)\b",
        r"\b(written|wrote|generated|made) (by|with|using) (an? )?(ai|chat ?gpt|bot)\b",
        r"\bai[- ](written|generated|slop|review|comment|response|reply)\b",
        r"\bsounds? like (an? )?(ai|chat ?gpt|bot)\b",
        r"\b(this|that|your) (review|comment|reply|response) (is|was|looks|reads)( like)? (ai|a bot|chat ?gpt)\b",
    ],
    "bot": [
        r"\b(bot|bots) (review|reviews|account|comment)s?\b",
        r"\b(you('re| are)|this is|that's) a bot\b",
    ],
    "paid_shill": [
        r"\bshill(s|ing|ed)?\b",
        r"\bpaid (review|reviewer|for this|to (say|write|post))\b",
        r"\b(got|get|getting) paid\b",
        r"\b(free key|key for (a|this) review|review copy)\b",
        r"\b(dev|developer)'?s? (alt|alt account|friend|mum|mom)\b",
    ],
    "fake": [
        r"\bfake (review|reviews|account|comment|positive|negative)\b",
        r"\b(astroturf|review bomb(ing|ed)?)\b",
    ],
    "no_experience": [
        r"\b(you|u) (didn'?t|did not|never) (even )?(play|played|try|tried|own)\b",
        r"\bwith (only )?\d+(\.\d+)? ?(hrs?|hours|minutes|mins)\b",
        r"\b(refunded|refund) (it|and)\b",
    ],
}

# Steam-specific false positives: game AI, cheating bots, game mechanics.
EXCLUDE = [
    r"\b(enemy|enemies|npc|npcs|companion|squad|teammate|opponent|ally|allied) ai\b",
    r"\bai (is|are) (dumb|stupid|smart|brain ?dead|broken|aggressive|terrible|good)\b",
    r"\b(aim ?bot|aimbots?|bot lobbies|bot matches?|vs bots|against bots|cheat(er|ers|ing))\b",
    r"\bfake (difficulty|walls?|hitbox|loading)\b",
    r"\bai (difficulty|director|pathing|behaviou?r)\b",
]

_FAM = {k: [re.compile(p, re.I) for p in v] for k, v in FAMILIES.items()}
_EXC = [re.compile(p, re.I) for p in EXCLUDE]


def candidate_flags(text):
    """Return dict: family -> bool, plus 'excluded_hit' and 'any_candidate'."""
    t = text or ""
    out = {f"cand_{k}": any(p.search(t) for p in pats) for k, pats in _FAM.items()}
    out["excluded_hit"] = any(p.search(t) for p in _EXC)
    out["any_candidate"] = any(out[f"cand_{k}"] for k in FAMILIES)
    return out
