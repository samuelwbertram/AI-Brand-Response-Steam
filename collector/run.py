"""Daily Steam collector for the AI Brand Response project.

Modes
  smoke  - one call to each source; reports which fields and methods work
  screen - screen candidate games and pick the panel
  daily  - new reviews, re-sweeps, backfill, then comment threads (screens first if due)

Data lives in ./data, which the workflow syncs with the steam_reviews Drive folder.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

import pandas as pd
import yaml

from . import community, detect, discover, steam_api, store
from .http_client import PoliteClient, RateLimited

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
STATE = DATA / "state"
TODAY = dt.datetime.now(dt.timezone.utc).date().isoformat()
RUN_ID = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def load_cfg():
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def now_ts():
    return int(time.time())


def frac(key):
    """Deterministic 0-1 value from an id (for reproducible random samples)."""
    return int(hashlib.sha1(str(key).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def save_parquet(df, path):
    """Write to a temp file then swap it in, so mid-run uploads never see half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def write_parquet(df, rel):
    if df is None or len(df) == 0:
        return None
    save_parquet(df, DATA / rel)
    return str(rel)


def read_parquet(path, columns=None):
    if Path(path).exists():
        return pd.read_parquet(path, columns=columns)
    return None


class Budget:
    def __init__(self, minutes):
        self.end = time.monotonic() + minutes * 60

    def left(self):
        return self.end - time.monotonic()

    def ok(self, reserve_s=0):
        return self.left() > reserve_s


# ---------------------------------------------------------------- smoke test

def smoke(cfg):
    api = PoliteClient(cfg["api"]["seconds_between_calls"])
    com = PoliteClient(cfg["community"]["seconds_between_calls"], stop_on_429=True)
    rep = {"run_id": RUN_ID, "date": TODAY, "checks": {}}

    def check(name, fn):
        try:
            rep["checks"][name] = {"ok": True, **fn()}
        except Exception as e:  # report everything, never crash the smoke test
            rep["checks"][name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    state = {}

    def reviews():
        p = steam_api.fetch_page(api, 1295660, page_size=20)
        revs = p.get("reviews", [])
        state["page"] = p
        with_reply = [r for r in revs if r.get("developer_response")]
        return {
            "top_level_keys": sorted(p.keys()),
            "n_reviews": len(revs),
            "review_keys": sorted(revs[0].keys()) if revs else [],
            "author_keys": sorted((revs[0].get("author") or {}).keys()) if revs else [],
            "has_comment_count": bool(revs) and "comment_count" in revs[0],
            "reviews_with_dev_reply_in_page": len(with_reply),
            "cursor_returned": bool(p.get("cursor")),
        }

    def threads():
        cursor, found = "*", None
        for _ in range(10):
            p = steam_api.fetch_page(api, 1295660, cursor=cursor, page_size=100)
            for r in p.get("reviews", []):
                if (r.get("comment_count") or 0) > 0:
                    found = r
                    break
            if found or not p.get("cursor"):
                break
            cursor = p["cursor"]
        if not found:
            return {"note": "no commented review in first 1,000 recent reviews"}
        sid, rid = found["author"]["steamid"], found["recommendationid"]
        out = {"comment_count_from_api": found.get("comment_count")}
        for method in ("render", "page"):
            try:
                if method == "render":
                    rows, total = community.fetch_thread_render(com, 1295660, sid, rid)
                else:
                    rows, total = community.fetch_thread_page(com, 1295660, sid, rid)
                out[method] = {
                    "ok": True, "n_parsed": len(rows), "total": total,
                    "share_with_unix_timestamp": (
                        sum(1 for x in rows if x["timestamp"]) / len(rows) if rows else None),
                    "example_timestamp_display": rows[0]["timestamp_display"] if rows else None,
                    "example_badges": [x["author_badges"] for x in rows if x["author_badges"]][:3],
                }
            except Exception as e:
                out[method] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return out

    def store_meta():
        d = store.app_details(api, 2073850)
        disclosed, text = store.ai_disclosure(api, 2073850)
        return {"name": (d or {}).get("name"), "ai_disclosed": disclosed,
                "disclosure_start": (text or "")[:200]}

    check("reviews_api", reviews)
    check("comment_threads", threads)
    check("store_and_ai_flag", store_meta)
    rep["calls"] = {"api": api.calls, "community": com.calls}
    write_json(rep, f"smoke/smoke-{RUN_ID}.json")
    print(json.dumps(rep, indent=2))
    return rep


def write_json(obj, rel):
    path = DATA / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str))


# ---------------------------------------------------------------- candidates

