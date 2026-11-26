"""Daily overview: one row per panel game, saved as a Google Sheet.

Runs after the collector (python -m collector.overview). Everything comes from the
collector's own memory in state/, so it covers all data collected so far:
  state/games.parquet          panel, screening reply rate, AI disclosure, store info
  state/latest/app=*.parquet   latest version of every review
  state/comment_counts.parquet running comment and call-out counts per game, updated here
                               from each run's new comments (seeded once from comments/)
Writes overview/steam_overview_<Melbourne date>.csv; the workflow uploads it as a Sheet.
"""
import datetime as dt
import glob
import json
import os
import subprocess
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .run import DATA, STATE, RUN_ID, read_parquet, save_parquet

REMOTE = os.environ.get("RCLONE_REMOTE", "steamdrive:")
LABEL = dt.datetime.now(ZoneInfo("Australia/Melbourne")).date().isoformat()


def update_comment_counts():
    """Add comments from part files not counted before to the running per-game counts."""
    cols = ["appid", "recommendationid", "comment_id", "any_candidate", "excluded_hit"]
    path = STATE / "comment_counts.parquet"
    marker = STATE / "comment_counts_files.json"      # part files already counted
    counts = read_parquet(path)
    counted = set(json.loads(marker.read_text())) if marker.exists() and counts is not None \
        else set()
    files = glob.glob(str(DATA / "comments" / "**" / "*.parquet"), recursive=True)
    if counts is None and not os.environ.get("NO_DRIVE"):
        # first time: also count everything collected before this file existed
        seed = Path(tempfile.mkdtemp())
        try:
            subprocess.run(["rclone", "copy", f"{REMOTE}comments", str(seed)],
                           check=True, capture_output=True, timeout=900)
            files += glob.glob(str(seed / "**" / "*.parquet"), recursive=True)
        except Exception as e:
            print(f"overview: could not seed from comments/: {e}")
    todo = {Path(f).name: f for f in files if Path(f).name not in counted}
    if not todo:
        return counts
    new = pd.concat([pd.read_parquet(f, columns=cols) for f in todo.values()],
                    ignore_index=True).drop_duplicates("comment_id")
    g = new.groupby("appid").agg(
        comments=("comment_id", "size"),
        flagged=("any_candidate", lambda x: int(x.fillna(False).sum())),
        flagged_game_words=("excluded_hit", lambda x: int(x.fillna(False).sum())),
        threads_with_comments=("recommendationid", "nunique"))
    if counts is not None:
        g = counts.set_index("appid").add(g, fill_value=0)
    g = g.reset_index().astype({"appid": int})
    save_parquet(g, path)
    marker.write_text(json.dumps(sorted(counted | set(todo))))
    return g


def game_rows(games, counts):
    rows = []
    for g in games[games["in_panel"].fillna(False)].to_dict("records"):
        appid = int(g["appid"])
        rev = read_parquet(STATE / "latest" / f"app={appid}.parquet",
                           columns=["voted_up", "has_dev_response", "comment_count",
                                    "timestamp_created", "thread_scraped_date",
                                    "disappeared_date"])
        n = 0 if rev is None else len(rev)
        live = rev[rev["disappeared_date"].isna()] if n else rev
        dates = pd.to_datetime(live["timestamp_created"], unit="s", errors="coerce") if n else None
        rows.append({
            "game": g.get("name"),
            "appid": appid,
            "store_link": f"https://store.steampowered.com/app/{appid}/",
            "developer": g.get("developers"),
            "genres": g.get("genres"),
            "ai_disclosed": bool(g.get("ai_disclosed")) if pd.notna(g.get("ai_disclosed")) else "",
            "panel_group": "replies to reviews" if g.get("stratum") == "responsive"
            else "random comparison",
            "reply_rate_at_screening": round(float(g.get("screen_reply_rate") or 0), 4),
            "reviews_on_steam": g.get("total_reviews"),
            "reviews_collected": n,
            "share_recommended": round(float(live["voted_up"].mean()), 3) if n else "",
            "reviews_with_dev_reply": int(live["has_dev_response"].fillna(False).sum()) if n else 0,
            "dev_reply_rate": round(float(live["has_dev_response"].fillna(False).mean()), 4)
            if n else "",
            "reviews_with_comments": int((live["comment_count"].fillna(0) > 0).sum()) if n else 0,
            "threads_scraped": int(live["thread_scraped_date"].notna().sum()) if n else 0,
            "earliest_review": dates.min().date().isoformat() if n and dates.notna().any() else "",
            "latest_review": dates.max().date().isoformat() if n and dates.notna().any() else "",
            "reviews_removed": int(rev["disappeared_date"].notna().sum()) if n else 0,
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    c = counts.set_index("appid") if counts is not None and len(counts) else pd.DataFrame()
    for col, name in (("comments", "comments_collected"), ("flagged", "flagged_callouts")):
        out[name] = [int(c[col].get(a, 0)) if col in c else 0 for a in out["appid"]]
    out["flagged_per_1000_comments"] = [
        round(1000 * f / m, 1) if m else "" for f, m in
        zip(out["flagged_callouts"], out["comments_collected"])]
    return out.sort_values(["panel_group", "reviews_with_dev_reply"],
                           ascending=[False, False])


def main():
    games = read_parquet(STATE / "games.parquet")
    if games is None or "in_panel" not in games:
        print("overview: no panel yet")
        return
    counts = update_comment_counts()
    out = game_rows(games, counts)
    if out.empty:
        print("overview: panel is empty")
        return
    path = DATA / "overview" / f"steam_overview_{LABEL}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    (DATA / "runs").mkdir(parents=True, exist_ok=True)
    (DATA / "runs" / f"{RUN_ID}-overview.json").write_text(json.dumps({
        "games": len(out), "reviews": int(out["reviews_collected"].sum()),
        "reviews_with_dev_reply": int(out["reviews_with_dev_reply"].sum()),
        "comments": int(out["comments_collected"].sum()),
        "flagged_callouts": int(out["flagged_callouts"].sum()),
        "file": str(path.relative_to(DATA))}, indent=1))
    print(f"overview: {len(out)} games -> {path}")


if __name__ == "__main__":
    main()
