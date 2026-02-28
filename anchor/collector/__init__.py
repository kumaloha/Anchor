from anchor.collector.base import BaseCollector, RawPostData
from anchor.collector.manager import CollectorManager
from anchor.collector.rss import RSSCollector
from anchor.collector.twitter import TwitterCollector
from anchor.collector.weibo import WeiboCollector

__all__ = [
    "BaseCollector",
    "RawPostData",
    "CollectorManager",
    "RSSCollector",
    "TwitterCollector",
    "WeiboCollector",
]