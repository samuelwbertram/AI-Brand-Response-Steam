# Querying the Steam data with SQL (DuckDB)

DuckDB is a free SQL engine that reads the collector's `.parquet` files straight from
Google Drive. You don't need a database server, imports or copies.

## One-time setup (Windows, about 5 minutes)

1. **Make the data local.** In File Explorer, go to `G:\My Drive`, right-click
   `steam_reviews` → *Offline access* → *Available offline*. Queries are much faster
   when Drive isn't streaming each file.
2. **Install DuckDB.** Open PowerShell and run:
   ```
   winget install DuckDB.cli
   ```
   Close and reopen PowerShell afterwards.
3. **Open the notebook UI.**
   ```
   duckdb -ui
   ```
   This opens DuckDB's notebook in your browser. It runs on your PC; the data stays local.

## Each session

1. In a notebook cell, paste all of [`setup.sql`](setup.sql) and run it. It ends with a
   `ready` row showing how many games, reviews and comments it found. If your Drive
   letter isn't `G:`, change the path on the `SET VARIABLE root` line first.
2. Paste a query from [`starter_queries.sql`](starter_queries.sql) into a new cell and run it.

## Tables (views) you get

| View | One row per | Use it for |
|---|---|---|
| `games` | screened game | panel, reply rate at screening, AI disclosure, genre, developer |
| `reviews` | review (latest version) | review text, praise/critique, developer reply and when it came |
| `review_versions` | version of a review | how reviews and replies changed over time |
| `comments` | comment under a review | comment text, timestamp, call-out flags (`cand_*`, `any_candidate`) |
| `daily_votes` | review per day | vote counts over time for reviews with comments or a reply |

Join `comments` to `reviews` on `(appid, recommendationid)`; join anything to `games` on `appid`.

## Starter queries

| # | Question | SQL skills |
|---|---|---|
| Q1 | How big is the dataset? | subqueries |
| Q2 | Which games reply to reviews most? | JOIN, GROUP BY, HAVING |
| Q3 | Do developers reply more to negative reviews? | CASE |
| Q4 | How fast do developers reply? | date maths, median |
| Q5 | Which call-out types does the regex catch? | UNPIVOT |
| Q6 | Call-outs under reviews with vs without a developer reply | two-key JOIN, FILTER |
| Q7 | Did the developer reply before or after the call-out? | CTE |
| Q8 | Read flagged comments next to the review and reply | multi-table JOIN |
| Q9 | Do AI-disclosed games get more AI call-outs? | moderator comparison |
| Q10 | How much did each run collect? | collector check |
| Q11 | Export a result to CSV for Google Sheets | COPY |

**Charts:** the DuckDB notebook shows a quick summary of each result column. For a proper
chart, export the result with Q11 and chart it in Google Sheets.

**Note:** the call-out flags are a keyword net (Stage 2 of the measurement plan) and need
checking by hand. Treat counts of flagged comments as candidate call-outs until the hand checks
and LLM coding are done.
