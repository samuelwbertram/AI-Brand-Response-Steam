"""Store metadata and the AI Generated Content Disclosure check."""
import re

APPDETAILS = "https://store.steampowered.com/api/appdetails"
STORE_PAGE = "https://store.steampowered.com/app/{appid}/"
AI_HEADING = re.compile(r"AI Generated Content Disclosure", re.I)


def app_details(client, appid):
    r = client.get(APPDETAILS, params={"appids": appid, "l": "english", "cc": "au"})
    r.raise_for_status()
    entry = (r.json() or {}).get(str(appid), {})
    if not entry.get("success"):
        return None
    d = entry.get("data", {})
    return {
        "appid": int(appid),
        "name": d.get("name"),
        "type": d.get("type"),
        "release_date": (d.get("release_date") or {}).get("date"),
        "is_free": d.get("is_free"),
        "price_final_aud": ((d.get("price_overview") or {}).get("final") or 0) / 100,
        "genres": ";".join(g.get("description", "") for g in d.get("genres", []) or []),
        "developers": ";".join(d.get("developers", []) or []),
        "publishers": ";".join(d.get("publishers", []) or []),
        # Some store payloads carry content descriptor ids; kept raw for checking.
        "content_descriptor_ids": ";".join(
            str(i) for i in ((d.get("content_descriptors") or {}).get("ids") or [])),
    }


def ai_disclosure(client, appid):
    """Return (disclosed, disclosure_text) from the public store page."""
    r = client.get(STORE_PAGE.format(appid=appid), params={"l": "english"})
    if r.status_code != 200:
        return None, None
    html = r.text
    m = AI_HEADING.search(html)
    if not m:
        return False, None
    # Grab a short window of text after the heading for the record.
    window = re.sub(r"<[^>]+>", " ", html[m.end(): m.end() + 3000])
    window = re.sub(r"\s+", " ", window).strip()
    return True, window[:1000]
