#!/usr/bin/env python3
"""
build_feed.py

Builds a single combined RSS feed from local Mid-Missouri news sites that
don't publish an easily discoverable RSS feed:

    - newstribune.com   (Jefferson City News Tribune)
    - krcgtv.com        (KRCG 13)
    - kmiz.com / abc17news.com (ABC 17 News)
    - komu.com          (KOMU 8)
    - kbia.org          (KBIA 91.3 FM, NPR-member station)
    - columbiamissourian.com (Columbia Missourian)

How it works, per source:
    1. Try a couple of "hidden" native feed URLs some of these CMSes expose
       even though they're not linked anywhere (WordPress /feed/,
       TownNews/BLOX's /search/?f=rss trick). If one works, we just use it.
    2. Otherwise, fetch a few listing/section pages, pull out every link
       that matches that site's article-URL pattern, and for any link
       we haven't seen before, fetch the article page itself and read its
       standard <meta> tags (og:title, og:description, article:published_time,
       JSON-LD, etc.) to get a clean title/summary/date. Every CMS in this
       list fills these in reliably, which matters most for the sources
       that don't have even a hidden RSS trick to fall back on.

Everything we've ever seen is cached in data/seen.json so that:
    - we never re-fetch an article we already have metadata for
    - the published feed.xml always contains the most recent N items
      across all sources, newest first.

Run with: python3 scripts/build_feed.py
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
SEEN_PATH = ROOT / "data" / "seen.json"
FEED_PATH = ROOT / "docs" / "feed.xml"

USER_AGENT = (
    "Mozilla/5.0 (compatible; MidMoRSSBridge/1.0; "
    "personal, low-volume, non-commercial feed for one reader; "
    "+https://github.com/)"
)

REQUEST_TIMEOUT = 15
REQUEST_DELAY_SECONDS = 1.0  # be polite between requests to the same site
MAX_NEW_ARTICLES_PER_SOURCE = 25  # safety cap per run
MAX_ITEMS_IN_FEED = 120
PRUNE_SEEN_AFTER_DAYS = 45  # forget items older than this so seen.json doesn't grow forever

FEED_TITLE = "Mid-Missouri Local News (Combined)"
FEED_DESCRIPTION = (
    "Unofficial combined feed of Jefferson City News Tribune, KRCG 13, "
    "ABC 17 News (KMIZ), KOMU 8, KBIA, and the Columbia Missourian -- "
    "built because none of them publish an easily discoverable RSS feed."
)
# Set this to your GitHub Pages URL once you know it, e.g.
# "https://yourusername.github.io/midmo-rss/feed.xml"
FEED_SELF_LINK = "https://collectivestatic.github.io/midmonews/feed.xml"
FEED_HOMEPAGE_LINK = "https://collectivestatic.github.io/midmonews/"

SOURCES = [
    {
        "name": "Jefferson City News Tribune",
        "listing_pages": [
            "https://www.newstribune.com/",
            "https://www.newstribune.com/news/jefferson-city/",
            "https://www.newstribune.com/news/missouri/",
        ],
        "link_pattern": re.compile(
            r"^https://(?:www\.)?newstribune\.com/news/\d{4}/[a-z]{3}/\d{2}/[a-z0-9-]+/?$"
        ),
        "exclude_pattern": None,
        "native_feed_urls": [
            "https://www.newstribune.com/search/?f=rss&t=article&l=30&s=start_time&sd=desc",
        ],
    },
    {
        "name": "KRCG 13",
        "listing_pages": [
            "https://krcgtv.com/",
            "https://krcgtv.com/news/local",
        ],
        "link_pattern": re.compile(
            r"^https://(?:www\.)?krcgtv\.com/(?:news|sports|money|features)/"
            r"[a-z0-9-]+/[a-z0-9-]+/?$"
        ),
        "exclude_pattern": re.compile(r"/(shopping|sponsored)/"),
        "native_feed_urls": [],
    },
    {
        # kmiz.com redirects to abc17news.com -- same site, same content.
        "name": "ABC 17 News (KMIZ)",
        "listing_pages": [
            "https://abc17news.com/",
            "https://abc17news.com/category/news/local-news/",
        ],
        "link_pattern": re.compile(
            r"^https://(?:www\.)?abc17news\.com/[a-z0-9/-]+/\d{4}/\d{2}/\d{2}/[a-z0-9-]+/?$"
        ),
        "exclude_pattern": None,
        "native_feed_urls": [
            "https://abc17news.com/category/news/local-news/feed/",
            "https://abc17news.com/feed/",
        ],
    },
    {
        "name": "KOMU 8",
        "listing_pages": [
            "https://www.komu.com/news/",
            "https://www.komu.com/news/midmissourinews/",
        ],
        "link_pattern": re.compile(
            r"^https://(?:www\.)?komu\.com/[a-z0-9/-]+/article_[a-z0-9-]+\.html$"
        ),
        "exclude_pattern": None,
        "native_feed_urls": [
            "https://www.komu.com/search/?f=rss&t=article&l=30&s=start_time&sd=desc",
        ],
    },
    {
        # kbia.org runs on NPR's shared "Brightspot" station CMS. No public
        # RSS feed could be confirmed, but every article page reliably
        # carries og:title / og:description / article:published_time meta
        # tags, so the generic scrape-and-read-meta path handles it well.
        "name": "KBIA",
        "listing_pages": [
            "https://www.kbia.org/news",
            "https://www.kbia.org/kbia-news",
            "https://www.kbia.org/missouri-news",
        ],
        "link_pattern": re.compile(
            r"^https://(?:www\.)?kbia\.org/(?:[a-z0-9-]+/)+"
            r"\d{4}-\d{2}-\d{2}/[a-z0-9-]+/?$"
        ),
        # Skip the recurring daily newscast episodes -- they're audio
        # roundups of headlines already covered elsewhere, not standalone
        # stories, and would otherwise flood the feed with near-duplicates.
        "exclude_pattern": re.compile(r"/podcast/kbia-newscast/"),
        "native_feed_urls": [],
    },
    {
        # Same TownNews/BLOX CMS as the News Tribune, with the same
        # "/search/?f=rss" trick. Confirmed working: the Missourian
        # documents it (buried at /site/feeds.html) rather than just
        # exposing it by accident.
        "name": "Columbia Missourian",
        "listing_pages": [
            "https://www.columbiamissourian.com/",
            "https://www.columbiamissourian.com/news/local/",
        ],
        "link_pattern": re.compile(
            r"^https://(?:www\.)?columbiamissourian\.com/"
            r"[a-z0-9_/-]+/article_[a-f0-9-]+\.html$"
        ),
        "exclude_pattern": None,
        "native_feed_urls": [
            "https://www.columbiamissourian.com/search/?f=rss&t=article&l=30&s=start_time&sd=desc",
        ],
    },
]

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})

_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
_last_request_time: dict[str, float] = {}


def _domain(url: str) -> str:
    return urlsplit(url).netloc


def _robots_allows(url: str) -> bool:
    domain = _domain(url)
    rp = _robots_cache.get(domain)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        robots_url = f"{urlsplit(url).scheme}://{domain}/robots.txt"
        try:
            resp = _session.get(robots_url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                rp.allow_all = True  # no robots.txt -> assume allowed
        except requests.RequestException:
            rp.allow_all = True
        _robots_cache[domain] = rp
    if getattr(rp, "allow_all", False):
        return True
    try:
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True


def polite_get(url: str) -> requests.Response | None:
    """GET a URL with basic politeness: robots.txt check + rate limiting."""
    if not _robots_allows(url):
        print(f"  [skip] robots.txt disallows: {url}")
        return None

    domain = _domain(url)
    last = _last_request_time.get(domain, 0.0)
    wait = REQUEST_DELAY_SECONDS - (time.monotonic() - last)
    if wait > 0:
        time.sleep(wait)

    try:
        resp = _session.get(url, timeout=REQUEST_TIMEOUT)
        _last_request_time[domain] = time.monotonic()
        if resp.status_code >= 400:
            print(f"  [warn] HTTP {resp.status_code} for {url}")
            return None
        return resp
    except requests.RequestException as exc:
        print(f"  [warn] request failed for {url}: {exc}")
        return None


def normalize_url(url: str) -> str:
    """Strip query string / fragment so tracking params don't create dupes."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_date_safe(value: str) -> datetime | None:
    try:
        dt = dateparser.parse(value)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, OverflowError, TypeError):
        return None