def load_candidates():
    """Hand-picked seeds (games/candidates.csv) plus games found by discovery."""
    cands = pd.read_csv(ROOT / "games" / "candidates.csv")[["appid", "expected_name", "source"]]
    found = read_parquet(STATE / "candidates.parquet")
    if found is not None:
        cands = pd.concat([cands, found], ignore_index=True)
    return cands.drop_duplicates("appid", keep="first")


def run_discovery(cfg, api, log):
    dc = cfg["discovery"]
    old = read_parquet(STATE / "candidates.parquet")
    n_old = 0 if old is None else len(old)
    fresh = old is not None and (dt.date.fromisoformat(TODAY) - dt.date.fromisoformat(
        str(old["discovered_date"].max()))).days < dc["refresh_every_days"]
    if n_old >= dc["target_candidates"] and fresh:
        return
    try:
        known = [] if old is None else old["appid"].astype(int).tolist()
        found, pages = discover.discover(api, dc["target_candidates"],
                                         cfg["screening"]["min_total_reviews"], known=known)
    except Exception as e:
        log["errors"].append(f"discovery: {type(e).__name__}: {e}")
        return
    new = pd.DataFrame(found)
    if len(new):
        new["source"] = "store_search"
        new["discovered_date"] = TODAY
        out = new if old is None else pd.concat([old, new], ignore_index=True)
        save_parquet(out, STATE / "candidates.parquet")
    elif old is not None:
        old["discovered_date"] = TODAY
        save_parquet(old, STATE / "candidates.parquet")
    log["discovery"] = {"pages": pages, "new_candidates": int(len(new)),
                        "total_candidates": n_old + int(len(new))}


# ---------------------------------------------------------------- screening

def screen(cfg, api, budget, log):
    sc = cfg["screening"]
    cands = load_candidates()
    screen_budget = Budget(min(sc.get("max_minutes_per_run", 150), budget.left() / 60))
    old = read_parquet(STATE / "games.parquet")
    done = {}
    if old is not None:
        for r in old.to_dict("records"):
            done[int(r["appid"])] = r
    rows, stopped_for_time, fresh = [], False, 0
    for c in cands.to_dict("records"):
        appid = int(c["appid"])
        prev = done.get(appid)
        if prev and prev.get("screened_date") and (
                dt.date.fromisoformat(TODAY) - dt.date.fromisoformat(prev["screened_date"])
        ).days < sc["rescreen_every_days"]:
            rows.append(prev)
            continue
        if not screen_budget.ok(0):
            stopped_for_time = True
            if prev:
                rows.append(prev)
            continue
        row = dict(prev or {})
        row.update({"appid": appid, "expected_name": c["expected_name"], "source": c["source"],
                    "in_panel": False, "screened_date": None, "screen_error": None})
        try:
            meta = store.app_details(api, appid) or {}
            row.update(meta)
            row["name_matches"] = (meta.get("name") or "").lower().strip() == \
                str(c["expected_name"]).lower().strip()
            row["ai_disclosed"], row["ai_disclosure_text"] = store.ai_disclosure(api, appid)
            n, n_reply, cursor, total = 0, 0, "*", None
            while n < sc["reviews_per_game"]:
                p = steam_api.fetch_page(api, appid, cursor=cursor,
                                         page_size=cfg["api"]["page_size"],
                                         languages=cfg["api"]["languages"])
                if total is None:
                    total = p.get("total_matching") or \
                        (p.get("query_summary") or {}).get("total_reviews")
                revs = p.get("reviews", [])
                n += len(revs)
                n_reply += sum(1 for r in revs if r.get("developer_response"))
                if not revs or not p.get("cursor") or p["cursor"] == cursor:
                    break
                cursor = p["cursor"]
            rate = n_reply / n if n else 0.0
            row.update({
                "screened_date": TODAY, "screen_n": n, "screen_dev_replies": n_reply,
                "screen_reply_rate": rate, "total_reviews": total,
            })
            big_enough = (total or n) >= sc["min_total_reviews"]
            row["stratum"] = "responsive" if rate >= sc["reply_rate_cutoff"] else "base_rate"
            row["in_panel"] = bool(big_enough and (
                rate >= sc["reply_rate_cutoff"] or frac(appid) < sc["random_keep_share"]))
        except RateLimited as e:
            log["notes"].append(f"screen rate-limited at {appid}: {e}")
            if prev:
                rows.append(prev)
            break
        except Exception as e:
            log["errors"].append(f"screen {appid}: {type(e).__name__}: {e}")
            row["screen_error"] = str(e)[:300]
        rows.append(row)
        fresh += 1
        if fresh % 25 == 0:      # checkpoint: progress survives a crash and shows up on Drive
            save_parquet(pd.DataFrame(rows), STATE / "games.parquet")
    games = pd.DataFrame(rows)
    if stopped_for_time:
        log["notes"].append("screening paused for time; it continues next run")
    log["screening_left"] = int(len(cands) - len(games))
    save_parquet(games, STATE / "games.parquet")
    write_parquet(games, f"screening/screen-{RUN_ID}.parquet")
    log["screened"] = int(len(games))
    log["panel_size"] = int(games.get("in_panel", pd.Series(dtype=bool)).fillna(False).sum())
    return games


