"""Run the SQL starter queries on the collected data and save each result as a CSV.

Used by the report workflow (python -m collector.report). Expects the Drive folders
(state, reviews, comments, metrics) copied into ./data. Writes
reports/<Melbourne date>/Q01 ... .csv, which the workflow uploads to Drive as Google Sheets.
"""
import datetime as dt
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from . import detect
from .run import DATA, ROOT

LABEL = dt.datetime.now(ZoneInfo("Australia/Melbourne")).strftime("%Y-%m-%d %H%M")


def blocks(sql_text):
    """Split starter_queries.sql into (title, sql) pairs; skip comment-only blocks."""
    out = []
    for b in re.split(r"\n(?=-- Q\d+\.)", sql_text)[1:]:
        lines = b.splitlines()
        title = lines[0].lstrip("- ").strip()
        body = "\n".join(l for l in lines if not l.strip().startswith("--")).strip()
        if body:
            out.append((title, body))
    return out


def safe_name(title):
    num, _, rest = title.partition(".")
    rest = re.sub(r"\(.*?\)", "", rest)
    rest = re.sub(r"[^A-Za-z0-9 ,'-]+", " ", rest).strip()
    return f"Q{int(num[1:]):02d} {' '.join(rest.split())[:70]}"


def main():
    con = duckdb.connect()
    setup = (ROOT / "sql" / "setup.sql").read_text().replace(
        "G:/My Drive/steam_reviews", str(DATA).replace("\\", "/"))
    con.execute(setup)
    # apply today's call-out rules (and target) to every stored comment, old ones included
    com = detect.retag(con.execute("SELECT * FROM comments").fetchdf())
    con.register("comments_retagged", com)
    con.execute("CREATE OR REPLACE VIEW comments AS SELECT * FROM comments_retagged")
    outdir = DATA / "reports" / LABEL
    outdir.mkdir(parents=True, exist_ok=True)
    summary = []
    for title, sql in blocks((ROOT / "sql" / "starter_queries.sql").read_text()):
        name = safe_name(title)
        try:
            df = con.execute(sql).fetchdf()
            df.to_csv(outdir / f"{name}.csv", index=False)
            summary.append({"query": name, "rows": len(df), "error": ""})
            print(f"{name}: {len(df)} rows")
            print(df.head(15).to_string(max_colwidth=60))
        except Exception as e:
            summary.append({"query": name, "rows": 0, "error": str(e)[:300]})
            print(f"{name}: ERROR {e}")
    (outdir / "Q00 Index of results.csv").write_text(
        "query,rows,error\n" + "".join(
            f"\"{s['query']}\",{s['rows']},\"{s['error'].replace(chr(34), chr(39))}\"\n"
            for s in summary))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