# --------------------------------------------------------------------------
# Native feed attempts
# --------------------------------------------------------------------------


def try_native_feed(feed_url: str) -> list[dict] | None:
    """If feed_url actually serves a working RSS/Atom feed, parse it directly."""
    resp = polite_get(feed_url)
    if resp is None:
        return None

    text = resp.text.strip()
    if not text or ("<rss" not in text and "<feed" not in text.lower()):
        return None

    try:
        soup = BeautifulSoup(text, "xml")
    except Exception:
        return None

    items = soup.find_all(["item", "entry"])
    if not items:
        return None

    entries = []
    for item in items:
        link_tag = item.find("link")
        if link_tag is None:
            continue
        link = (link_tag.get("href") or link_tag.text or "").strip()
        title = (item.find("title").text or "").strip() if item.find("title") else ""
        desc_tag = item.find("description") or item.find("summary")
        description = (desc_tag.text or "").strip() if desc_tag else ""
        pub_tag = item.find("pubDate") or item.find("published") or item.find("updated")
        pub_dt = parse_date_safe(pub_tag.text) if pub_tag else None
        if not link or not title:
            continue
        entries.append(
            {
                "link": normalize_url(link),
                "title": title,
                "description": description,
                "pubdate": to_iso(pub_dt) if pub_dt else to_iso(datetime.now(timezone.utc)),
            }
        )
    return entries or None