# ---------------------------------------------------------------- reviews

class AppState:
    """Latest known version of every review for one game."""

    COLS = ["recommendationid", "content_hash", "first_seen", "last_seen",
            "timestamp_created", "voted_up", "comment_count", "has_dev_response",
            "author_steamid", "thread_scraped_date", "thread_comment_count",
            "thread_priority", "disappeared_date"]

    def __init__(self, appid):
        self.appid = appid
        self.path = STATE / "latest" / f"app={appid}.parquet"
        df = read_parquet(self.path)
        self.rows = {} if df is None else {r["recommendationid"]: r for r in df.to_dict("records")}
        self.versions, self.metrics, self.gone = [], [], []

    def ingest(self, raw_reviews, cfg):
        seen = []
        for raw in raw_reviews:
            row = steam_api.flatten_review(self.appid, raw)
            rid = row["recommendationid"]
            seen.append(rid)
            h = steam_api.content_hash(row)
            prev = self.rows.get(rid)
            if prev is None or prev["content_hash"] != h:
                v = {k: val for k, val in row.items() if k != "author_steamid"}
                v.update({"snapshot_date": TODAY, "run_id": RUN_ID,
                          "change": "new" if prev is None else "changed"})
                self.versions.append(v)
            if (row["comment_count"] or 0) > 0 or row["developer_response"]:
                self.metrics.append({
                    "appid": self.appid, "recommendationid": rid, "snapshot_date": TODAY,
                    "votes_up": row["votes_up"], "votes_funny": row["votes_funny"],
                    "weighted_vote_score": row["weighted_vote_score"],
                    "comment_count": row["comment_count"], "voted_up": row["voted_up"],
                    "has_dev_response": bool(row["developer_response"])})
            new = dict(prev or {})
            new.update({
                "recommendationid": rid, "content_hash": h,
                "first_seen": (prev or {}).get("first_seen") or TODAY, "last_seen": TODAY,
                "timestamp_created": row["timestamp_created"], "voted_up": row["voted_up"],
                "comment_count": row["comment_count"] or 0,
                "has_dev_response": bool(row["developer_response"]),
                "author_steamid": row["author_steamid"], "disappeared_date": None})
            if new.get("thread_priority") is None or new["has_dev_response"]:
                new["thread_priority"] = 1 if new["has_dev_response"] else (
                    2 if frac(rid) < cfg["threads"]["no_reply_sample_share"] else 3)
            self.rows[rid] = new
        return seen

    def mark_gone(self, start, end, seen):
        """After a complete pass over [start, end], reviews we knew but didn't see are gone."""
        seen = set(seen)
        for rid, r in self.rows.items():
            tc = r.get("timestamp_created") or 0
            if start <= tc <= end and rid not in seen and not r.get("disappeared_date"):
                r["disappeared_date"] = TODAY
                self.gone.append({"appid": self.appid, "recommendationid": rid,
                                  "disappeared_date": TODAY})

    def save(self):
        if self.rows:
            df = pd.DataFrame(list(self.rows.values()))
            for c in self.COLS:
                if c not in df:
                    df[c] = None
            save_parquet(df[self.COLS], self.path)
        tag = f"app={self.appid}-{RUN_ID}.parquet"
        write_parquet(pd.DataFrame(self.versions), f"reviews/date={TODAY}/{tag}")
        write_parquet(pd.DataFrame(self.metrics), f"metrics/date={TODAY}/{tag}")
        write_parquet(pd.DataFrame(self.gone), f"disappeared/date={TODAY}/{tag}")
        n = (len(self.versions), len(self.metrics), len(self.gone))
        self.versions, self.metrics, self.gone = [], [], []
        return n


