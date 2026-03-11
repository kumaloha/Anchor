"""
extractor.py — 向后兼容 shim
=============================
此文件已重构为 router.py + pipelines/ 结构。
保留此 shim 确保旧 import 路径不报错。
"""
from anchor.extract.router import Extractor  # noqa: F401

__all__ = ["Extractor"]
