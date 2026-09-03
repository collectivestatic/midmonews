# Mid-Missouri Local News RSS Bridge

A combined RSS feed built from four local news sites that don't publish
their own feed:

- **newstribune.com** — Jefferson City News Tribune
- **krcgtv.com** — KRCG 13
- **kmiz.com** (abc17news.com) — ABC 17 News
- **komu.com** — KOMU 8

A GitHub Action runs once a day, scrapes each site's article listings,
pulls a clean title/summary/date from each new article's page metadata,
and publishes one combined `feed.xml` via GitHub Pages that you can point
any RSS reader at.

## How it works

- `scripts/build_feed.py` does all the work. For each site it first tries
  a couple of "hidden" feed URLs that sometimes work even when a site
  doesn't advertise RSS (WordPress's default `/feed/`, and a URL trick
  that works on some TownNews/BLOX-powered sites). If neither works for a
  given site, it falls back to reading a few listing pages, finding
  article links, and fetching each *new* article's page once to read its
  standard `<meta>` tags for a clean title/description/date.
- Everything it has ever seen is cached in `data/seen.json` so articles
  are never re-fetched or duplicated, and the feed always contains the
  most recent ~120 items across all four sources, newest first.
- `docs/feed.xml` is the file GitHub Pages serves. That's your feed URL.

## One-time setup

1. **Create a new GitHub repo** (public — GitHub Pages on the free tier
   needs a public repo unless you have GitHub Pro/Team/Enterprise) and
   push everything in this folder to it.

   ```bash
   cd midmo-rss
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git push -u origin main
   ```

2. **Enable GitHub Pages**: repo → Settings → Pages → under "Build and
   deployment", set Source to "Deploy from a branch", branch `main`,
   folder `/docs`. Save.

   After a minute or two, GitHub will show you the Pages URL, something
   like `https://<your-username>.github.io/<your-repo>/`.

3. **Update the feed's self-referencing URLs** so the XML `<link>` tags
   are correct. In `scripts/build_feed.py`, edit these two lines near the
   top:

   ```python
   FEED_SELF_LINK = "https://<your-username>.github.io/<your-repo>/feed.xml"
   FEED_HOMEPAGE_LINK = "https://<your-username>.github.io/<your-repo>/"
   ```

   Commit and push that change.

4. **Run the workflow once by hand** so you don't have to wait for the
   schedule: repo → Actions tab → "Update RSS feed" → "Run workflow".
   Give it a minute, then check that `docs/feed.xml` in the repo has real
   articles in it.

5. **Subscribe** your RSS reader to:

   ```
   https://<your-username>.github.io/<your-repo>/feed.xml
   ```

That's it — from here it updates itself once a day automatically via the
schedule in `.github/workflows/update-feed.yml`.

## Adjusting things later

- **Change how often it runs**: edit the `cron` line in
  `.github/workflows/update-feed.yml`. It's currently `0 11 * * *` (once
  daily, 11:00 UTC ≈ 6am US Central). [crontab.guru](https://crontab.guru)
  is handy for building cron expressions.
- **Add/remove sources or sections**: edit the `SOURCES` list in
  `scripts/build_feed.py`. Each source is just a name, a list of listing
  pages to scan, and a regex that matches that site's real article URLs.
- **Feed length**: `MAX_ITEMS_IN_FEED` in `build_feed.py` (default 120).
- **If a site changes its page layout** and article links stop matching,
  the Action will just find 0 new links for that source (check the
  Action's log output under the Actions tab) — the `link_pattern` regex
  for that source will need a small update to match the new URL shape.

## Testing changes locally without hitting the real sites

`scripts/test_mock.py` runs the whole pipeline against small canned HTML
snippets modeled on each site's real markup, so you can check your
changes don't break anything before pushing:

```bash
pip install -r requirements.txt
python3 scripts/test_mock.py
```

## A couple of notes

- This is a personal-use convenience tool, not an official feed from any
  of these outlets. Keep the run frequency reasonable (daily is plenty)
  and it stays a light, polite footprint — the script already checks
  each site's `robots.txt` and adds a short delay between requests to
  the same domain.
- Article pages behind a subscriber paywall (marked "Subscriber
  Exclusive" on some of these sites) will still show up in the feed with
  their headline/summary, same as they'd show up in a normal RSS feed —
  clicking through may hit a paywall depending on the site.
