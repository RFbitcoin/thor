"""
RSS headline fetcher + VADER sentiment scorer.
Fetches from free crypto/finance RSS feeds, scores each headline,
and returns a list of scored articles plus an aggregate sentiment score.
"""

import feedparser
import time
import logging
from datetime import datetime, timezone
from nltk.sentiment.vader import SentimentIntensityAnalyzer

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RSS feed sources
# ---------------------------------------------------------------------------
FEEDS = [
    {"name": "CoinDesk",          "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",   "category": "crypto"},
    {"name": "Cointelegraph",     "url": "https://cointelegraph.com/rss",                     "category": "crypto"},
    {"name": "Bitcoin Magazine",  "url": "https://bitcoinmagazine.com/.rss/full/",            "category": "crypto"},
    {"name": "Reuters Business",  "url": "https://feeds.reuters.com/reuters/businessNews",    "category": "finance"},
    {"name": "Investing.com BTC", "url": "https://www.investing.com/rss/news_301.rss",        "category": "crypto"},
]

# Keywords that amplify sentiment score for crypto context
BULL_KEYWORDS = [
    "breakout", "rally", "surge", "all-time high", "ath", "bullish",
    "accumulation", "buy", "moon", "soar", "record", "gain", "rise",
    "adoption", "etf", "institutional", "halving", "upgrade",
]
BEAR_KEYWORDS = [
    "crash", "dump", "bear", "selloff", "sell-off", "plunge", "collapse",
    "ban", "hack", "exploit", "fraud", "scam", "regulation", "crackdown",
    "fear", "liquidation", "liquidated", "recession", "inflation",
]

_sia = SentimentIntensityAnalyzer()


def _boost_score(text: str, raw_score: float) -> float:
    """Amplify VADER compound score using crypto-specific keywords."""
    text_lower = text.lower()
    boost = 0.0
    for kw in BULL_KEYWORDS:
        if kw in text_lower:
            boost += 0.05
    for kw in BEAR_KEYWORDS:
        if kw in text_lower:
            boost -= 0.05
    return max(-1.0, min(1.0, raw_score + boost))


def _score_text(title: str, summary: str = "") -> float:
    combined = f"{title}. {summary}"
    raw = _sia.polarity_scores(combined)["compound"]
    return _boost_score(combined, raw)


def _label(score: float) -> str:
    if score >= 0.15:
        return "bullish"
    if score <= -0.15:
        return "bearish"
    return "neutral"


def fetch_headlines(max_per_feed: int = 10) -> dict:
    """
    Fetch and score headlines from all RSS feeds.

    Returns:
        {
            "articles": [{"title", "source", "category", "url", "score", "label", "published"}],
            "aggregate_score": float,   # -1.0 to +1.0
            "bull_pct": float,          # % bullish articles
            "bear_pct": float,
            "neutral_pct": float,
            "article_count": int,
            "fetched_at": ISO timestamp,
        }
    """
    articles = []

    for feed_info in FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            entries = feed.entries[:max_per_feed]
            for entry in entries:
                title   = entry.get("title", "").strip()
                summary = entry.get("summary", "").strip()[:300]
                url     = entry.get("link", "")
                # Parse published date
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub:
                    published = datetime(*pub[:6], tzinfo=timezone.utc).isoformat()
                else:
                    published = datetime.now(timezone.utc).isoformat()

                if not title:
                    continue

                score = _score_text(title, summary)
                articles.append({
                    "title":     title,
                    "source":    feed_info["name"],
                    "category":  feed_info["category"],
                    "url":       url,
                    "score":     round(score, 3),
                    "label":     _label(score),
                    "published": published,
                })
        except Exception as e:
            log.warning(f"RSS fetch failed for {feed_info['name']}: {e}")

    # Sort newest first (approximate — by list order is fine for RSS)
    if not articles:
        return {
            "articles": [],
            "aggregate_score": 0.0,
            "bull_pct": 0.0, "bear_pct": 0.0, "neutral_pct": 0.0,
            "article_count": 0,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    scores = [a["score"] for a in articles]
    agg    = sum(scores) / len(scores)

    bull    = sum(1 for a in articles if a["label"] == "bullish")
    bear    = sum(1 for a in articles if a["label"] == "bearish")
    neutral = len(articles) - bull - bear
    n       = len(articles)

    return {
        "articles":        articles,
        "aggregate_score": round(agg, 3),
        "bull_pct":        round(bull / n * 100, 1),
        "bear_pct":        round(bear / n * 100, 1),
        "neutral_pct":     round(neutral / n * 100, 1),
        "article_count":   n,
        "fetched_at":      datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import json
    result = fetch_headlines()
    print(f"Fetched {result['article_count']} articles")
    print(f"Aggregate sentiment: {result['aggregate_score']:+.3f}")
    print(f"Bull {result['bull_pct']}% | Neutral {result['neutral_pct']}% | Bear {result['bear_pct']}%")
    print("\nTop 5 headlines:")
    for a in result["articles"][:5]:
        print(f"  [{a['label']:7s}] {a['score']:+.2f}  {a['source']}: {a['title'][:80]}")
