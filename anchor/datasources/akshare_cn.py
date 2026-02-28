"""
anchor/datasources/akshare_cn.py
=================================
中国经济数据适配器（通过 akshare，已在 requirements.txt 中）
机构：中国国家统计局、中国人民银行、商务部（MOFCOM）、财政部

常用 function 名称：
  GDP：
    macro_china_gdp_yearly            年度 GDP 及增速
    macro_china_gdp_monthly           季度 GDP（实际增速）
  CPI / PPI：
    macro_china_cpi_monthly           月度 CPI（同比、环比）
    macro_china_ppi_monthly           月度 PPI
  就业：
    macro_china_urban_unemployment    城镇调查失业率（月度）
    macro_china_nbs_report            NBS 月度经济数据报告
  货币/金融（PBoC）：
    macro_china_money_supply          M0/M1/M2 月度数据
    macro_china_reserve_requirement   存款准备金率
    macro_china_loan_prime_rate       贷款市场报价利率（LPR）
  贸易（海关总署）：
    macro_china_trade_balance         月度进出口及贸易差额
    macro_china_imports_yoy           进口同比增速
    macro_china_exports_yoy           出口同比增速
  房地产：
    macro_china_real_estate           房地产开发投资、销售数据
"""
from __future__ import annotations

import asyncio
from loguru import logger
from .base import DataResult

# akshare function → 中文说明 映射
_FUNC_DESCRIPTIONS: dict[str, str] = {
    "macro_china_gdp_yearly": "中国年度GDP及增速",
    "macro_china_gdp_monthly": "中国季度GDP增速",
    "macro_china_cpi_monthly": "中国月度CPI（同比/环比）",
    "macro_china_ppi_monthly": "中国月度PPI",
    "macro_china_urban_unemployment": "中国城镇调查失业率（月度）",
    "macro_china_money_supply": "中国M0/M1/M2月度数据",
    "macro_china_reserve_requirement": "中国存款准备金率",
    "macro_china_loan_prime_rate": "中国贷款市场报价利率（LPR）",
    "macro_china_trade_balance": "中国月度进出口及贸易差额",
    "macro_china_imports_yoy": "中国进口同比增速",
    "macro_china_exports_yoy": "中国出口同比增速",
    "macro_china_real_estate": "中国房地产开发投资数据",
}


async def query(params: dict) -> DataResult:
    """
    params 字段：
      function    (必需)  akshare 函数名，如 "macro_china_cpi_monthly"
      tail_n      (可选)  展示最近 N 行，默认 24
      kwargs      (可选)  传给 akshare 函数的额外关键字参数（dict）
    """
    func_name = params.get("function", "").strip()
    if not func_name:
        return DataResult(content="akshare查询失败：未提供 function 名称",
                          data_period=None, source_url=None,
                          source_type="akshare", ok=False)

    tail_n = int(params.get("tail_n", 24))
    extra_kwargs: dict = params.get("kwargs", {}) or {}

    def _fetch():
        import akshare as ak
        func = getattr(ak, func_name, None)
        if func is None:
            raise AttributeError(f"akshare 中不存在函数: {func_name}")
        return func(**extra_kwargs)

    try:
        df = await asyncio.to_thread(_fetch)
    except Exception as exc:
        logger.warning(f"[akshare] query failed for {func_name}: {exc}")
        return DataResult(
            content=f"akshare 查询失败（{func_name}）: {exc}",
            data_period=None,
            source_url="https://akshare.akfamily.xyz",
            source_type="akshare",
            ok=False,
        )

    if df is None or df.empty:
        return DataResult(
            content=f"akshare {func_name} 返回空数据",
            data_period=None,
            source_url="https://akshare.akfamily.xyz",
            source_type="akshare",
            ok=False,
        )

    description = _FUNC_DESCRIPTIONS.get(func_name, func_name)
    tail_df = df.tail(tail_n)

    # 尝试识别日期范围
    date_col = next(
        (c for c in df.columns if any(k in c.lower() for k in ["日期", "date", "月份", "年份", "时间"])),
        None,
    )
    data_period: str | None = None
    if date_col and not df.empty:
        try:
            data_period = f"{str(df[date_col].iloc[0])} 至 {str(df[date_col].iloc[-1])}"
        except Exception:
            pass

    lines = [
        f"akshare 数据（中国官方来源）",
        f"函数: {func_name}",
        f"描述: {description}",
        f"字段: {', '.join(df.columns.tolist())}",
        "",
        tail_df.to_string(index=False, max_rows=tail_n),
    ]

    return DataResult(
        content="\n".join(lines),
        data_period=data_period,
        source_url="https://akshare.akfamily.xyz",
        source_type="akshare",
        ok=True,
    )
