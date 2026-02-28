"""
anchor/datasources/federal_register.py
=======================================
Federal Register (联邦公报) API 适配器（无需 API Key）
机构：美国国家档案局（NARA）
覆盖：总统行政令、监管规则、贸易政策公告、关税声明

文件类型：
  PRESDOCU  总统文件（行政令 Executive Order、总统声明 Proclamation）
  RULE      最终规则
  PRORULE   拟议规则
  NOTICE    通知/公告

常用搜索场景：
  关税声明：  search_terms="tariff proclamation section 122", type="PRESDOCU"
  行政令：    search_terms="executive order tariff", type="PRESDOCU"
  贸易规则：  search_terms="section 301 investigation unfair trade", type="RULE"
  IEEPA：    search_terms="IEEPA tariff", type="PRESDOCU"
"""
from __future__ import annotations

import httpx
from loguru import logger
from .base import DataResult

_API_URL = "https://www.federalregister.gov/api/v1/documents"
_TIMEOUT = 20.0


async def query(params: dict) -> DataResult:
    """
    params 字段：
      search_terms  (必需)  搜索关键词，如 "tariff section 122 proclamation"
      type          (可选)  文件类型："PRESDOCU"|"RULE"|"PRORULE"|"NOTICE"，默认 PRESDOCU
      per_page      (可选)  返回条数，默认 5
      start_date    (可选)  "YYYY-MM-DD"，限制发布日期起点
      end_date      (可选)  "YYYY-MM-DD"，限制发布日期终点
    """
    search_terms = params.get("search_terms", "").strip()
    if not search_terms:
        return DataResult(content="Federal Register查询失败：未提供 search_terms",
                          data_period=None, source_url=None,
                          source_type="federal_register", ok=False)

    doc_type = params.get("type", "PRESDOCU")
    per_page = min(int(params.get("per_page", 5)), 10)

    req_params: dict = {
        "conditions[term]": search_terms,
        "conditions[type][]": doc_type,
        "per_page": per_page,
        "order": "relevance",
        "fields[]": ["title", "document_number", "publication_date",
                     "abstract", "html_url", "type", "president", "agency_names"],
    }
    if params.get("start_date"):
        req_params["conditions[publication_date][gte]"] = params["start_date"]
    if params.get("end_date"):
        req_params["conditions[publication_date][lte]"] = params["end_date"]

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_API_URL, params=req_params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"[FedRegister] query failed: {exc}")
        return DataResult(
            content=f"Federal Register 查询失败: {exc}",
            data_period=None, source_url="https://www.federalregister.gov",
            source_type="federal_register", ok=False,
        )

    docs = data.get("results", [])
    total = data.get("count", 0)

    if not docs:
        return DataResult(
            content=f"Federal Register 未找到与 '{search_terms}' 相关的文件",
            data_period=None, source_url="https://www.federalregister.gov",
            source_type="federal_register", ok=False,
        )

    dates = [d.get("publication_date", "") for d in docs if d.get("publication_date")]
    data_period = f"{min(dates)} 至 {max(dates)}" if dates else None

    lines = [
        f"Federal Register 检索结果",
        f"搜索词: {search_terms}",
        f"文件类型: {doc_type}",
        f"共找到 {total} 条结果，展示前 {len(docs)} 条",
        "=" * 60,
    ]

    for i, doc in enumerate(docs, 1):
        lines.append(f"\n[文件 {i}]")
        lines.append(f"  标题: {doc.get('title', 'N/A')}")
        lines.append(f"  文号: {doc.get('document_number', 'N/A')}")
        lines.append(f"  发布日期: {doc.get('publication_date', 'N/A')}")
        lines.append(f"  类型: {doc.get('type', 'N/A')}")
        agencies = doc.get("agency_names", [])
        if agencies:
            lines.append(f"  发布机构: {', '.join(agencies[:3])}")
        abstract = doc.get("abstract") or ""
        if abstract:
            lines.append(f"  摘要: {abstract[:300]}")
        lines.append(f"  URL: {doc.get('html_url', 'N/A')}")

    return DataResult(
        content="\n".join(lines),
        data_period=data_period,
        source_url="https://www.federalregister.gov",
        source_type="federal_register",
        ok=True,
    )
