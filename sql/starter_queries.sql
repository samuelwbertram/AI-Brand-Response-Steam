-- AI Brand Response – Steam data: starter queries (DuckDB)
-- Run setup.sql first. Then run one query at a time.
-- Each query says what question it answers. Change them, break them, make your own.


-- Q1. How big is the dataset?
SELECT (SELECT count(*) FROM games WHERE in_panel)          AS panel_games,
       (SELECT count(*) FROM reviews)                       AS reviews,
       (SELECT count(*) FROM reviews WHERE has_dev_reply)   AS reviews_with_dev_reply,
       (SELECT count(*) FROM comments)                      AS comments,
       (SELECT count(*) FROM comments WHERE any_candidate)  AS flagged_comments;


-- Q2. Which games reply to reviews most often?  (JOIN + GROUP BY)
SELECT g.game, g.stratum,
       count(*)                                     AS reviews,
       sum(r.has_dev_reply::INT)                    AS dev_replies,
       round(100 * avg(r.has_dev_reply::INT), 1)    AS reply_rate_pct
FROM reviews r
JOIN games g USING (appid)
GROUP BY ALL
HAVING count(*) >= 100
ORDER BY reply_rate_pct DESC
LIMIT 20;


-- Q3. Do developers reply more to negative or positive reviews?  (CASE)
SELECT CASE WHEN voted_up THEN 'positive' ELSE 'negative' END  AS review_type,
       count(*)                                                AS reviews,
       round(100 * avg(has_dev_reply::INT), 2)                 AS reply_rate_pct
FROM reviews
GROUP BY ALL;


-- Q4. How fast do developers reply?  (dates and medians)
SELECT g.game,
       count(*)                                                        AS replies,
       round(median(date_diff('hour', r.created_at, r.dev_replied_at)), 1) AS median_hours,
       round(100 * avg((date_diff('hour', r.created_at, r.dev_replied_at) <= 48)::INT), 1)
                                                                       AS pct_within_2_days
FROM reviews r JOIN games g USING (appid)
WHERE r.has_dev_reply
GROUP BY ALL
HAVING count(*) >= 20
ORDER BY median_hours;


-- Q5. Which kinds of call-out does the regex net catch?  (UNPIVOT)
UNPIVOT (SELECT sum(cand_ai_written::INT)    AS ai_written,
                sum(cand_bot::INT)           AS bot,
                sum(cand_paid_shill::INT)    AS paid_shill,
                sum(cand_fake::INT)          AS fake,
                sum(cand_no_experience::INT) AS no_experience
         FROM comments WHERE NOT excluded_hit)
ON COLUMNS(*) INTO NAME callout_type VALUE flagged_comments
ORDER BY flagged_comments DESC;


-- Q6. The core design: call-outs under reviews with and without a developer reply
--     (praise vs critique)  (JOIN on two keys, conditional counts)
SELECT CASE WHEN r.voted_up THEN 'praise' ELSE 'critique' END            AS review_type,
       r.has_dev_reply,
       count(DISTINCT r.recommendationid)                                 AS reviews_with_comments,
       count(DISTINCT r.recommendationid) FILTER (WHERE c.any_candidate)  AS reviews_called_out,
       round(100 * count(DISTINCT r.recommendationid) FILTER (WHERE c.any_candidate)
             / count(DISTINCT r.recommendationid), 2)                     AS pct_called_out
FROM reviews r
JOIN comments c USING (appid, recommendationid)
GROUP BY ALL
ORDER BY review_type, has_dev_reply;


-- Q7. Timing: did the developer reply before or after the call-out?  (CTE)
WITH first_callout AS (
    SELECT appid, recommendationid, min(commented_at) AS first_callout_at
    FROM comments
    WHERE any_candidate AND NOT excluded_hit
    GROUP BY ALL
)
SELECT CASE WHEN NOT r.has_dev_reply                     THEN 'no developer reply'
            WHEN r.dev_replied_at <  f.first_callout_at  THEN 'reply before call-out'
            ELSE 'reply after call-out' END              AS sequence,
       count(*)                                          AS called_out_reviews
FROM first_callout f
JOIN reviews r USING (appid, recommendationid)
GROUP BY ALL
ORDER BY called_out_reviews DESC;


-- Q8. Read the flagged comments next to the review and the developer's reply
--     (change the game name or remove the WHERE line to see everything)
SELECT g.game,
       CASE WHEN r.voted_up THEN 'praise' ELSE 'critique' END AS review_type,
       left(r.review, 200)              AS review,
       left(c.comment_text, 300)        AS comment,
       left(r.developer_response, 200)  AS developer_reply,
       c.commented_at::DATE             AS comment_date
FROM comments c
JOIN reviews r USING (appid, recommendationid)
JOIN games g   USING (appid)
WHERE c.any_candidate AND NOT c.excluded_hit
  -- AND g.game ILIKE '%civilization%'
ORDER BY c.commented_at DESC
LIMIT 50;


-- Q9. Do games that disclose AI use get more AI call-outs?  (the AI-disclosure moderator)
SELECT g.ai_disclosed,
       count(DISTINCT g.appid)                                           AS games,
       count(*)                                                          AS comments,
       round(1000 * avg((c.any_candidate AND NOT c.excluded_hit)::INT), 1) AS flagged_per_1000,
       round(1000 * avg(c.cand_ai_written::INT), 1)                      AS ai_written_per_1000
FROM comments c JOIN games g USING (appid)
GROUP BY ALL;


-- Q10. How much did each day's run collect?  (good for checking the collector)
SELECT date, count(*) AS review_versions, count(DISTINCT appid) AS games
FROM review_versions
GROUP BY ALL
ORDER BY date DESC;


-- Q12. Only the AI call-outs: comments saying something is AI-written or a bot, split by what
--      they seem to target. Target is a keyword guess: words like dev / developer / response /
--      reply point at the developer's reply; everything else points at the review or another
--      comment. Steam comments are flat (no reply threads), so "another comment" and "the review"
--      can't be told apart by structure - read the comment to judge.
WITH ai_callouts AS (
    SELECT c.*,
           CASE WHEN regexp_matches(lower(c.comment_text),
                     '\b(dev|devs|developers?|response|responses|reply|replies|replied|responded|support|pr team|community manager|cm)\b')
                THEN 'developer reply'
                ELSE 'review or another comment' END AS likely_target
    FROM comments c
    WHERE (c.cand_ai_written OR c.cand_bot) AND NOT c.excluded_hit
)
SELECT a.likely_target,
       g.game,
       CASE WHEN r.voted_up THEN 'praise' ELSE 'critique' END AS review_type,
       a.comment_text                   AS callout_comment,
       r.review                         AS review,
       r.developer_response             AS developer_reply,
       a.commented_at::DATE             AS comment_date,
       r.dev_replied_at < a.commented_at AS dev_replied_before_callout,
       a.cand_ai_written                AS says_ai_written,
       a.cand_bot                       AS says_bot
FROM ai_callouts a
JOIN reviews r USING (appid, recommendationid)
JOIN games g   USING (appid)
ORDER BY a.likely_target DESC, a.commented_at DESC;


-- Q11. Save any result as a CSV you can open in Google Sheets (example: Q8's comments)
-- COPY (SELECT ... ) TO 'G:/My Drive/steam_reviews/exports/flagged_comments.csv' (HEADER);
