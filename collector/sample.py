"""Daily call-out review sample: a small sheet a person can check by hand.

Runs after the collector (python -m collector.sample). It picks
  - up to `n_candidates` regex call-out candidates not sampled before, spread across
    the candidate families (AI-written, bot, paid shill, fake, no experience), and
  - `n_random_checks` random comments the net did NOT flag (to catch misses),
adds the review being commented on (text, recommended or not, developer reply, link),
and writes callout_review/callout_sample_<date>.csv, which the workflow uploads to
Drive as a Google Sheet. The reviewer fills in the last three columns.

Review text comes from reviews/ on Drive, downloaded only for the games in the sample.
The first time, it also pulls earlier comments/ so older candidates get reviewed too.
"""
import glob
import json
import os
import random
import subprocess
import tempfile
import datetime as dt
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd

from . import detect
from .run import DATA, STATE, TODAY, RUN_ID, load_cfg, read_parquet, save_parquet

REMOTE = os.environ.get("RCLONE_REMOTE", "steamdrive:")
# Melbourne date and time for names: one sheet per run, so a later run the same day
# never overwrites a sheet that is being filled in
LABEL = dt.datetime.now(ZoneInfo("Australia/Melbourne")).strftime("%Y-%m-%d_%H%M")
FAMILIES = ["ai_written", "bot", "paid_shill", "fake", "no_experience"]


def _rclone(args):
    if os.environ.get("NO_DRIVE"):
        return False
    try:
        subprocess.run(["rclone", *args], check=True, capture_output=True, timeout=900)
        return True
    except Exception as e:  # sample is a helper; never fail the run over it
        print(f"rclone {' '.join(args[:2])} failed: {e}")
        return False


