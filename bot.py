import os
import time
import requests
import feedparser
import html
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
    "Business & Tech": [
        "https://www.theverge.com/rss/index.xml",
        "https://www.wired.com/feed/rss",
    ],
}

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
    recent = [e for e in entries if safe_dt(e) >= cutoff]
    recent.sort(key=lambda e: safe_dt(e), reverse=True)

    seen = set()
    picked = []
    for e in recent:
        title = clean(getattr(e, "title", ""))
        link = clean(getattr(e, "link", ""))
        if not title or not link:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        picked.append(e)
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

def main():
    # Header
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    telegram_send_message(f"<b>Daily News Briefing</b>\n<i>Compiled: {date_str} (UTC)</i>")

    for category, urls in FEEDS.items():
        all_entries = []
        for url in urls:
            try:
                d = feedparser.parse(url)
                all_entries.extend(d.entries or [])
            except Exception:
                continue

        chosen = pick_top(all_entries)
        if not chosen:
            telegram_send_message(f"<b>{category}</b>\n\nNo items found in the last {LOOKBACK_HOURS}h. (We can fix sources.)")
        else:
            telegram_send_message(build_category_message(category, chosen))

        time.sleep(2)

if __name__ == "__main__":
    main()