def pull_range(cfg, api, st, start, end, budget, max_calls=None, cursor="*"):
    """Page through [start, end]. Returns (complete, next_cursor, seen_ids, calls)."""
    seen, calls = [], 0
    while budget.ok(1800) and (max_calls is None or calls < max_calls):
        p = steam_api.fetch_page(api, st.appid, cursor=cursor, page_size=cfg["api"]["page_size"],
                                 languages=cfg["api"]["languages"], date_start=start, date_end=end)
        calls += 1
        revs = p.get("reviews", [])
        seen += st.ingest(revs, cfg)
        nxt = p.get("cursor")
        if not revs or not nxt or nxt == cursor:
            return True, None, seen, calls
        cursor = nxt
    return False, cursor, seen, calls


def collect_reviews(cfg, api, games, budget, log):
    progress_path = STATE / "progress.json"
    prog = json.loads(progress_path.read_text()) if progress_path.exists() else {}
    panel = games[games["in_panel"].fillna(False)]["appid"].astype(int).tolist()
    day_index = dt.date.fromisoformat(TODAY).toordinal()
    api_deadline = Budget(budget.left() / 60 * cfg.get("api_time_share", 0.5))
    counts = {"incremental_calls": 0, "resweep_calls": 0, "backfill_calls": 0,
              "versions": 0, "metric_rows": 0, "disappeared": 0}
    bf_start = int(dt.datetime.fromisoformat(cfg["api"]["backfill_start"])
                   .replace(tzinfo=dt.timezone.utc).timestamp())
    t_now = now_ts()

    # Three passes so every game gets its cheap daily update before any backfill.
    for phase in ("incremental", "resweep", "backfill"):
        for appid in panel:
            if not api_deadline.ok(0):
                log["notes"].append(f"API time share used up during {phase}")
                break
            p = prog.setdefault(str(appid), {})
            st = AppState(appid)
            try:
                if phase == "incremental":
                    start = t_now - cfg.get("incremental_window_days", 14) * 86400
                    done, _, seen, c = pull_range(cfg, api, st, start, t_now, api_deadline)
                    counts["incremental_calls"] += c
                    p["incremental_last"] = TODAY
                elif phase == "resweep":
                    if (appid + day_index) % cfg["resweep"]["every_days"] or not p.get("backfill_done"):
                        continue
                    start = t_now - cfg["resweep"]["window_days"] * 86400
                    done, _, seen, c = pull_range(cfg, api, st, start, t_now, api_deadline)
                    counts["resweep_calls"] += c
                    if done:
                        st.mark_gone(start, t_now - 86400, seen)
                        p["resweep_last"] = TODAY
                else:
                    if p.get("backfill_done"):
                        continue
                    p.setdefault("backfill_end", t_now)
                    done, cur, seen, c = pull_range(
                        cfg, api, st, bf_start, p["backfill_end"], api_deadline,
                        max_calls=cfg.get("backfill_calls_per_game_per_run", 1500),
                        cursor=p.get("backfill_cursor", "*"))
                    counts["backfill_calls"] += c
                    p["backfill_cursor"] = cur
                    p["backfill_done"] = bool(done)
            except RateLimited as e:
                log["notes"].append(f"API rate-limited ({phase}, {appid}): {e}")
                st.save()
                api_deadline = Budget(0)
                break
            except Exception as e:
                log["errors"].append(f"{phase} {appid}: {type(e).__name__}: {e}")
            v, m, g = st.save()
            counts["versions"] += v
            counts["metric_rows"] += m
            counts["disappeared"] += g
            progress_path.write_text(json.dumps(prog, indent=1))
    log["reviews"] = counts
    log["backfill_remaining"] = [a for a in panel if not prog.get(str(a), {}).get("backfill_done")]


# ---------------------------------------------------------------- threads