def _read_dir(pattern):
    parts = [pd.read_parquet(p) for p in glob.glob(pattern, recursive=True)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def load_comments(first_time):
    local = _read_dir(str(DATA / "comments" / "**" / "*.parquet"))
    if first_time:
        seed = Path(tempfile.mkdtemp())
        if _rclone(["copy", f"{REMOTE}comments", str(seed)]):
            local = pd.concat([local, _read_dir(str(seed / "**" / "*.parquet"))],
                              ignore_index=True)
    if len(local):
        local = local.drop_duplicates("comment_id")
    return local


def _spread(cand, n, rng, chosen):
    """Round-robin across call-out families so rarer types still show up."""
    by_fam = {f: list(cand[cand[f"cand_{f}"].fillna(False)]["comment_id"]) for f in FAMILIES
              if f"cand_{f}" in cand}
    for ids in by_fam.values():
        rng.shuffle(ids)
    i, start = 0, len(chosen)
    while len(chosen) - start < n and any(i < len(v) for v in by_fam.values()):
        for ids in by_fam.values():
            if i < len(ids) and ids[i] not in chosen and len(chosen) - start < n:
                chosen.append(ids[i])
        i += 1
    return chosen


def pick(comments, sampled, n_cand, n_rand, seed, focus_share=0.75):
    """Flagged comments, mostly call-outs of reviews/comments (the paper's focus), plus a
    random check of unflagged ones."""
    pool = comments[~comments["comment_id"].astype(str).isin(sampled)]
    rng = random.Random(seed)
    cand = pool[pool["any_candidate"].fillna(False)]
    focus = cand[cand["callout_target"] == "review_or_comment"]
    chosen = _spread(focus, int(round(n_cand * focus_share)), rng, [])
    chosen = _spread(cand[~cand["comment_id"].isin(chosen)], n_cand - len(chosen), rng, chosen)
    rest = list(pool[~pool["any_candidate"].fillna(False)]["comment_id"])
    rng.shuffle(rest)
    rows = pool.set_index("comment_id")
    out = rows.loc[chosen].assign(net="flagged")
    if n_rand and rest:
        out = pd.concat([out, rows.loc[rest[:n_rand]].assign(net="random check")])
    return out.reset_index()


def review_context(appids):
    """Latest version of each review for these games, from reviews/ on Drive (or local)."""
    tmp = Path(tempfile.mkdtemp())
    filt = tmp / "filter.txt"
    filt.write_text("".join(f"+ **/app={a}-*.parquet\n" for a in appids) + "- **\n")
    _rclone(["copy", f"{REMOTE}reviews", str(tmp / "reviews"), "--filter-from", str(filt)])
    cols = ["appid", "recommendationid", "review", "voted_up", "developer_response",
            "timestamp_updated"]
    frames = []
    for pattern in (str(tmp / "reviews" / "**" / "*.parquet"),
                    str(DATA / "reviews" / "**" / "*.parquet")):
        for p in glob.glob(pattern, recursive=True):
            if any(f"app={a}-" in Path(p).name for a in appids):
                frames.append(pd.read_parquet(p, columns=cols))
    if not frames:
        return pd.DataFrame(columns=cols)
    rev = pd.concat(frames, ignore_index=True)
    rev["recommendationid"] = rev["recommendationid"].astype(str)
    return rev.sort_values("timestamp_updated").drop_duplicates("recommendationid", keep="last")


def steamids(appids):
    out = {}
    for a in appids:
        df = read_parquet(STATE / "latest" / f"app={a}.parquet",
                          columns=["recommendationid", "author_steamid"])
        if df is not None:
            out.update({str(r): s for r, s in zip(df["recommendationid"], df["author_steamid"])})
    return out


def clip(text, n):
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[: n - 1] + "…"


def main():
    cfg = load_cfg().get("callout_sample", {})
    n_cand = int(cfg.get("n_candidates", 40))
    n_rand = int(cfg.get("n_random_checks", 10))
    sampled_path = STATE / "callout_sampled.parquet"
    sampled_df = read_parquet(sampled_path)
    first_time = sampled_df is None
    sampled = set() if first_time else set(sampled_df["comment_id"].astype(str))

    comments = load_comments(first_time)
    if comments.empty:
        print("callout sample: no comments yet")
        return
    comments["comment_id"] = comments["comment_id"].astype(str)
    comments = detect.retag(comments)
    s = pick(comments, sampled, n_cand, n_rand, seed=RUN_ID)
    if s.empty:
        print("callout sample: nothing new to sample")
        return

    appids = sorted({int(a) for a in s["appid"]})
    rev = review_context(appids)
    s["recommendationid"] = s["recommendationid"].astype(str)
    s = s.merge(rev.drop(columns=["appid"]), on="recommendationid", how="left")
    ids = steamids(appids)
    games = read_parquet(STATE / "games.parquet")
    names = {} if games is None or "name" not in games else \
        dict(zip(games["appid"].astype(int), games["name"]))

    def flags(r):
        f = [x.replace("_", " ") for x in FAMILIES if r.get(f"cand_{x}")]
        if r.get("excluded_hit"):
            f.append("(game-AI/cheat words too)")
        return ", ".join(f)

    def link(r):
        sid = ids.get(str(r["recommendationid"]))
        return f"https://steamcommunity.com/profiles/{sid}/recommended/{int(r['appid'])}/" \
            if sid else ""

    def says(v):
        return "" if pd.isna(v) else ("Recommended" if v else "Not recommended")

    out = pd.DataFrame({
        "sample_id": [f"{LABEL}-{i + 1:02d}" for i in range(len(s))],
        "game": [names.get(int(a), str(a)) for a in s["appid"]],
        "review_link": [link(r) for r in s.to_dict("records")],
        "review_says": [says(v) for v in s.get("voted_up", pd.Series([None] * len(s)))],
        "review_text": [clip(t, 400) for t in s.get("review", pd.Series([""] * len(s)))],
        "developer_reply": [clip(t, 300) for t in
                            s.get("developer_response", pd.Series([""] * len(s)))],
        "comment_date": pd.to_datetime(s["timestamp"], unit="s", errors="coerce")
        .dt.strftime("%Y-%m-%d").fillna(""),
        "comment_text": [clip(t, 600) for t in s["comment_text"]],
        "regex_flags": [flags(r) for r in s.to_dict("records")],
        "likely_target": s["callout_target"].map({
            "review_or_comment": "another review/comment", "developer_reply": "developer reply",
            "game": "the game (AI-made)", "unclear": "unclear"}).fillna(""),
        "net": s["net"],
        "is_callout (Y/N)": "",
        "true_target (review-comment / dev reply / game / none)": "",
        "callout_type (AI / fake / shill / no experience / other)": "",
        "notes": "",
        "comment_id": s["comment_id"],
    })
    path = DATA / "callout_review" / f"callout_sample_{LABEL}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)

    new = pd.DataFrame({"comment_id": list(s["comment_id"]), "sampled_date": TODAY,
                        "net": list(s["net"])})
    save_parquet(new if first_time else pd.concat([sampled_df, new], ignore_index=True),
                 sampled_path)

    log_path = DATA / "runs" / f"{RUN_ID}-sample.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps({
        "date": TODAY, "rows": len(out), "flagged": int((out["net"] == "flagged").sum()),
        "random_checks": int((out["net"] == "random check").sum()),
        "with_review_text": int((out["review_text"] != "").sum()),
        "seeded_from_earlier_runs": first_time, "file": str(path.relative_to(DATA))}, indent=1))
    print(f"callout sample: {len(out)} rows -> {path}")


if __name__ == "__main__":
    main()
