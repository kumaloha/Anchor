from anchor.collect.base import BaseCollector, RawPostData
from anchor.collect.manager import CollectorManager
from anchor.collect.rss import RSSCollector
from anchor.collect.twitter import TwitterCollector
from anchor.collect.weibo import WeiboCollector

__all__ = [
    "BaseCollector",
    "RawPostData",
    "CollectorManager",
    "RSSCollector",
    "TwitterCollector",
    "WeiboCollector",
]
