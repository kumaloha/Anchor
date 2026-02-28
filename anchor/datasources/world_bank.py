"""
anchor/datasources/world_bank.py
================================
World Bank Open Data API 适配器（无需 API Key）
机构：世界银行（World Bank）
覆盖：200+ 国家，63 个数据库，17,500+ 指标，历史追溯至 1960 年

常用指标 ID（economy 参数用 ISO-2 代码，如 US、CN、DE）：
  GDP：
    NY.GDP.MKTP.KD.ZG   GDP 实际增长率 (%)
    NY.GDP.MKTP.CD       GDP 现价美元
    NY.GDP.PCAP.CD       人均 GDP (USD)
  通胀：
    FP.CPI.TOTL.ZG       CPI 年变化率 (%)
  失业：
    SL.UEM.TOTL.ZS       失业率 (%, ILO 口径)
    SL.UEM.TOTL.NE.ZS    失业率（劳动力%，国家口径）
  贸易：
    NE.TRD.GNFS.ZS       贸易总额占 GDP (%)
    BN.CAB.XOKA.GD.ZS    经常账户差额占 GDP (%)
    TM.VAL.MRCH.CD.WT    商品进口 (USD)
    TX.VAL.MRCH.CD.WT    商品出口 (USD)
  债务/财政：
    GC.DOD.TOTL.GD.ZS    中央政府债务占 GDP (%)
    GC.BAL.CASH.GD.ZS    财政盈余/赤字占 GDP (%)
"""
from __future__ import annotations

import httpx
from loguru import logger
from .base import DataResult

_BASE_URL = "https://api.worldbank.org/v2/country/{economy}/indicator/{indicator_id}"
_TIMEOUT = 20.0


async def query(params: dict) -> DataResult:
    """
    params 字段：
      indicator_id  (必需)  如 "NY.GDP.MKTP.KD.ZG"
      economy       (可选)  ISO2 国家代码，如 "US"、"CN"；默认 "US"
                            传 "all" 获取全部国家
      mrv           (可选)  最近 N 期，默认 10
    """
    indicator_id = params.get("indicator_id", "").strip()
    if not indicator_id:
        return DataResult(content="World Bank查询失败：未提供 indicator_id",
                          data_period=None, source_url=None, source_type="world_bank", ok=False)

    economy = params.get("economy", "US").strip().upper()
    mrv = int(params.get("mrv", 10))

    url = _BASE_URL.format(economy=economy, indicator_id=indicator_id)
    request_params = {"format": "json", "per_page": 50, "mrv": mrv}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, params=request_params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"[WorldBank] query failed for {indicator_id}: {exc}")
        return DataResult(
            content=f"World Bank 查询失败（{indicator_id}）: {exc}",
            data_period=None,
            source_url=f"https://data.worldbank.org/indicator/{indicator_id}",
            source_type="world_bank",
            ok=False,
        )

    if not isinstance(data, list) or len(data) < 2 or not data[1]:
        return DataResult(
            content=f"World Bank 无返回数据（{indicator_id}, {economy}）",
            data_period=None,
            source_url=f"https://data.worldbank.org/indicator/{indicator_id}",
            source_type="world_bank",
            ok=False,
        )

    meta = data[0]
    records = [r for r in data[1] if r.get("value") is not None]

    if not records:
        return DataResult(
            content=f"World Bank 该经济体无此指标数据（{indicator_id}, {economy}）",
            data_period=None,
            source_url=f"https://data.worldbank.org/indicator/{indicator_id}",
            source_type="world_bank",
            ok=False,
        )

    indicator_name = records[0].get("indicator", {}).get("value", indicator_id)
    country_name = records[0].get("country", {}).get("value", economy)
    unit = records[0].get("unit", "")

    sorted_records = sorted(records, key=lambda r: r.get("date", ""))
    years = [r["date"] for r in sorted_records]
    data_period = f"{years[0]} 至 {years[-1]}" if years else None

    lines = [
        f"World Bank 数据",
        f"指标: {indicator_name} ({indicator_id})",
        f"国家/经济体: {country_name} ({economy})",
        f"单位: {unit or '见指标说明'}",
        "",
        f"{'年份':<10}{'数值'}",
        "-" * 30,
    ]
    for r in sorted_records:
        val = r.get("value")
        val_str = f"{val:,.3f}" if isinstance(val, (int, float)) else str(val)
        lines.append(f"{r['date']:<10}{val_str}")

    return DataResult(
        content="\n".join(lines),
        data_period=data_period,
        source_url=f"https://data.worldbank.org/indicator/{indicator_id}?locations={economy}",
        source_type="world_bank",
        ok=True,
    )
