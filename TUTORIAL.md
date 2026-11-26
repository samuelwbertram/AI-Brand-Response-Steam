# Tutorial: running the Steam collector

Works from a phone browser. About 10 minutes.

## 1. Fork or open the repo

Co-authors: fork `samuelwbertram/AI-Brand-Response-Steam`. Sam: use the repo directly.

## 2. Add the contact variable

Repo → **Settings → Secrets and variables → Actions → Variables → New repository variable**

- Name: `CONTACT_EMAIL`
- Value: the address Valve can contact you on

## 3. Add the Drive secret

Same page → **Secrets → New repository secret**

- Name: `RCLONE_DRIVE_TOKEN`
- Value: the token JSON from `rclone authorize "drive"` (the `{"access_token":...}` line).
  You can reuse the token from the YouTube repo.

The workflow writes only inside the folder whose ID is in `config.yaml → drive.root_folder_id`
(Sam's `steam_reviews` folder). Co-authors put their own folder ID there.

**If the run says "rclone can't open the steam_reviews folder":** your token was made with
the limited `drive.file` scope, which can't see folders created outside rclone. Re-run
`rclone authorize "drive"` and choose full `drive` scope, then update the secret.

## 4. Smoke test first

**Actions → Steam reviews – daily collect → Run workflow → mode: smoke**

It makes about 30 calls and reports:
- whether the reviews API answers without a key, and which fields come back
- whether comment threads load, by which method, and whether comments carry exact timestamps
- whether the AI disclosure check works

Read it in the run log, or in `steam_reviews/smoke/` on Drive.

## 5. Go daily

Once the smoke test passes, the daily schedule takes over. You can also run
**mode: daily** by hand, or **mode: screen** after adding games to `games/candidates.csv`.

## Adding games

Add rows to `games/candidates.csv` (`appid,expected_name,source,note`). The next run screens
them. `name_matches` in the games table shows whether the appid is the game you meant.
