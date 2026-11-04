"""Every source the pipeline reads from. This is the only place that says *what* gets scraped.

- To follow another YouTube channel, add its handle to `channels`.
- To read another feed from a site that's already here, add its URL to that site's `feed_urls`.
- To add a new site, give it a table and a `Digest` column in `app/database/models.py`,
  then add one `FeedSource(...)` entry here.
"""

from app.database.models import AnthropicArticle, OpenAIArticle
from app.ingestion.base import Source
from app.ingestion.feeds import FeedSource
from app.ingestion.youtube import YouTubeSource

SOURCES: list[Source] = [
    YouTubeSource(channels=["@anthropic-ai", "@OpenAI", "@matthew_berman"]),
    FeedSource(
        name="OpenAI",
        model=OpenAIArticle,
        digest_link="openai_article_id",
        feed_urls=["https://openai.com/news/rss.xml"],
    ),
    FeedSource(
        name="Anthropic",
        model=AnthropicArticle,
        digest_link="anthropic_article_id",
        # Anthropic doesn't publish feeds; these are community-maintained mirrors of its blogs.
        feed_urls=[
            "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_news.xml",
            "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_research.xml",
            "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_engineering.xml",
        ],
    ),
]