# --------------------------------------------------------------------------
# Listing-page link extraction
# --------------------------------------------------------------------------


def extract_article_links(html: str, base_url: str, source: dict) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    found = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        href = normalize_url(href)
        if not source["link_pattern"].match(href):
            continue
        if source.get("exclude_pattern") and source["exclude_pattern"].search(href):
            continue
        found.add(href)
    return found


# --------------------------------------------------------------------------
# Per-article metadata extraction
# --------------------------------------------------------------------------


def _meta(soup: BeautifulSoup, **attrs) -> str | None:
    tag = soup.find("meta", attrs=attrs)
    if tag and tag.get("content"):
        return tag["content"].strip()
    return None


def fetch_article_metadata(url: str) -> dict | None:
    resp = polite_get(url)
    if resp is None:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    title = (
        _meta(soup, property="og:title")
        or _meta(soup, name="twitter:title")
        or (soup.title.text.strip() if soup.title else None)
    )
    description = (
        _meta(soup, property="og:description")
        or _meta(soup, name="description")
        or ""
    )

    pub_dt = None
    for attrs in (
        {"property": "article:published_time"},
        {"name": "article:published_time"},
        {"property": "og:updated_time"},
    ):
        raw = _meta(soup, **attrs)
        if raw:
            pub_dt = parse_date_safe(raw)
            if pub_dt:
                break

    if pub_dt is None:
        time_tag = soup.find("time", attrs={"datetime": True})
        if time_tag:
            pub_dt = parse_date_safe(time_tag["datetime"])

    if pub_dt is None:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            candidates = data if isinstance(data, list) else [data]
            for candidate in candidates:
                if isinstance(candidate, dict) and candidate.get("datePublished"):
                    pub_dt = parse_date_safe(candidate["datePublished"])
                    if pub_dt:
                        break
            if pub_dt:
                break

    if pub_dt is None:
        pub_dt = datetime.now(timezone.utc)

    if not title:
        return None

    return {
        "link": normalize_url(url),
        "title": title,
        "description": description,
        "pubdate": to_iso(pub_dt),
    }


# --------------------------------------------------------------------------
# seen.json persistence
# --------------------------------------------------------------------------


