import requests
import logging

logger = logging.getLogger("FearlessFutures.News")

class NewsFetcher:
    def __init__(self):
        # Using a public RSS-to-JSON or similar free API for crypto news
        self.url = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN"

    def fetch_latest_news(self, limit=5):
        try:
            response = requests.get(self.url, timeout=10)
            data = response.json()
            news_items = data.get('Data', [])
            headlines = []
            for item in news_items[:limit]:
                headlines.append({
                    "title": item.get('title'),
                    "source": item.get('source'),
                    "body": item.get('body')[:100] + "..."
                })
            return headlines
        except Exception as e:
            logger.error(f"Error fetching news: {e}")
            return []
