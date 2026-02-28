"""
anchor/datasources/fred.py
==========================
FRED (Federal Reserve Economic Data) 适配器
机构：美联储圣路易斯分行 (St. Louis Fed)
覆盖：826,000+ 时序——BLS就业、BEA国民账户、美联储利率、CPI/PCE 通胀等
API Key：免费注册 https://fred.stlouisfed.org/docs/api/api_key.html

常用 Series ID：
  就业：PAYEMS(非农总就业), UNRATE(失业率), LNS13000000(失业人数,千)
  JOLTS：JTSJOL(职位空缺), JTSQUL(主动离职), JTSLDL(裁员), JTSHIL(新增雇佣)
  通胀：CPIAUCSL(CPI全项,SA), CPILFESL(核心CPI), PCEPI(PCE)
  GDP：GDPC1(实际GDP,季度), GDP(名义GDP,季度)
  利率：FEDFUNDS(联邦基金), DGS10(10年期国债), DGS2(2年期国债)
  贸易：BOPGSTB(商品贸易余额,月度)
  金融：SP500(标普500,日度), VIXCLS(VIX,日度)
"""
from __future__ import annotations

import asyncio
from loguru import logger
from .base import DataResult


async def query(params: dict) -> DataResult:
    """
    params 字段：
      series_id   (必需)  FRED 时序标识，如 "PAYEMS"
      start_date  (可选)  "YYYY-MM-DD"，默认抓最近 3 年
      end_date    (可选)  "YYYY-MM-DD"，默认今天
      tail_n      (可选)  最多展示最近 N 个观测值，默认 36
    """
    series_id = params.get("series_id", "").strip().upper()
    if not series_id:
        return DataResult(content="FRED查询失败：未提供 series_id", data_period=None,
                          source_url=None, source_type="fred", ok=False)

    start_date = params.get("start_date")
    end_date = params.get("end_date")
    tail_n = int(params.get("tail_n", 36))

    def _fetch():
        from fredapi import Fred
        from anchor.config import settings
        api_key = settings.fred_api_key or None
        fred = Fred(api_key=api_key)
        info = fred.get_series_info(series_id)
        series = fred.get_series(
            series_id,
            observation_start=start_date,
            observation_end=end_date,
        )
        return info, series

    try:
        info, series = await asyncio.to_thread(_fetch)
    except Exception as exc:
        logger.warning(f"[FRED] query failed for {series_id}: {exc}")
        return DataResult(
            content=f"FRED查询失败（{series_id}）: {exc}",
            data_period=None,
            source_url=f"https://fred.stlouisfed.org/series/{series_id}",
            source_type="fred",
            ok=False,
        )

    if series.empty:
        return DataResult(
            content=f"FRED返回空数据（{series_id}）",
            data_period=None,
            source_url=f"https://fred.stlouisfed.org/series/{series_id}",
            source_type="fred",
            ok=False,
        )

    title = info.get("title", series_id) if hasattr(info, "get") else str(info.get("title", series_id))
    freq = info.get("frequency_short", "") if hasattr(info, "get") else ""
    units = info.get("units_short", "") if hasattr(info, "get") else ""
    seasonal = info.get("seasonal_adjustment_short", "") if hasattr(info, "get") else ""

    first_date = series.index[0].strftime("%Y-%m-%d")
    last_date = series.index[-1].strftime("%Y-%m-%d")
    data_period = f"{first_date} 至 {last_date}"

    lines = [
        f"FRED 数据系列: {series_id}",
        f"标题: {title}",
        f"频率: {freq}  单位: {units}  季调: {seasonal}",
        f"完整数据范围: {first_date} ~ {last_date}",
        f"（以下展示最近 {min(tail_n, len(series))} 期）",
        "",
        "日期            数值",
        "-" * 30,
    ]
    for date, val in series.tail(tail_n).items():
        val_str = f"{val:,.3f}" if val == val else "NaN"  # NaN check
        lines.append(f"{date.strftime('%Y-%m-%d')}   {val_str}")

    return DataResult(
        content="\n".join(lines),
        data_period=data_period,
        source_url=f"https://fred.stlouisfed.org/series/{series_id}",
        source_type="fred",
        ok=True,
        extra={"series_id": series_id, "last_value": float(series.iloc[-1]) if not series.empty else None},
    )
