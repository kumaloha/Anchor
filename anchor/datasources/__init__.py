"""
anchor/datasources
==================
数据源路由层：为 Layer 3 事实核查提供结构化 API 查询能力。

支持数据源：
  fred              FRED 美联储圣路易斯分行（826,000+ 时序）
  bls               BLS 美国劳工统计局官方 API v2
  world_bank        World Bank Open Data（200+ 国，无需 Key）
  imf               IMF DataMapper WEO（无需 Key）
  federal_register  美国联邦公报（行政令/关税声明，无需 Key）
  usitc             USITC 关税税率 + 进出口贸易量（无需 Key）
  akshare           中国官方数据（NBS/PBoC/MOFCOM，已安装）
  web               降级为 httpx 网页抓取
"""
from anchor.datasources.base import DataResult
from anchor.datasources.router import query as route_query

__all__ = ["DataResult", "route_query"]