def collect_threads(cfg, com, games, budget, log):
    panel = games[games["in_panel"].fillna(False)]["appid"].astype(int).tolist()
    recheck = cfg["threads"]["recheck_every_days"]
    queue = []
    for appid in panel:
        st = AppState(appid)
        for r in st.rows.values():
            cc = r.get("comment_count") or 0
            if cc <= 0 or r.get("disappeared_date") or not r.get("author_steamid"):
                continue
            last = r.get("thread_scraped_date")
            due = (last is None or cc != r.get("thread_comment_count") or
                   (dt.date.fromisoformat(TODAY) - dt.date.fromisoformat(last)).days >= recheck)
            if due:
                queue.append((int(r.get("thread_priority") or 3), last is not None,
                              appid, r["recommendationid"], r["author_steamid"], cc))
    queue.sort()  # priority 1 first, never-scraped before re-checks
    log["threads_queued"] = len(queue)

    seen_path = STATE / "comments_seen.parquet"
    seen_df = read_parquet(seen_path)
    seen = set() if seen_df is None else set(seen_df["comment_id"].astype(str))
    method = (json.loads((STATE / "progress.json").read_text()).get("_thread_method")
              if (STATE / "progress.json").exists() else None)
    new_rows, updates = [], {}
    tot = {"scraped": 0, "new_comments": 0, "candidates": 0, "parts": 0}

    def flush():
        for appid, recs in updates.items():
            st = AppState(appid)
            for rid, cc in recs.items():
                if rid in st.rows:
                    st.rows[rid]["thread_scraped_date"] = TODAY
                    st.rows[rid]["thread_comment_count"] = cc
            st.save()
        if new_rows:
            tot["parts"] += 1
            write_parquet(pd.DataFrame(new_rows),
                          f"comments/date={TODAY}/part-{RUN_ID}-{tot['parts']:03d}.parquet")
            tot["new_comments"] += len(new_rows)
            tot["candidates"] += int(sum(r["any_candidate"] for r in new_rows))
        save_parquet(pd.DataFrame({"comment_id": sorted(seen)}), seen_path)
        new_rows.clear()
        updates.clear()

    try:
        for prio, _, appid, rid, sid, cc in queue:
            if not budget.ok(900):
                log["notes"].append("thread time used up")
                break
            try:
                rows, total, method = community.fetch_thread(
                    com, appid, sid, rid, cfg["community"]["comments_per_call"], method)
            except ValueError as e:
                log["errors"].append(f"thread {rid}: {e}")
                continue
            tot["scraped"] += 1
            for row in rows:
                if row["comment_id"] in seen:
                    continue
                seen.add(row["comment_id"])
                row.update(detect.candidate_flags(row["comment_text"]))
                row.update({"thread_priority": prio, "first_seen": TODAY, "run_id": RUN_ID})
                new_rows.append(row)
            updates.setdefault(appid, {})[rid] = cc
            if tot["scraped"] % 200 == 0:   # about every 10 minutes at 3 s a page
                flush()
    except RateLimited as e:
        log["notes"].append(f"community rate-limited after {tot['scraped']} threads: {e}")
    flush()
    if method:
        pp = STATE / "progress.json"
        prog = json.loads(pp.read_text()) if pp.exists() else {}
        prog["_thread_method"] = method
        pp.write_text(json.dumps(prog, indent=1))
    log["threads"] = {"scraped": tot["scraped"], "new_comments": tot["new_comments"],
                      "candidate_callouts": tot["candidates"], "method": method}


# ---------------------------------------------------------------- main

def daily(cfg, force_screen=False):
    STATE.mkdir(parents=True, exist_ok=True)
    budget = Budget(cfg["time_budget_minutes"])
    api = PoliteClient(cfg["api"]["seconds_between_calls"], cfg["api"]["max_retries"])
    com = PoliteClient(cfg["community"]["seconds_between_calls"],
                       stop_on_429=cfg["community"]["stop_on_first_429"])
    log = {"run_id": RUN_ID, "date": TODAY, "notes": [], "errors": []}
    t0 = time.time()
    try:
        games = read_parquet(STATE / "games.parquet")
        due = games is None or force_screen or "screened_date" not in games or \
            games["screened_date"].isna().any() or any(
            (dt.date.fromisoformat(TODAY) - dt.date.fromisoformat(d)).days
            >= cfg["screening"]["rescreen_every_days"] for d in games["screened_date"].dropna())
        run_discovery(cfg, api, log)
        new_cands = games is None or not set(
            load_candidates()["appid"]).issubset(set(games["appid"]))
        if due or new_cands:
            games = screen(cfg, api, budget, log)
        collect_reviews(cfg, api, games, budget, log)
        collect_threads(cfg, com, games, budget, log)
    finally:
        log["calls"] = {"api": api.calls, "api_errors": api.errors,
                        "community": com.calls, "community_errors": com.errors}
        log["minutes"] = round((time.time() - t0) / 60, 1)
        log["code_commit"] = os.environ.get("GITHUB_SHA")
        write_json(log, f"runs/{RUN_ID}.json")
        print(json.dumps(log, indent=2, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["smoke", "screen", "daily"])
    args = ap.parse_args(argv)
    cfg = load_cfg()
    if args.mode == "smoke":
        rep = smoke(cfg)
        sys.exit(0 if rep["checks"].get("reviews_api", {}).get("ok") else 1)
    daily(cfg, force_screen=(args.mode == "screen"))


if __name__ == "__main__":
    main()
