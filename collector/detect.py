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

# Steam-specific false positives: game AI, cheating bots, game mechanics, AI graphics tech.
EXCLUDE = [
    r"\b(frame ?gen(eration)?|dlss|fsr|xess|upscal(e|er|ing)|ai (generated )?frames?|fake frames?)\b",
    r"\bai slop (gpu|card|frames?)\b",
    r"\b(enemy|enemies|npc|npcs|companion|squad|teammate|opponent|ally|allied) ai\b",
    r"\bai (is|are) (dumb|stupid|smart|brain ?dead|broken|aggressive|terrible|good)\b",
    r"\b(aim ?bot|aimbots?|bot lobbies|bot matches?|vs bots|against bots|cheat(er|ers|ing))\b",
    r"\bfake (difficulty|walls?|hitbox|loading)\b",
    r"\bai (difficulty|director|pathing|behaviou?r)\b",
]

# What a call-out points at. Steam comment threads are flat, so this reads the words used.
# Order matters: the first group that matches wins.
TARGETS = [
    # the developer's reply (brands' replies get called AI too - a separate arm)
    ("developer_reply", [
        r"\b(dev|devs|developers?|publisher|studio|pr|community manager|cm|support( team)?)\b.{0,40}"
        r"\b(response|reply|replies|message|answer|copy[- ]?paste|cut[- ]and[- ]paste|canned|template|bot|ai|chat ?gpt)\b",
        r"\b(ai|chat ?gpt|bot|canned|generic|copy[- ]?paste[d]?|cut[- ]and[- ]paste[d]?|template[d]?)\b.{0,30}"
        r"\b(response|reply|replies|message|answer)\b",
        r"\b(response|reply|message)\b.{0,30}\b(written|generated|made) (by|with) (ai|chat ?gpt|a bot)\b",
    ]),
    # another user's review or comment - the focus of the paper
    ("review_or_comment", [
        r"@\w+",
        r"\b(review|reviews|reviewer|comment|comments|post|op|account|profile)\b",
        r"\b(you|u|ur|your|you're|youre|this (guy|person|dude|user)|he|she|they)\b.{0,30}"
        r"\b(bot|ai|chat ?gpt|gpt|shill|paid|fake)\b",
        r"\b(bot|ai|chat ?gpt|shill)\b.{0,15}\b(much|alert|detected|lol|lmao)\b",
    ]),
    # the game itself being AI-made (AI art, AI code, AI voices) - kept, but not a call-out
    ("game", [
        r"\b(game|it|this|dev|devs|they)\b.{0,30}\b(ai|chat ?gpt|claude|llm)[- ]?(made|generated|coded|written|art|assets?|voices?|music|slop)\b",
        r"\b(ai|vibe)[- ]?(art|assets?|voices?|voice acting|music|images?|graphics|coded|generated content)\b",
        r"\b(asset flip|ai slop game|made with ai|uses ai)\b",
        r"\b(claude|chat ?gpt|cursor) coded\b",
    ]),
]

_FAM = {k: [re.compile(p, re.I) for p in v] for k, v in FAMILIES.items()}
_EXC = [re.compile(p, re.I) for p in EXCLUDE]
_TGT = [(name, [re.compile(p, re.I) for p in pats]) for name, pats in TARGETS]


def callout_target(text):
    """developer_reply / review_or_comment / game / unclear - a keyword guess for checking by hand."""
    t = text or ""
    for name, pats in _TGT:
        if any(p.search(t) for p in pats):
            return name
    return "unclear"


def candidate_flags(text):
    """Return dict: family -> bool, plus 'excluded_hit' and 'any_candidate'."""
    t = text or ""
    out = {f"cand_{k}": any(p.search(t) for p in pats) for k, pats in _FAM.items()}
    out["excluded_hit"] = any(p.search(t) for p in _EXC)
    out["any_candidate"] = any(out[f"cand_{k}"] for k in FAMILIES) and not out["excluded_hit"]
    out["callout_target"] = callout_target(t) if out["any_candidate"] else None
    return out


def retag(df):
    """Re-apply the current rules to stored comments (so tuning applies to old data too)."""
    if df is None or len(df) == 0 or "comment_text" not in df:
        return df
    flags = [candidate_flags(t) for t in df["comment_text"]]
    for k in flags[0]:
        df[k] = [f[k] for f in flags]
    return df
