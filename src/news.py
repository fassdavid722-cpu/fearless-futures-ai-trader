import requests
import logging

logger = logging.getLogger("FearlessFutures.News")

class NewsFetcher:
    def __init__(self):
        self.url = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN"

    def fetch_latest_news(self, limit=5):
        try:
            response = requests.get(self.url, timeout=5)
            data = response.json()
            # Ensure we are accessing the list correctly
            news_items = data.get('Data', [])
            if not isinstance(news_items, list):
                return []
                
            headlines = []
            # Use a simple loop to avoid slice errors if the list is smaller than limit
            count = 0
            for item in news_items:
                if count >= limit:
                    break
                headlines.append({
                    "title": item.get('title', 'No Title'),
                    "source": item.get('source', 'Unknown'),
                    "body": (item.get('body', '')[:100] + "...") if item.get('body') else ""
                })
                count += 1
            return headlines
        except Exception as e:
            logger.error(f"Error fetching news: {e}")
            return []
