"""
Sentiment & news signals from crypto RSS feeds.
Uses keyword-based NLP scoring (no external NLP library needed).
"""
import requests
import xml.etree.ElementTree as ET
import re
from data.feargreed import get_fear_greed

FEEDS = [
    'https://feeds.feedburner.com/CoinDesk',
    'https://cointelegraph.com/rss',
]

BULLISH_WORDS = [
    'rally', 'surge', 'soar', 'breakout', 'bull', 'bullish', 'buy', 'adoption',
    'institutional', 'etf', 'approval', 'partnership', 'launch', 'upgrade',
    'halving', 'accumulate', 'support', 'rebound', 'recovery', 'upside',
    'all-time high', 'ath', 'green', 'gains', 'positive', 'boost'
]

BEARISH_WORDS = [
    'crash', 'dump', 'bear', 'bearish', 'sell', 'ban', 'hack', 'exploit',
    'liquidation', 'fear', 'panic', 'collapse', 'plunge', 'decline', 'drop',
    'correction', 'resistance', 'warning', 'risk', 'concern', 'negative',
    'regulatory', 'crackdown', 'fraud', 'scam', 'lose', 'loss', 'red'
]

def _score_text(text):
    text = text.lower()
    bull = sum(1 for w in BULLISH_WORDS if w in text)
    bear = sum(1 for w in BEARISH_WORDS if w in text)
    return bull, bear

def get_news_sentiment(symbol='BTC', limit=20):
    """Fetch recent crypto headlines and score sentiment."""
    headlines = []
    total_bull, total_bear = 0, 0

    for feed_url in FEEDS:
        try:
            r = requests.get(feed_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            root = ET.fromstring(r.content)
            items = root.findall('.//item')[:limit//2]
            for item in items:
                title = item.findtext('title', '') or ''
                desc = item.findtext('description', '') or ''
                text = title + ' ' + desc
                bull, bear = _score_text(text)
                total_bull += bull
                total_bear += bear
                if title:
                    headlines.append({
                        'title': title[:100],
                        'sentiment': 'bullish' if bull > bear else 'bearish' if bear > bull else 'neutral'
                    })
        except Exception as e:
            print(f'Feed error {feed_url}: {e}')

    total = total_bull + total_bear
    bull_pct = (total_bull / total * 100) if total > 0 else 50

    if bull_pct > 65:   signal = 'BULLISH';  score = 1
    elif bull_pct > 55: signal = 'LEANING_BULLISH'; score = 1
    elif bull_pct < 35: signal = 'BEARISH';  score = -1
    elif bull_pct < 45: signal = 'LEANING_BEARISH'; score = -1
    else:               signal = 'NEUTRAL';  score = 0

    return {
        'bull_pct': round(bull_pct, 1),
        'total_articles': len(headlines),
        'signal': signal,
        'score': score,
        'headlines': headlines[:5]
    }

def get_sentiment_summary(symbol='BTC'):
    """Full sentiment pillar: Fear & Greed + news NLP."""
    fg = get_fear_greed()
    fgv = fg.get('value', 50)

    # Fear & Greed score (contrarian at extremes)
    if fgv < 20:   fg_score = 2    # extreme fear = strong buy signal
    elif fgv < 35: fg_score = 1
    elif fgv > 80: fg_score = -2   # extreme greed = strong sell signal
    elif fgv > 65: fg_score = -1
    else:          fg_score = 0

    news = get_news_sentiment(symbol)
    # Anti-correlation: if F&G is extreme AND news is same direction, downweight news
    # (crowded consensus = less signal)
    if fgv < 25 and news['score'] > 0:   news['score'] = 0  # already priced in
    if fgv > 75 and news['score'] < 0:   news['score'] = 0

    score = fg_score + news['score']
    return {
        'fear_greed': fg,
        'fear_greed_score': fg_score,
        'news': news,
        'score': score,
        'max_score': 4
    }

if __name__ == '__main__':
    import json
    print(json.dumps(get_sentiment_summary('BTC'), indent=2))
