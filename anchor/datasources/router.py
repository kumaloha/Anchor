"""
anchor/datasources/router.py
============================
数据源路由器 —— 根据 source_type 将查询分发给对应适配器。

支持的 source_type：
  fred              FRED (美联储圣路易斯分行)，美国宏观经济时序
  bls               BLS 官方 API v2，美国就业/CPI/JOLTS
  world_bank        World Bank Open Data，全球多国年度指标
  imf               IMF DataMapper，WEO 全球宏观预测与实际值
  federal_register  美国联邦公报，行政令/关税声明/贸易政策
  usitc             USITC，美国 HTS 关税税率 + 进出口贸易量
  akshare           中国官方数据（NBS/PBoC/MOFCOM）
  web               降级为 httpx 网页抓取（由 condition_verifier 直接处理）
"""
from __future__ import annotations

from loguru import logger
from .base import DataResult


async def query(source_type: str, query_params: dict) -> DataResult:
    """
    统一入口：根据 source_type 路由到对应适配器。

    若查询失败（ok=False），调用方可降级为网页抓取。
    若 source_type == "web"，直接返回 ok=False 以触发 httpx 降级。
    """
    st = (source_type or "").lower().strip()

    if st == "fred":
        from . import fred
        return await fred.query(query_params)

    elif st == "bls":
        from . import bls
        return await bls.query(query_params)

    elif st in ("world_bank", "worldbank", "wb"):
        from . import world_bank
        return await world_bank.query(query_params)

    elif st == "imf":
        from . import imf
        return await imf.query(query_params)

    elif st in ("federal_register", "fed_register", "fedreg"):
        from . import federal_register
        return await federal_register.query(query_params)

    elif st == "usitc":
        from . import usitc
        return await usitc.query(query_params)

    elif st in ("akshare", "akshare_cn", "china"):
        from . import akshare_cn
        return await akshare_cn.query(query_params)

    elif st == "web":
        # 让调用方走 httpx 网页抓取
        return DataResult(
            content="",
            data_period=None,
            source_url=None,
            source_type="web",
            ok=False,
        )

    else:
        logger.warning(f"[DataRouter] 未知 source_type: {source_type!r}")
        return DataResult(
            content=f"未知数据源类型: {source_type}",
            data_period=None,
            source_url=None,
            source_type=source_type,
            ok=False,
        )
