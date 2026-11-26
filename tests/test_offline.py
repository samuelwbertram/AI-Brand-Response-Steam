"""Offline test: fakes every Steam response and runs smoke + two daily runs.

Run with:  python -m tests.test_offline
"""
import json
import os
import shutil
import tempfile
import time
from unittest import mock
from urllib.parse import parse_qs, urlparse

TMP = tempfile.mkdtemp()
os.environ["DATA_DIR"] = TMP

import requests  # noqa: E402

from collector import run  # noqa: E402

NOW = int(time.time())


def make_reviews(appid, n=250):
    out = []
    for i in range(n):
        r = {
            "recommendationid": f"{appid}{i:05d}",
            "author": {"steamid": f"7656119{i:010d}", "playtime_at_review": i * 3,
                       "num_reviews": 2},
            "language": "english",
            "review": "Great game, loved it" if i % 2 else "Refunded, enemy AI is dumb",
            "timestamp_created": NOW - i * 3600, "timestamp_updated": NOW - i * 3600,
            "voted_up": bool(i % 2), "votes_up": i, "votes_funny": 0,
            "weighted_vote_score": "0.5", "comment_count": 2 if i % 10 == 0 else 0,
            "received_for_free": False, "refunded": False,
        }
        if i % 20 == 0:
            r["developer_response"] = "Thanks so much for playing!"
            r["timestamp_dev_responded"] = NOW - i * 3600 + 600
        out.append(r)
    return out


REVIEWS = {a: make_reviews(a) for a in (1295660, 2073850, 1285190)}
COMMENT_HTML = """
<div class="commentthread_comment" id="comment_111{n}">
  <a class="commentthread_author_link" data-miniprofile="555{n}"><bdi>someone</bdi></a>
  <div class="commentthread_comment_timestamp"></div>
  <div class="commentthread_comment_timestamp" title="26 November 2026 @ 9:15pm"
        data-timestamp="{ts}">26 Nov @ 9:15pm&nbsp;</div>
  <div class="commentthread_comment_text">this review is clearly written by ChatGPT lol</div>
</div>
<div class="commentthread_comment" id="comment_222{n}">
  <a class="commentthread_author_link" data-miniprofile="666{n}"><bdi>other</bdi></a>
  <div class="commentthread_comment_timestamp" data-timestamp="{ts}">26 Nov</div>
  <div class="commentthread_comment_text">agreed, enemy AI is broken though</div>
</div>"""


class Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self._p, self.text = status, payload, text

    def json(self):
        if self._p is None:
            raise ValueError("no json")
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(self.status_code)


def fake_request(self, method, url, params=None, data=None, **kw):
    u = urlparse(url)
    if "GetAppReviews" in url:
        body = json.loads(params["input_json"])
        revs = REVIEWS.get(body["appid"], [])
        if "date_range_start" in body:
            revs = [r for r in revs if body["date_range_start"] <= r["timestamp_created"]
                    <= body["date_range_end"]]
        start = 0 if body["cursor"] == "*" else int(body["cursor"])
        page = revs[start:start + body["num_per_page"]]
        nxt = str(start + len(page)) if page else body["cursor"]
        return Resp(payload={"response": {"reviews": page, "cursor": nxt,
                                          "total_matching": len(revs)}})
    if "search/results" in url:
        if params["start"] > 0:
            return Resp(payload={"results_html": ""})
        return Resp(payload={"results_html": """
          <a class="search_result_row" data-ds-appid="1285190"><span class="title">Borderlands 4</span>
            <span class="search_review_summary" data-tooltip-html="Mixed<br>60% of the 90,000 user reviews for this game are positive."></span></a>
          <a class="search_result_row" data-ds-appid="999"><span class="title">Found Game</span>
            <div class="search_released">1 Jan, 2025</div>
            <span class="search_review_summary" data-tooltip-html="Very Positive<br>88% of the 30,979 user reviews for this game are positive."></span></a>
          <a class="search_result_row" data-ds-appid="998"><span class="title">Tiny</span>
            <span class="search_review_summary" data-tooltip-html="Positive<br>92% of the 14 user reviews for this game are positive."></span></a>"""})
    if "appdetails" in url:
        a = params["appids"]
        return Resp(payload={str(a): {"success": True, "data": {
            "name": {1295660: "Sid Meier's Civilization VII", 2073850: "THE FINALS",
                     1285190: "Borderlands 4"}.get(int(a), "x"),
            "type": "game", "genres": [{"description": "Strategy"}]}}})
    if "store.steampowered.com/app/" in url:
        txt = "<h2>AI Generated Content Disclosure</h2><p>We use AI for voices</p>" \
            if "2073850" in url else "<html>no disclosure</html>"
        return Resp(text=txt)
    if "comment/Recommendation/render" in url:
        rid = u.path.rstrip("/").split("/")[-2] + u.path.rstrip("/").split("/")[-1]
        return Resp(payload={"success": True, "total_count": 2,
                             "comments_html": COMMENT_HTML.format(n=rid, ts=NOW - 100)})
    if "/recommended/" in url:
        return Resp(text=COMMENT_HTML.format(n="p", ts=NOW))
    return Resp(404)


