import logging
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlparse

import tweepy

from app.core.config import settings
from app.models.blogger import Blogger
from app.models.raw_content import ContentTypeEnum, RawContent
from app.services.collectors.base import BaseCollector

logger = logging.getLogger(__name__)

# Maximum tweets to retrieve per crawl
MAX_RESULTS_PER_REQUEST = 10
MAX_PAGES = 3


class TwitterCollector(BaseCollector):
    """Collector for X (Twitter) using Twitter API v2 via tweepy."""

    def supports_platform(self, platform: str) -> bool:
        return platform == "x"

    def _get_client(self) -> tweepy.AsyncClient:
        if not settings.TWITTER_BEARER_TOKEN:
            raise RuntimeError("TWITTER_BEARER_TOKEN is not configured")
        return tweepy.AsyncClient(bearer_token=settings.TWITTER_BEARER_TOKEN)

    def _extract_username(self, blogger: Blogger) -> str:
        """
        Extract a Twitter username from the blogger's URL or name.
        Handles:
          - https://twitter.com/username
          - https://x.com/username
          - @username
          - username
        """
        url = blogger.url or ""
        if url:
            path = urlparse(url).path
            parts = [p for p in path.split("/") if p]
            if parts:
                return parts[0].lstrip("@")
        # Fall back to name field
        return blogger.name.lstrip("@")

    async def fetch(self, blogger: Blogger) -> List[RawContent]:
        client = self._get_client()
        username = self._extract_username(blogger)

        try:
            # Resolve username -> user id
            user_resp = await client.get_user(username=username)
            if not user_resp.data:
                logger.warning("TwitterCollector: user '%s' not found", username)
                return []
            user_id = user_resp.data.id
        except tweepy.TweepyException as exc:
            logger.error("TwitterCollector: failed to resolve user '%s': %s", username, exc)
            return []

        results: List[RawContent] = []
        pagination_token: Optional[str] = None

        for page_num in range(MAX_PAGES):
            try:
                resp = await client.get_users_tweets(
                    id=user_id,
                    max_results=MAX_RESULTS_PER_REQUEST,
                    pagination_token=pagination_token,
                    tweet_fields=["created_at", "text", "id"],
                    exclude=["retweets", "replies"],
                )
            except tweepy.TooManyRequests:
                logger.warning("TwitterCollector: rate limited on page %d for '%s'", page_num, username)
                break
            except tweepy.TweepyException as exc:
                logger.error("TwitterCollector: error fetching tweets: %s", exc)
                break

            if not resp.data:
                break

            for tweet in resp.data:
                published_at: Optional[datetime] = None
                if tweet.created_at:
                    # tweepy returns datetime objects for created_at
                    if isinstance(tweet.created_at, datetime):
                        published_at = tweet.created_at
                        if published_at.tzinfo is None:
                            published_at = published_at.replace(tzinfo=timezone.utc)
                    else:
                        try:
                            published_at = datetime.fromisoformat(str(tweet.created_at))
                        except ValueError:
                            published_at = None

                raw = RawContent(
                    blogger_id=blogger.id,
                    platform="x",
                    content_type=ContentTypeEnum.text,
                    raw_text=tweet.text,
                    source_url=f"https://x.com/{username}/status/{tweet.id}",
                    source_id=str(tweet.id),
                    published_at=published_at,
                    is_processed=False,
                )
                results.append(raw)

            # Check for next page
            meta = getattr(resp, "meta", None)
            if meta and hasattr(meta, "next_token") and meta.next_token:
                pagination_token = meta.next_token
            else:
                break

        logger.info(
            "TwitterCollector: fetched %d tweets for blogger '%s'",
            len(results),
            blogger.name,
        )
        return results
