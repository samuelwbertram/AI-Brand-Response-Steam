"""Find candidate games from Steam's public store search (popular games first).

Each call returns 100 games with their appid, name, release date and review count.
We keep games with enough reviews, up to config discovery.target_candidates.
Raise that number in config.yaml to grow the panel later.
"""
import re

from bs4 import BeautifulSoup

SEARCH = "https://store.steampowered.com/search/results/"
_COUNT = re.compile(r"of the ([\d,]+) user reviews")
_PCT = re.compile(r"(\d+)% of the")


def parse_results(html):
    rows = []
    soup = BeautifulSoup(html or "", "html.parser")
    for a in soup.select("a.search_result_row"):
        appid = (a.get("data-ds-appid") or "").split(",")[0]
        if not appid.isdigit():
            continue
        tip_el = a.select_one(".search_review_summary")
        tip = tip_el.get("data-tooltip-html", "") if tip_el else ""
        m, p = _COUNT.search(tip), _PCT.search(tip)
        title, rel = a.select_one(".title"), a.select_one(".search_released")
        rows.append({
            "appid": int(appid),
            "expected_name": title.get_text(strip=True) if title else None,
            "search_review_count": int(m.group(1).replace(",", "")) if m else 0,
            "search_pct_positive": int(p.group(1)) if p else None,
            "search_release": rel.get_text(strip=True) if rel else None,
        })
    return rows


def discover(client, target, min_reviews, max_pages=400, known=()):
    """Page through the store search until `target` games with enough reviews are found."""
    found, seen, dry_pages, page = [], set(known), 0, 0
    while len(found) + len(known) < target and page < max_pages and dry_pages < 15:
        r = client.get(SEARCH, params={
            "query": "", "start": page * 100, "count": 100, "category1": 998,
            "infinite": 1, "l": "english", "cc": "us"})
        page += 1
        if r.status_code != 200:
            break
        rows = parse_results(r.json().get("results_html", ""))
        if not rows:
            break
        keep = [x for x in rows if x["search_review_count"] >= min_reviews
                and x["appid"] not in seen]
        dry_pages = 0 if keep else dry_pages + 1
        for x in keep:
            seen.add(x["appid"])
            x["search_rank"] = (page - 1) * 100 + rows.index(x) + 1
            found.append(x)
    return found, page