def main():
    import pandas as pd
    with mock.patch.object(requests.Session, "request", fake_request), \
            mock.patch("time.sleep", lambda s: None):
        for c in ("api", "community"):
            run.load_cfg  # noqa
        cfg = run.load_cfg()
        cfg["api"]["seconds_between_calls"] = 0
        cfg["community"]["seconds_between_calls"] = 0
        cfg["screening"]["min_total_reviews"] = 100
        cand = pd.DataFrame([
            {"appid": 1295660, "expected_name": "Sid Meier's Civilization VII", "source": "t", "note": ""},
            {"appid": 2073850, "expected_name": "THE FINALS", "source": "t", "note": ""},
            {"appid": 1285190, "expected_name": "Borderlands 4", "source": "t", "note": ""}])
        with mock.patch("pandas.read_csv", lambda *a, **k: cand):
            rep = run.smoke(cfg)
            assert rep["checks"]["reviews_api"]["ok"], rep
            assert rep["checks"]["comment_threads"]["render"]["share_with_unix_timestamp"] == 1.0
            assert rep["checks"]["store_and_ai_flag"]["ai_disclosed"] is True
            run.daily(cfg)
            run.daily(cfg)  # second run: no duplicate versions or comments
    games = pd.read_parquet(f"{TMP}/state/games.parquet")
    print(games[["appid", "name_matches", "ai_disclosed", "screen_reply_rate", "in_panel"]])
    vers = pd.concat(pd.read_parquet(p) for p in __import__("glob").glob(f"{TMP}/reviews/**/*.parquet", recursive=True))
    com = pd.concat(pd.read_parquet(p) for p in __import__("glob").glob(f"{TMP}/comments/**/*.parquet", recursive=True))
    assert set(games.appid) == {1295660, 2073850, 1285190, 999}, set(games.appid)
    print("review versions:", len(vers), "unique:", vers.recommendationid.nunique())
    print("comments:", len(com), "unique:", com.comment_id.nunique(),
          "candidates:", int(com.any_candidate.sum()), "excluded hits:", int(com.excluded_hit.sum()))
    assert len(vers) == vers.recommendationid.nunique() == 750
    assert len(com) == com.comment_id.nunique()
    assert "author_steamid" not in vers.columns
    assert com.timestamp.notna().all()
    # call-out sample sheet (no Drive in tests)
    os.environ["NO_DRIVE"] = "1"
    from collector import sample
    sample.main()
    sheet = pd.read_csv(__import__("glob").glob(f"{TMP}/callout_review/*.csv")[0])
    print(sheet[["game", "review_says", "net", "regex_flags"]].head(8))
    assert 0 < len(sheet) <= 50 and set(sheet.net) <= {"flagged", "random check"}
    assert sheet.review_link.str.startswith("https://steamcommunity.com/profiles/").all()
    assert (sheet.review_text.fillna("") != "").all()
    sample.main()   # second pass must not repeat comments
    again = pd.read_parquet(f"{TMP}/state/callout_sampled.parquet")
    assert again.comment_id.is_unique
    # overview sheet
    from collector import overview
    overview.main()
    ov = pd.read_csv(__import__("glob").glob(f"{TMP}/overview/*.csv")[0])
    print(ov[["game", "panel_group", "reviews_collected", "dev_reply_rate",
              "comments_collected", "flagged_callouts"]])
    assert ov.comments_collected.sum() == len(com)
    overview.main()   # same files again must not double-count
    ov2 = pd.read_csv(__import__("glob").glob(f"{TMP}/overview/*.csv")[0])
    assert ov2.comments_collected.sum() == len(com)
    print("ALL OFFLINE CHECKS PASSED")
    shutil.rmtree(TMP)


if __name__ == "__main__":
    main()
