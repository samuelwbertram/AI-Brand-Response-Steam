"""Official reviews endpoint: IUserReviewsService/GetAppReviews (keyless)."""
import hashlib
import json

ENDPOINT = "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/"
FILTER_RECENT = 1  # 0 helpful, 1 recent, 2 updated, 3 funny


def hash_id(value):
    """One-way hash for author IDs in analysis tables."""
    if value in (None, ""):
        return None
    return hashlib.sha256(f"steam:{value}".encode()).hexdigest()[:16]


def fetch_page(client, appid, cursor="*", page_size=100, languages=("all",),
               date_start=None, date_end=None):
    body = {
        "appid": int(appid),
        "cursor": cursor,
        "num_per_page": page_size,
        "filter": FILTER_RECENT,
        "languages": list(languages),
    }
    if date_start is not None and date_end is not None:
        body["date_range_start"] = int(date_start)
        body["date_range_end"] = int(date_end)
    r = client.get(ENDPOINT, params={"input_json": json.dumps(body)})
    r.raise_for_status()
    data = r.json()
    # Service responses wrap the payload in "response".
    payload = data.get("response", data)
    return payload


def flatten_review(appid, raw):
    """Turn one raw review into a flat row. Unknown fields stay in raw_json."""
    author = raw.get("author", {}) or {}
    steamid = author.get("steamid")
    return {
        "appid": int(appid),
        "recommendationid": str(raw.get("recommendationid")),
        "author_hash": hash_id(steamid),
        "author_steamid": str(steamid) if steamid else None,  # private; needed for thread URLs
        "author_num_reviews": author.get("num_reviews"),
        "playtime_forever": author.get("playtime_forever"),
        "playtime_at_review": author.get("playtime_at_review"),
        "last_played": author.get("last_played"),
        "language": raw.get("language"),
        "review": raw.get("review"),
        "timestamp_created": raw.get("timestamp_created"),
        "timestamp_updated": raw.get("timestamp_updated"),
        "voted_up": raw.get("voted_up"),
        "votes_up": raw.get("votes_up"),
        "votes_funny": raw.get("votes_funny"),
        "weighted_vote_score": _num(raw.get("weighted_vote_score")),
        "comment_count": raw.get("comment_count"),
        "steam_purchase": raw.get("steam_purchase"),
        "received_for_free": raw.get("received_for_free"),
        "refunded": raw.get("refunded"),
        "written_during_early_access": raw.get("written_during_early_access"),
        "primarily_steam_deck": raw.get("primarily_steam_deck"),
        "developer_response": raw.get("developer_response") or None,
        "timestamp_dev_responded": raw.get("timestamp_dev_responded") or None,
        "raw_json": json.dumps(raw, ensure_ascii=False),
    }


def content_hash(row):
    """Changes when anything we track as a 'version' of the review changes."""
    key = "|".join(str(row.get(k)) for k in (
        "review", "voted_up", "timestamp_updated", "developer_response",
        "timestamp_dev_responded"))
    return hashlib.sha1(key.encode()).hexdigest()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