def load_seen() -> dict[str, dict]:
    if SEEN_PATH.exists():
        try:
            return json.loads(SEEN_PATH.read_text())
        except json.JSONDecodeError:
            print("  [warn] seen.json was corrupt, starting fresh")
    return {}


def save_seen(seen: dict[str, dict]) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(seen, indent=2, sort_keys=True))


def prune_seen(seen: dict[str, dict]) -> dict[str, dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=PRUNE_SEEN_AFTER_DAYS)
    pruned = {}
    for link, entry in seen.items():
        pub = parse_date_safe(entry.get("pubdate", "")) or datetime.now(timezone.utc)
        if pub >= cutoff:
            pruned[link] = entry
    return pruned


# --------------------------------------------------------------------------
# RSS output
# --------------------------------------------------------------------------


def xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_rss(entries: list[dict]) -> str:
    now_rfc822 = format_datetime(datetime.now(timezone.utc))

    items_xml = []
    for entry in entries:
        pub_dt = parse_date_safe(entry["pubdate"]) or datetime.now(timezone.utc)
        items_xml.append(
            f"""    <item>
      <title>{xml_escape(entry['title'])}</title>
      <link>{xml_escape(entry['link'])}</link>
      <guid isPermaLink="true">{xml_escape(entry['link'])}</guid>
      <pubDate>{format_datetime(pub_dt)}</pubDate>
      <source>{xml_escape(entry['source'])}</source>
      <description>{xml_escape(entry.get('description', ''))}</description>
    </item>"""
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{xml_escape(FEED_TITLE)}</title>
    <link>{xml_escape(FEED_HOMEPAGE_LINK)}</link>
    <atom:link href="{xml_escape(FEED_SELF_LINK)}" rel="self" type="application/rss+xml" />
    <description>{xml_escape(FEED_DESCRIPTION)}</description>
    <language>en-us</language>
    <lastBuildDate>{now_rfc822}</lastBuildDate>
{chr(10).join(items_xml)}
  </channel>
</rss>
"""


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def process_source(source: dict, seen: dict[str, dict]) -> int:
    """Returns number of new entries added to `seen` for this source."""
    print(f"Source: {source['name']}")
    new_count = 0

    # 1. Try native/hidden feeds first.
    for feed_url in source.get("native_feed_urls", []):
        print(f"  trying native feed: {feed_url}")
        entries = try_native_feed(feed_url)
        if entries:
            print(f"  native feed worked! {len(entries)} entries")
            for entry in entries:
                if entry["link"] not in seen:
                    entry["source"] = source["name"]
                    seen[entry["link"]] = entry
                    new_count += 1
            return new_count
        print("  no luck, falling back to scraping listing pages")

    # 2. Scrape listing pages for article links.
    candidate_links: set[str] = set()
    for listing_url in source["listing_pages"]:
        resp = polite_get(listing_url)
        if resp is None:
            continue
        links = extract_article_links(resp.text, listing_url, source)
        print(f"  {listing_url}: {len(links)} matching links")
        candidate_links |= links

    new_links = [link for link in candidate_links if link not in seen]
    print(f"  {len(new_links)} new links out of {len(candidate_links)} found")

    for link in new_links[:MAX_NEW_ARTICLES_PER_SOURCE]:
        metadata = fetch_article_metadata(link)
        if metadata is None:
            continue
        metadata["source"] = source["name"]
        seen[metadata["link"]] = metadata
        new_count += 1

    return new_count


def main() -> None:
    seen = load_seen()
    total_new = 0

    for source in SOURCES:
        try:
            total_new += process_source(source, seen)
        except Exception as exc:  # one bad source shouldn't kill the whole run
            print(f"  [error] {source['name']} failed: {exc}")

    seen = prune_seen(seen)
    save_seen(seen)

    ordered = sorted(
        seen.values(),
        key=lambda e: parse_date_safe(e["pubdate"]) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:MAX_ITEMS_IN_FEED]

    FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    FEED_PATH.write_text(build_rss(ordered))

    print(f"\nDone. {total_new} new articles this run. Feed has {len(ordered)} items.")


if __name__ == "__main__":
    sys.exit(main() or 0)
