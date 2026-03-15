"""
Schemas package — 域专用提取架构
================================
company 域使用 company.py 中的 CompanyExtractionResult。
旧 Node/Edge schemas (nodes.py) 保留供参考但不再主动导出。
"""

from anchor.extract.schemas.company import CompanyExtractionResult

__all__ = [
    "CompanyExtractionResult",
]
