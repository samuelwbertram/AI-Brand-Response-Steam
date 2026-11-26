"""Comment threads under reviews on steamcommunity.com.

These are HTML pages, not an official API. Two methods are tried:
  A. the comment 'render' endpoint the page itself calls (JSON with comment HTML, paged)
  B. the public review page (first page of comments only)
The smoke test reports which one works; the collector uses the first that does.
"""
import hashlib
import re

from bs4 import BeautifulSoup

from .steam_api import hash_id

RENDER = "https://steamcommunity.com/comment/Recommendation/render/{steamid}/{recid}/"
REVIEW_PAGE = "https://steamcommunity.com/profiles/{steamid}/recommended/{appid}/"


def parse_comments(html, appid, recid):
    soup = BeautifulSoup(html or "", "html.parser")
    rows = []
    for c in soup.select("div.commentthread_comment"):
        cid = (c.get("id") or "").replace("comment_", "") or None
        author = c.select_one("a.commentthread_author_link")
        text_el = c.select_one("div.commentthread_comment_text")
        ts_el = c.select_one("span.commentthread_comment_timestamp")
        badges = [b.get_text(" ", strip=True) for b in c.select(
            "[class*=badge], [class*=developer], [class*=official]")]
        account_id = author.get("data-miniprofile") if author else None
        ts = None
        if ts_el is not None and ts_el.get("data-timestamp"):
            try:
                ts = int(ts_el["data-timestamp"])
            except ValueError:
                ts = None
        text = text_el.get_text("\n", strip=True) if text_el else None
        rows.append({
            "appid": int(appid),
            "recommendationid": str(recid),
            "comment_id": cid or hashlib.sha1(f"{recid}|{text}".encode()).hexdigest()[:16],
            "author_hash": hash_id(account_id),
            "author_classes": " ".join(c.get("class", [])),
            "author_badges": ";".join(b for b in badges if b) or None,
            "comment_text": text,
            "timestamp": ts,
            "timestamp_display": ts_el.get_text(strip=True) if ts_el else None,
            "timestamp_title": ts_el.get("title") if ts_el else None,
        })
    return rows


def fetch_thread_render(client, appid, steamid, recid, per_call=100):
    """Method A. Returns (rows, total_count) or raises ValueError if unusable."""
    rows, start, total = [], 0, None
    while True:
        r = client.post(RENDER.format(steamid=steamid, recid=recid),
                        data={"start": start, "count": per_call})
        if r.status_code != 200:
            raise ValueError(f"render HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise ValueError("render not JSON") from e
        if not data.get("success"):
            raise ValueError("render success=false")
        total = int(data.get("total_count") or 0)
        page = parse_comments(data.get("comments_html", ""), appid, recid)
        rows.extend(page)
        start += per_call
        if not page or start >= total:
            return rows, total


def fetch_thread_page(client, appid, steamid, recid):
    """Method B. First page of comments from the public review page."""
    r = client.get(REVIEW_PAGE.format(steamid=steamid, appid=appid), params={"l": "english"})
    if r.status_code != 200:
        raise ValueError(f"page HTTP {r.status_code}")
    rows = parse_comments(r.text, appid, recid)
    m = re.search(r'"total_count"\s*:\s*(\d+)', r.text)
    return rows, int(m.group(1)) if m else len(rows)


def fetch_thread(client, appid, steamid, recid, per_call=100, method=None):
    """Returns (rows, total, method_used). method=None tries A then B."""
    if method in (None, "render"):
        try:
            rows, total = fetch_thread_render(client, appid, steamid, recid, per_call)
            return rows, total, "render"
        except ValueError:
            if method == "render":
                raise
    rows, total = fetch_thread_page(client, appid, steamid, recid)
    return rows, total, "page"
