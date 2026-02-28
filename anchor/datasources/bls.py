"""
anchor/datasources/bls.py
=========================
BLS (Bureau of Labor Statistics) 官方 JSON API v2 适配器
机构：美国劳工统计局（权威原始数据）
API Key：免费注册 https://www.bls.gov/developers/home.htm（无 Key 时 25次/天）

常用 Series ID：
  就业（月度）：
    CES0000000001  Total Nonfarm Payrolls (SA, thousands)
    LNS13000000    Number Unemployed (SA, thousands)
    LNS14000000    Unemployment Rate (SA, %)
    LNS12000000    Civilian Employment (SA, thousands)
  JOLTS（月度，2001年起）：
    JTS000000000000000JOL  Total Job Openings (thousands)
    JTS000000000000000QUL  Total Quits (thousands)
    JTS000000000000000LDL  Total Layoffs & Discharges (thousands)
    JTS000000000000000HIL  Total Hires (thousands)
  CPI（月度）：
    CUUR0000SA0    CPI-U All Items (NSA, index)
    CUSR0000SA0    CPI-U All Items (SA, index)
    CUSR0000SA0L1E CPI-U Core (ex food&energy, SA)
  PPI：
    WPU00000000    PPI All Commodities
  Productivity：
    PRS85006092    Nonfarm Business Labor Productivity
"""
from __future__ import annotations

import httpx
from loguru import logger
from .base import DataResult

_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
_TIMEOUT = 20.0


async def query(params: dict) -> DataResult:
    """
    params 字段：
      series_id     (必需)  BLS series ID，如 "JTS000000000000000JOL"
                            也可以是列表，如 ["JTSJOL", "LNS13000000"] 做比率计算
      start_year    (可选)  "2024"，默认 2022
      end_year      (可选)  "2025"，默认当前年
    """
    from anchor.config import settings

    raw_id = params.get("series_id", "")
    series_ids = [raw_id] if isinstance(raw_id, str) else list(raw_id)
    series_ids = [s.strip() for s in series_ids if s.strip()]
    if not series_ids:
        return DataResult(content="BLS查询失败：未提供 series_id", data_period=None,
                          source_url=None, source_type="bls", ok=False)

    start_year = str(params.get("start_year", "2022"))
    end_year = str(params.get("end_year", "2025"))

    api_key = getattr(settings, "bls_api_key", "") or ""
    payload: dict = {
        "seriesid": series_ids,
        "startyear": start_year,
        "endyear": end_year,
        "calculations": False,
    }
    if api_key:
        payload["registrationkey"] = api_key

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                _API_URL,
                json=payload,
                headers={"Content-type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"[BLS] API request failed: {exc}")
        return DataResult(content=f"BLS API 请求失败: {exc}", data_period=None,
                          source_url=None, source_type="bls", ok=False)

    status = data.get("status", "")
    if status != "REQUEST_SUCCEEDED":
        msgs = "; ".join(data.get("message", []))
        return DataResult(content=f"BLS API 返回错误（{status}）: {msgs}",
                          data_period=None, source_url=None, source_type="bls", ok=False)

    results = data.get("Results", {}).get("series", [])
    if not results:
        return DataResult(content="BLS API 无返回数据", data_period=None,
                          source_url=None, source_type="bls", ok=False)

    lines: list[str] = []
    all_periods: list[str] = []

    for series_obj in results:
        sid = series_obj.get("seriesID", "")
        items = series_obj.get("data", [])
        lines.append(f"BLS Series: {sid}")
        lines.append(f"{'年份':<8}{'期次':<10}{'数值':<15}{'是否修订'}")
        lines.append("-" * 45)
        for item in sorted(items, key=lambda x: (x["year"], x["period"])):
            yr = item["year"]
            period = item["period"]
            val = item["value"]
            footnotes = ",".join(f.get("code", "") for f in item.get("footnotes", []) if f)
            revised = "已修订" if "R" in footnotes else ""
            lines.append(f"{yr:<8}{period:<10}{val:<15}{revised}")
            all_periods.append(f"{yr}-{period}")
        lines.append("")

    data_period = None
    if all_periods:
        data_period = f"{min(all_periods)} 至 {max(all_periods)}"

    return DataResult(
        content="\n".join(lines),
        data_period=data_period,
        source_url=f"https://www.bls.gov/developers/home.htm",
        source_type="bls",
        ok=True,
    )
