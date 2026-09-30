"""Version the news evidence, independently of cache write/enrichment time."""

BIOTECH_NEWS_CONTRACT_VERSION = "biotech-news-v2"


def biotech_news_contract_valid(row):
    return isinstance(row, dict) and row.get(
        "News_Contract_Version", row.get("news_contract_version")
    ) == BIOTECH_NEWS_CONTRACT_VERSION
