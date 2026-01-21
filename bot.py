import os
import time
import requests
import feedparser
import html
import re
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, timezone
from dateutil import parser as dateparser

BOT_TOKEN = os.environ["BOT_TOKEN"]
TARGET = os.environ["TARGET"]  # e.g. "@tommydailynews"

MAX_PER_CATEGORY = 3
LOOKBACK_HOURS = 30  # last ~day

FEEDS = {
    "World": [
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://rss.cnn.com/rss/edition.rss",
        "https://www.aljazeera.com/xml/rss/all.xml",
        "https://www.theguardian.com/world/rss",
    ],
    "Italy": [
        "https://www.ilpost.it/feed/",
        "https://www.repubblica.it/rss/homepage/rss2.0.xml",
        "https://www.lastampa.it/rss.xml",
    ],
   "Luxury Fashion": [
    "https://www.voguebusiness.com/rss",
    "https://fashionunited.com/rss/news",
    "https://www.highsnobiety.com/feed/",
    "https://hypebeast.com/fashion/feed",  # Hypebeast Fashion RSS :contentReference[oaicite:0]{index=0}
    "https://fashionnetwork.com/rss.xml",
    "https://www.thefashionlaw.com/feed/",
    "https://www.vogue.com/feed/rss",      # Vogue (non Vogue Business) :contentReference[oaicite:1]{index=1}
],
    "Electronic Music": [
        "https://ra.co/rss/news",
        "https://mixmag.net/rss",
        "https://djmag.com/rss.xml",
    ],
    "Art": [
        "https://www.theartnewspaper.com/rss.xml",
        "https://www.artnews.com/c/art-news/news/feed/",
        "https://www.artforum.com/feed/",
    ],
    "Business & Tech (AI focus)": [
    "https://techcrunch.com/feed/",              # TechCrunch main feed :contentReference[oaicite:2]{index=2}
    "https://www.theverge.com/rss/index.xml",
    "https://www.wired.com/feed/rss",
    "https://news.ycombinator.com/rss",          # HN (segnali trend)
    "https://news.mit.edu/rss",                  # MIT News RSS :contentReference[oaicite:3]{index=3}
],
}

TIER1_KEYWORDS = [
    "voguebusiness.com",
    "vogue.com",
    "wwd.com",
    "businessoffashion.com",
    "highsnobiety.com",
]

def telegram_send_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
    "chat_id": TARGET,
    "text": text,
    "parse_mode": "HTML",
    "disable_web_page_preview": False,
}
    r = requests.post(url, data=payload, timeout=30)
    r.raise_for_status()

def safe_dt(entry):
    for k in ("published", "updated", "pubDate"):
        v = getattr(entry, k, None)
        if v:
            try:
                dt = dateparser.parse(v)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                pass
    return datetime.now(timezone.utc)

def clean(s: str) -> str:
    return " ".join((s or "").strip().split())

def pick_top(entries):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    # 1) filter recent + valid title/link
    candidates = []
    for e in entries:
        title = clean(getattr(e, "title", ""))
        link = clean(getattr(e, "link", ""))
        if not title or not link:
            continue
        if safe_dt(e) < cutoff:
            continue
        candidates.append(e)

    # 2) build clusters of similar titles (same story across sources)
    clusters = []  # each: {"key": norm_title, "items": [entry,...]}
    for e in candidates:
        nt = normalize_title(getattr(e, "title", ""))
        placed = False
        for c in clusters:
            if title_similarity(nt, c["key"]) >= 0.86:
                c["items"].append(e)
                placed = True
                break
        if not placed:
            clusters.append({"key": nt, "items": [e]})

    # 3) score clusters: cross-source + tier + recency
    scored = []
    now = datetime.now(timezone.utc)

    for c in clusters:
        items = c["items"]

        # unique sources (by feed url)
        sources = set(getattr(x, "_feed_url", "") for x in items)

        # tier score: count tier1 sources inside the cluster
        tier1_count = sum(1 for s in sources if s and is_tier1(s))

        # recency: newest item in cluster
        newest_dt = max(safe_dt(x) for x in items)
        age_hours = max(0.0, (now - newest_dt).total_seconds() / 3600.0)
        recency_score = max(0.0, 30.0 - age_hours)  # 0..30

        # importance score (tuneable)
        cross_source_score = len(sources) * 10.0
        tier_score = tier1_count * 4.0

        score = cross_source_score + tier_score + recency_score
        scored.append((score, newest_dt, c))

    # 4) pick top clusters, and inside each cluster pick best representative
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)

    picked = []
    for score, newest_dt, c in scored:
        # representative: prefer tier1 item; otherwise newest
        items = c["items"]
        tier1_items = [x for x in items if is_tier1(getattr(x, "_feed_url", ""))]
        if tier1_items:
            rep = max(tier1_items, key=lambda x: safe_dt(x))
        else:
            rep = max(items, key=lambda x: safe_dt(x))

        picked.append(rep)
        if len(picked) >= MAX_PER_CATEGORY:
            break

    return picked

def make_summary(entry):
    title = clean(getattr(entry, "title", ""))
    link = clean(getattr(entry, "link", ""))
    summary = clean(getattr(entry, "summary", "")) or clean(getattr(entry, "description", ""))

    if not summary:
        summary = "Short update available at the source link."

    if len(summary) > 320:
        summary = summary[:320].rsplit(" ", 1)[0] + "..."

    # IMPORTANT: escape any HTML coming from feeds
    title = html.escape(title)
    summary = html.escape(summary)

    return title, summary, link

def build_category_message(category, chosen_entries):
    lines = [f"<b>{category} — Top {len(chosen_entries)}</b>", ""]
    for i, e in enumerate(chosen_entries, 1):
        title, summary, link = make_summary(e)
        lines.append(f"<b>{i}) {title}</b>")
        lines.append(summary)
        lines.append(f'<a href="{link}">Source link</a>')
        lines.append("")
    return "\n".join(lines).strip()

def domain_from_url(u: str) -> str:
    u = (u or "").lower()
    u = re.sub(r"^https?://", "", u)
    return u.split("/")[0]

def is_tier1(feed_url: str) -> bool:
    d = domain_from_url(feed_url)
    return any(k in d for k in TIER1_KEYWORDS)

def normalize_title(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"<[^>]+>", " ", t)         # strip html tags if any
    t = re.sub(r"[^a-z0-9\s]", " ", t)     # keep alnum
    t = re.sub(r"\s+", " ", t).strip()
    return t

def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def main():
    now_rome = datetime.now(ZoneInfo("Europe/Rome"))
    print("TEST RUN - Rome time:", now_rome.isoformat())
    
    # Allow a 15-minute window around 08:00
    if not (now_rome.hour == 8 and 0 <= now_rome.minute <= 15):
        print("Skipping send (outside window)")
        return

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    telegram_send_message(
        f"<b>Daily News Briefing</b>\n<i>Compiled: {date_str} (UTC)</i>"
    )

    for category, urls in FEEDS.items():
        all_entries = []

        for feed_url in urls:
            try:
                d = feedparser.parse(feed_url)
                for e in (d.entries or []):
                    e._feed_url = feed_url  # attach source
                    all_entries.append(e)
            except Exception:
                continue

        chosen = pick_top(all_entries)

        if not chosen:
            telegram_send_message(
                f"<b>{category}</b>\n\nNo items found in the last {LOOKBACK_HOURS}h."
            )
        else:
            telegram_send_message(build_category_message(category, chosen))

        time.sleep(2)

if __name__ == "__main__":
    main()
