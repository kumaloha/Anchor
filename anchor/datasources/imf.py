"""
anchor/datasources/imf.py
=========================
IMF DataMapper API 适配器（无需 API Key）
机构：国际货币基金组织（IMF）
主要数据集：世界经济展望（WEO），每年 4 月/10 月更新

常用指标代码：
  宏观总量：
    NGDP_RPCH    实际 GDP 增长率 (%)
    NGDPD        名义 GDP (十亿美元)
    NGDPDPC      人均名义 GDP (美元)
    PPPGDP       购买力平价 GDP (国际美元)
  通胀：
    PCPIPCH      CPI 通胀率 (%)
    PCPIE        通胀率（剔除能源）
  失业：
    LUR          失业率 (%) — ILO 口径
  财政：
    GGXWDG_NGDP  政府债务占 GDP (%)
    GGXONLB_NGDP 政府净借贷占 GDP (%)
  外部账户：
    BCA_NGDPD    经常账户差额占 GDP (%)
    TM_RPCH      进口量增长 (%)
    TX_RPCH      出口量增长 (%)

国家代码（WEO 格式）：
  USA, CHN, DEU, JPN, GBR, FRA, IND, RUS, BRA, KOR, CAN, AUS
"""
from __future__ import annotations

import httpx
from loguru import logger
from .base import DataResult

_DATAMAPPER_URL = "https://www.imf.org/external/datamapper/api/v1/{indicator}"
_TIMEOUT = 20.0


async def query(params: dict) -> DataResult:
    """
    params 字段：
      indicator_code  (必需)  如 "NGDP_RPCH"
      country         (可选)  WEO 国家代码，如 "USA"；传 None 获取所有国家
                              也可以是列表 ["USA", "CHN"]
    """
    indicator_code = params.get("indicator_code", "").strip().upper()
    if not indicator_code:
        return DataResult(content="IMF查询失败：未提供 indicator_code",
                          data_period=None, source_url=None, source_type="imf", ok=False)

    raw_country = params.get("country")
    countries: list[str] = []
    if raw_country:
        countries = [raw_country] if isinstance(raw_country, str) else list(raw_country)
        countries = [c.strip().upper() for c in countries if c.strip()]

    url = _DATAMAPPER_URL.format(indicator=indicator_code)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"[IMF] query failed for {indicator_code}: {exc}")
        return DataResult(
            content=f"IMF DataMapper 查询失败（{indicator_code}）: {exc}",
            data_period=None,
            source_url=f"https://www.imf.org/external/datamapper/{indicator_code}",
            source_type="imf",
            ok=False,
        )

    values_by_country = data.get("values", {}).get(indicator_code, {})
    if not values_by_country:
        return DataResult(
            content=f"IMF DataMapper 无返回数据（{indicator_code}）",
            data_period=None,
            source_url=f"https://www.imf.org/external/datamapper/{indicator_code}",
            source_type="imf",
            ok=False,
        )

    # 若指定了国家，只保留这些国家
    if countries:
        values_by_country = {k: v for k, v in values_by_country.items() if k in countries}

    # 取指标元数据
    indicator_meta = data.get("indicators", {}).get(indicator_code, {})
    ind_label = indicator_meta.get("label", indicator_code)
    ind_unit = indicator_meta.get("unit", "")

    all_years: set[str] = set()
    for country_data in values_by_country.values():
        all_years.update(country_data.keys())
    sorted_years = sorted(all_years)[-15:]  # 最近 15 年

    lines = [
        f"IMF DataMapper 数据（WEO）",
        f"指标: {ind_label} ({indicator_code})",
        f"单位: {ind_unit}",
        f"展示年份: {sorted_years[0]} ~ {sorted_years[-1]}",
        "",
    ]

    for country_code, country_values in sorted(values_by_country.items()):
        lines.append(f"国家/地区: {country_code}")
        lines.append(f"{'年份':<8}{'数值'}")
        lines.append("-" * 20)
        for yr in sorted_years:
            if yr in country_values:
                val = country_values[yr]
                val_str = f"{val:,.3f}" if isinstance(val, (int, float)) else str(val)
                lines.append(f"{yr:<8}{val_str}")
        lines.append("")

    data_period = f"{sorted_years[0]} 至 {sorted_years[-1]}" if sorted_years else None

    return DataResult(
        content="\n".join(lines),
        data_period=data_period,
        source_url=f"https://www.imf.org/external/datamapper/{indicator_code}",
        source_type="imf",
        ok=True,
    )
