"""
anchor/datasources/usitc.py
============================
USITC (U.S. International Trade Commission) 数据适配器（无需 API Key）
机构：美国国际贸易委员会
数据：美国 HTS 关税税率、进出口贸易量数据

USITC DataWeb: https://dataweb.usitc.gov
HTS (Harmonized Tariff Schedule): https://hts.usitc.gov

HTS 代码示例：
  8471.30  笔记本电脑
  8704.31  轻型汽油车
  2709.00  原油
  8803.30  飞机及航天器零件
  0207     家禽
"""
from __future__ import annotations

import httpx
from loguru import logger
from .base import DataResult

_HTS_API = "https://hts.usitc.gov/reststop/api/details/en/{hts_code}"
_TRADE_API = "https://dataweb.usitc.gov/trade/charting/data"
_TIMEOUT = 20.0


async def query(params: dict) -> DataResult:
    """
    params 字段：
      endpoint      (必需)  "tariff"（关税税率）或 "trade"（贸易量）
      hts_code      (tariff时必需)  HTS代码，如 "8471.30"（可省略小数点）
      hs_code       (trade时必需)   HS代码，如 "847130"（6位）
      flow          (trade时可选)   "imports" 或 "exports"，默认 imports
      partner       (trade时可选)   贸易伙伴 ISO2，如 "CN"；默认全球
      years         (trade时可选)   年份列表，如 ["2023","2024","2025"]
    """
    endpoint = params.get("endpoint", "tariff").lower()

    if endpoint == "tariff":
        return await _query_tariff(params)
    elif endpoint == "trade":
        return await _query_trade(params)
    else:
        return DataResult(content=f"USITC: 不支持的 endpoint '{endpoint}'",
                          data_period=None, source_url=None,
                          source_type="usitc", ok=False)


async def _query_tariff(params: dict) -> DataResult:
    hts_code = params.get("hts_code", "").replace(".", "").replace(" ", "").strip()
    if not hts_code:
        return DataResult(content="USITC关税查询失败：未提供 hts_code",
                          data_period=None, source_url=None,
                          source_type="usitc", ok=False)

    url = _HTS_API.format(hts_code=hts_code)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"[USITC] HTS query failed for {hts_code}: {exc}")
        return DataResult(
            content=f"USITC HTS 查询失败（{hts_code}）: {exc}",
            data_period=None,
            source_url=f"https://hts.usitc.gov/#{hts_code}",
            source_type="usitc",
            ok=False,
        )

    if not data:
        return DataResult(
            content=f"USITC HTS 未找到代码 {hts_code} 的数据",
            data_period=None,
            source_url=f"https://hts.usitc.gov/#{hts_code}",
            source_type="usitc",
            ok=False,
        )

    # HTS API 返回结构解析
    lines = [f"USITC 协调关税表（HTS）数据", f"HTS 代码: {hts_code}", ""]
    if isinstance(data, list):
        for entry in data[:5]:  # 最多展示5个子条目
            desc = entry.get("description") or entry.get("shortDescription") or ""
            gen_rate = entry.get("generalRateOfDuty") or entry.get("general") or "N/A"
            special = entry.get("specialRateOfDuty") or entry.get("special") or "N/A"
            col2 = entry.get("column2RateOfDuty") or entry.get("col2") or "N/A"
            indent = entry.get("indent", 0)
            lines.append(f"{'  ' * indent}{desc}")
            lines.append(f"{'  ' * indent}  一般税率: {gen_rate}")
            lines.append(f"{'  ' * indent}  特惠税率: {special}")
            lines.append(f"{'  ' * indent}  专栏2税率: {col2}")
            lines.append("")
    elif isinstance(data, dict):
        for k, v in data.items():
            lines.append(f"  {k}: {v}")

    return DataResult(
        content="\n".join(lines),
        data_period="当前HTS税率表",
        source_url=f"https://hts.usitc.gov/#{hts_code}",
        source_type="usitc",
        ok=True,
    )


async def _query_trade(params: dict) -> DataResult:
    """使用 USITC DataWeb 查询进出口贸易量数据（简化版 httpx 接口）。"""
    hs_code = params.get("hs_code", "").replace(".", "").strip()
    if not hs_code:
        return DataResult(content="USITC贸易查询失败：未提供 hs_code",
                          data_period=None, source_url=None,
                          source_type="usitc", ok=False)

    flow = params.get("flow", "imports").lower()
    partner = params.get("partner", "0000")  # 0000 = 全球
    years = params.get("years", ["2023", "2024", "2025"])

    # DataWeb API 简化调用
    payload = {
        "typeCode": "HTS",
        "flowType": "I" if flow == "imports" else "E",
        "reportPeriod": [f"{y}" for y in years],
        "classification": [hs_code[:6]],
        "partner": [partner],
        "measure": "VCY",  # Value (customs)
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_TRADE_API, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"[USITC] trade query failed for {hs_code}: {exc}")
        return DataResult(
            content=f"USITC 贸易数据查询失败（HS:{hs_code}）: {exc}",
            data_period=None,
            source_url=f"https://dataweb.usitc.gov/trade/annual/{hs_code}/{flow}/all",
            source_type="usitc",
            ok=False,
        )

    lines = [
        f"USITC 贸易数据（DataWeb）",
        f"HS 代码: {hs_code}",
        f"方向: {flow}",
        f"贸易伙伴: {'全球' if partner == '0000' else partner}",
        "",
    ]

    if isinstance(data, dict):
        rows = data.get("data", data.get("rows", []))
        for row in rows[:20]:
            lines.append(str(row))
    elif isinstance(data, list):
        for row in data[:20]:
            lines.append(str(row))

    return DataResult(
        content="\n".join(lines),
        data_period=f"{min(years)} 至 {max(years)}",
        source_url=f"https://dataweb.usitc.gov/trade/annual/{hs_code}/{flow}/all",
        source_type="usitc",
        ok=True,
    )
