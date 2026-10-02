"""Version the news evidence, independently of cache write/enrichment time."""

# v3 unifies negation-aware risk interpretation and drops unavailable calendar
# scores during quick refresh. Old rows must undergo a fresh producer pass.
BIOTECH_NEWS_CONTRACT_VERSION = "biotech-news-v3"


def biotech_news_contract_valid(row):
    return isinstance(row, dict) and row.get(
        "News_Contract_Version", row.get("news_contract_version")
    ) == BIOTECH_NEWS_CONTRACT_VERSION
