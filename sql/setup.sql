-- AI Brand Response – Steam data: SQL setup (DuckDB)
-- Run this once per session. It makes "views": saved queries that act like tables,
-- reading the .parquet files straight from Drive. Nothing is copied or changed.
--
-- 1. Change the path below if your Drive letter or folder differs.
--    Use forward slashes, even on Windows.
SET VARIABLE root = 'G:/My Drive/steam_reviews';

-- games: every screened game. in_panel = TRUE for the games the collector follows.
--   stratum: 'responsive' (developer replies to >= 1% of reviews) or 'base_rate' (random 10%)
CREATE OR REPLACE VIEW games AS
SELECT appid, name AS game, developers, publishers, genres, release_date, is_free,
       price_final_aud, ai_disclosed, in_panel, stratum,
       screen_reply_rate, total_reviews AS reviews_on_steam, screened_date
FROM read_parquet(getvariable('root') || '/state/games.parquet');

-- review_versions: every version of every review the collector has stored
--   (a review gets a new row when its text, vote or developer reply changes)
CREATE OR REPLACE VIEW review_versions AS
SELECT * EXCLUDE (raw_json, author_hash),
       to_timestamp(timestamp_created)       AS created_at,
       to_timestamp(timestamp_dev_responded) AS dev_replied_at
FROM read_parquet(getvariable('root') || '/reviews/date=*/*.parquet',
                  hive_partitioning = true, union_by_name = true);

-- reviews: the latest version of each review (use this one most of the time)
CREATE OR REPLACE VIEW reviews AS
SELECT *, developer_response IS NOT NULL AS has_dev_reply
FROM review_versions
QUALIFY row_number() OVER (PARTITION BY recommendationid
                           ORDER BY snapshot_date DESC, timestamp_updated DESC) = 1;

-- comments: comments under reviews, with the regex call-out flags (cand_*)
--   any_candidate = the net flagged it; excluded_hit = it also used game-AI / cheating words
CREATE OR REPLACE VIEW comments AS
SELECT * EXCLUDE (author_hash),
       to_timestamp(timestamp) AS commented_at
FROM read_parquet(getvariable('root') || '/comments/date=*/*.parquet',
                  hive_partitioning = true, union_by_name = true);

-- daily_votes: one row per review per day, for reviews with comments or a developer reply
CREATE OR REPLACE VIEW daily_votes AS
SELECT *
FROM read_parquet(getvariable('root') || '/metrics/date=*/*.parquet',
                  hive_partitioning = true, union_by_name = true);

-- callout_checks: your hand checks, once you've filled in a sheet and downloaded it
--   as CSV into steam_reviews/callout_review_checked/ (File > Download > .csv)
-- CREATE OR REPLACE VIEW callout_checks AS
-- SELECT * FROM read_csv(getvariable('root') || '/callout_review_checked/*.csv');

SELECT 'ready' AS status,
       (SELECT count(*) FROM games WHERE in_panel) AS panel_games,
       (SELECT count(*) FROM reviews)              AS reviews,
       (SELECT count(*) FROM comments)             AS comments;
