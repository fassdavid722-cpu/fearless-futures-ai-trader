import requests
import logging

logger = logging.getLogger("FearlessFutures.News")

class NewsFetcher:
    """Multi-source crypto + macro news aggregator."""

    SOURCES = [
        {
            "name": "CryptoCompare",
            "url": "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=latest",
            "parser": "_parse_cryptocompare"
        },
        {
            "name": "CoinDesk RSS",
            "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "parser": "_parse_rss"
        }
    ]

    def fetch_latest_news(self, limit=6):
        headlines = []
        for source in self.SOURCES:
            if len(headlines) >= limit:
                break
            try:
                parser = getattr(self, source['parser'])
                items = parser(source['url'], limit - len(headlines))
                headlines.extend(items)
            except Exception as e:
                logger.warning(f"News fetch failed ({source['name']}): {e}")
        return headlines[:limit]

    def _parse_cryptocompare(self, url, limit):
        resp = requests.get(url, timeout=6).json()
        items = resp.get('Data', [])
        results = []
        for item in items[:limit]:
            results.append({
                "title": item.get('title', ''),
                "source": item.get('source_info', {}).get('name', 'CryptoCompare'),
                "body": (item.get('body', '')[:120] + "...") if item.get('body') else ""
            })
        return results

    def _parse_rss(self, url, limit):
        import xml.etree.ElementTree as ET
        resp = requests.get(url, timeout=6)
        root = ET.fromstring(resp.content)
        results = []
        for item in root.findall('./channel/item')[:limit]:
            title = item.findtext('title', '').strip()
            if title:
                results.append({
                    "title": title,
                    "source": "CoinDesk",
                    "body": ""
                })
        return results
