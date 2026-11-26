# AI Brand Response – Steam collector

Daily, free collector of Steam reviews, official developer replies, and the comment threads
under reviews. It's the brand-reply arm of the AI Brand Response project: reviews that praise
or criticise a game, other users calling those reviews out ("this is AI / fake / a shill"),
and whether and how the developer then replies.

Companion to [AI-Brand-Response-YouTube](https://github.com/samuelwbertram/AI-Brand-Response-YouTube).
Same design principles, separate code and separate Drive folder.

## What it collects

| Source | How | Gives |
|---|---|---|
| Reviews + developer replies | Official `IUserReviewsService/GetAppReviews` (keyless) | review text, voted up/down, votes, comment count, playtime, free key, refunded, developer reply text, all to-the-second timestamps |
| Comment threads under reviews | Public Steam Community pages (HTML) | each comment's text, hashed author, timestamp |
| Store page | Public store page + `appdetails` | AI Generated Content Disclosure flag and text, genre, price, developer |

## Daily run (GitHub Actions, 04:23 Melbourne)

0. **Discover** candidate games (monthly) from Steam's store search, popular games first,
   keeping games with 500+ reviews up to `discovery.target_candidates` in `config.yaml`
   (2,000 to start; raise it to grow the panel).
1. **Screen** candidate games (up to 150 minutes a run, continuing on later runs): reply rate from the 500 most recent reviews, AI disclosure.
   Panel = games with reply rate ≥ 1%, plus a random 10% of the rest for base rates.
2. **Reviews:** every panel game's last 14 days, every day; a 90-day re-sweep once a week per
   game (catches late developer replies, edits, deletions); then the backfill to Jan 2022.
3. **Threads**, in priority order: (1) reviews with a developer reply, (2) a random 10% of
   commented reviews without a reply, (3) the rest if time allows. Threads are re-checked weekly.
4. **Call-out candidates:** a regex net marks possible call-outs in each new comment
   (measurement plan Stage 2). LLM coding and human checks run later, off this collector.
5. **Call-out review sheet:** a Google Sheet in `callout_review/` with up to 40 flagged comments
   (spread across call-out types) and 10 random unflagged ones, each with the review it sits under.
   About 75% are call-outs that seem aimed at another review or comment (the paper's focus);
   the rest cover developer-reply call-outs and other flags. Fill in `is_callout`, `true_target`,
   `callout_type` and `notes`; the answers are used to tune `detect.py`.

Each flagged comment gets a `callout_target` guess: `review_or_comment` (focus),
`developer_reply`, `game` (the game itself is AI-made, not a call-out) or `unclear`.
Comment threads rotate one game at a time and mix in ~30% no-reply reviews, and get ~70%
of the time left after screening.

6. **Overview sheet:** a Google Sheet in `overview/` with one row per panel game (reviews,
   developer reply rate, comments, flagged call-outs, AI disclosure), covering all data so far.

A backup run at 16:23 Melbourne starts only if no run went in the previous 13 hours
(GitHub sometimes drops scheduled runs).

Each review is stored only when it is new or changed, so the data is a version history.
Votes are stored daily for reviews with comments or a developer reply.

## Politeness

1 API call a second; 1 Community page every 3 seconds; it stops threads for the day on the first
429; a single runner and never rotated IPs; it identifies itself in the User-Agent. Author
IDs are hashed in every analysis table.

## Data layout (Drive: `steam_reviews/`)

```
state/          games panel, latest version per review, progress (the collector's memory)
screening/      each screening pass
reviews/date=/  new or changed review versions
metrics/date=/  daily votes for focus reviews
disappeared/    reviews that vanished in a complete re-sweep
comments/date=/ new comments + call-out candidate flags
runs/           one JSON log per run (calls, counts, errors)
callout_review/ daily call-out review sheets (Google Sheets) for hand checks
overview/       daily one-row-per-game overview (Google Sheets)
smoke/          smoke-test reports
```

Raw Steam IDs exist only in `state/latest/` on Drive (needed to fetch threads), never in this repo.

## Querying the data with SQL

See [sql/README.md](sql/README.md): DuckDB reads the Drive files directly, with ready-made
views and starter queries.

## Tests

`PYTHONPATH=. python tests/test_offline.py` fakes every Steam response and checks a full run
offline, including the call-out sheet.

## Setup

See [TUTORIAL.md](TUTORIAL.md).
