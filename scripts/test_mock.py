
"""
Offline test: monkeypatches polite_get() with canned HTML modeled on the
real markup of each of the four sites, then runs the full pipeline to make
sure regexes / extraction / RSS building all actually work end-to-end.

Run with: python3 scripts/test_mock.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_feed  # noqa: E402

# ---- Mock listing/article pages, modeled on real observed markup --------

MOCK_PAGES = {
    "https://www.newstribune.com/": """
        <html><body>
        <a href="/news/2026/sep/02/blair-oaks-unveils-expanded-high-school-campus-at/">
            Blair Oaks unveils expanded high school campus
        </a>
        <a href="/photos/galleries/2026/sep/02/some-gallery/">Not an article</a>
        <a href="/news/2026/sep/02/local-pantries-stay-busy-during-hunger-action/">
            Local pantries stay busy
        </a>
        </body></html>
    """,
    "https://www.newstribune.com/news/jefferson-city/": "<html><body></body></html>",
    "https://www.newstribune.com/news/missouri/": "<html><body></body></html>",
    "https://www.newstribune.com/news/2026/sep/02/blair-oaks-unveils-expanded-high-school-campus-at/": """
        <html><head>
        <meta property="og:title" content="Blair Oaks unveils expanded high school campus at community open house">
        <meta property="og:description" content="It didn't take long for a crowd to fill the halls.">
        <meta property="article:published_time" content="2026-09-02T09:00:00-05:00">
        </head><body></body></html>
    """,
    "https://www.newstribune.com/news/2026/sep/02/local-pantries-stay-busy-during-hunger-action/": """
        <html><head>
        <meta property="og:title" content="Local pantries stay busy during Hunger Action Month">
        <meta property="og:description" content="Pantries across the region report high demand.">
        <meta property="article:published_time" content="2026-09-02T04:00:00-05:00">
        </head><body></body></html>
    """,
    "https://www.newstribune.com/search/?f=rss&t=article&l=30&s=start_time&sd=desc": "not xml, 404-ish page",
    "https://krcgtv.com/": """
        <html><body>
        <a href="/news/local/osage-ambulance-district-announces-community-healthcare-vending-machine-in-chamois">
        Osage Ambulance District Announces Community Healthcare Vending Machine
        </a>
        <a href="/shopping/some-affiliate-deal">Shopping deal, should be excluded</a>
        </body></html>
    """,
    "https://krcgtv.com/news/local": "<html><body></body></html>",
    "https://krcgtv.com/sports": "<html><body></body></html>",
    "https://krcgtv.com/news/local/osage-ambulance-district-announces-community-healthcare-vending-machine-in-chamois": """
        <html><head>
        <meta property="og:title" content="Osage Ambulance District Announces Community Healthcare Vending Machine in Chamois">
        <meta property="og:description" content="The Osage Ambulance District announced the installation.">
        <meta property="article:published_time" content="2026-09-02T08:00:00-05:00">
        </head><body></body></html>
    """,
    "https://abc17news.com/": """
        <html><body>
        <a href="/news/crime/2026/09/02/truck-driver-in-tractor-trailer-crash-involving-42000-pounds-of-paint-charged-with-dwi/">
        Truck driver charged with DWI
        </a>
        </body></html>
    """,
    "https://abc17news.com/category/news/local-news/": "<html><body></body></html>",
    "https://abc17news.com/category/news/local-news/feed/": """<?xml version="1.0"?>
        <rss><channel>
        <item>
            <title>Truck driver in tractor-trailer crash charged with DWI</title>
            <link>https://abc17news.com/news/crime/2026/09/02/truck-driver-in-tractor-trailer-crash-involving-42000-pounds-of-paint-charged-with-dwi/</link>
            <description>A truck driver who crashed a semi carrying 42,000 pounds of paint was charged.</description>
            <pubDate>Wed, 02 Sep 2026 15:49:00 -0500</pubDate>
        </item>
        </channel></rss>
    """,
    "https://www.komu.com/news/": """
        <html><body>
        <a href="/news/state/missouri-supreme-court-weighs-constitutionality-of-a-vote-on-gerrymandered-map/article_a12a9103-58ea-4467-900b-0376df6f7ea5.html">
        Missouri Supreme Court weighs constitutionality
        </a>
        <a href="/sports/mizzou-freshman-wide-receiver-sleeps-in-locker-room-during-fall-camp/video_74e2e8d0-e663-5945-8499-0b7adc5460a4.html">
        Video, should be excluded
        </a>
        </body></html>
    """,
    "https://www.komu.com/news/midmissourinews/": "<html><body></body></html>",
    "https://www.komu.com/search/?f=rss&t=article&l=30&s=start_time&sd=desc": "not xml either",
    "https://www.komu.com/news/state/missouri-supreme-court-weighs-constitutionality-of-a-vote-on-gerrymandered-map/article_a12a9103-58ea-4467-900b-0376df6f7ea5.html": """
        <html><head>
        <meta property="og:title" content="Missouri Supreme Court weighs constitutionality of a vote on gerrymandered map">
        <meta property="og:description" content="The Missouri Supreme Court heard a case Wednesday.">
        <script type="application/ld+json">{"datePublished": "2026-09-02T14:00:00-05:00"}</script>
        </head><body></body></html>
    """,
}


def fake_polite_get(url):
    if url not in MOCK_PAGES:
        print(f"  (mock) no canned response for {url}, returning None")
        return None
    return SimpleNamespace(text=MOCK_PAGES[url], status_code=200)


def fake_robots_allows(url):
    return True


build_feed.polite_get = fake_polite_get
build_feed._robots_allows = fake_robots_allows

seen = {}
total_new = 0
for source in build_feed.SOURCES:
    total_new += build_feed.process_source(source, seen)

print(f"\nTotal new entries across all sources: {total_new}")
assert total_new == 5, f"expected 5 mock articles total, got {total_new}"

for link, entry in seen.items():
    print(f"- [{entry['source']}] {entry['title']}  ({entry['pubdate']})")

rss = build_feed.build_rss(list(seen.values()))
assert "<rss" in rss and "</rss>" in rss
assert "Blair Oaks" in rss
assert "Osage Ambulance" in rss
assert "Truck driver" in rss
assert "Missouri Supreme Court" in rss
assert "video_74e2e8d0" not in rss  # excluded video link must not leak in
assert "shopping/some-affiliate-deal" not in rss  # excluded shopping link

print("\nALL CHECKS PASSED")
