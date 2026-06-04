import requests
import logging

logger = logging.getLogger("FearlessFutures.Macro")

class MacroFetcher:
    """Fetches global macro signals: Fear & Greed, BTC dominance, market cap."""

    def fetch(self) -> dict:
        data = {}
        try:
            # Fear & Greed Index
            fg = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5).json()
            fg_data = fg['data'][0]
            data['fear_greed_value'] = int(fg_data['value'])
            data['fear_greed_label'] = fg_data['value_classification']
        except Exception as e:
            logger.warning(f"Fear & Greed fetch failed: {e}")
            data['fear_greed_value'] = 50
            data['fear_greed_label'] = 'Neutral'

        try:
            # CoinGecko global market data
            cg = requests.get("https://api.coingecko.com/api/v3/global", timeout=8).json()
            gd = cg.get('data', {})
            data['btc_dominance'] = round(gd.get('market_cap_percentage', {}).get('btc', 0), 2)
            data['eth_dominance'] = round(gd.get('market_cap_percentage', {}).get('eth', 0), 2)
            data['market_cap_change_24h'] = round(gd.get('market_cap_change_percentage_24h_usd', 0), 2)
            data['total_market_cap_usd'] = gd.get('total_market_cap', {}).get('usd', 0)
            data['active_cryptos'] = gd.get('active_cryptocurrencies', 0)
        except Exception as e:
            logger.warning(f"CoinGecko global fetch failed: {e}")

        try:
            # BTC 24h change
            btc = requests.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true",
                timeout=8
            ).json()
            data['btc_price'] = btc['bitcoin']['usd']
            data['btc_change_24h'] = round(btc['bitcoin']['usd_24h_change'], 2)
        except Exception as e:
            logger.warning(f"BTC price fetch failed: {e}")

        return data

    def format_for_telegram(self, data: dict) -> str:
        fg = data.get('fear_greed_value', '?')
        fg_label = data.get('fear_greed_label', '?')
        fg_emoji = '😱' if fg < 25 else ('😨' if fg < 45 else ('😐' if fg < 55 else ('🤑' if fg < 75 else '🚀')))

        btc_d = data.get('btc_dominance', '?')
        mkt_chg = data.get('market_cap_change_24h', '?')
        btc_px = data.get('btc_price', '?')
        btc_chg = data.get('btc_change_24h', '?')
        btc_emoji = '🟢' if (isinstance(btc_chg, (int, float)) and btc_chg > 0) else '🔴'

        return (
            f"🌍 *Global Macro Snapshot*\n\n"
            f"{fg_emoji} Fear & Greed: *{fg}/100* ({fg_label})\n"
            f"₿ BTC Dominance: *{btc_d}%*\n"
            f"{btc_emoji} BTC Price: *${btc_px:,.0f}* ({btc_chg:+.2f}%)\n"
            f"📊 Total Market Cap 24h: *{mkt_chg:+.2f}%*"
        )
