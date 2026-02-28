"""
Claude 观点提取的输入/输出 Pydantic 模型（v4）

四类基础结构：
  ExtractedFact        — 独立可核实事实
  ExtractedConclusion  — 结论（含回顾型 retrospective 和预测型 predictive）
  ExtractedSolution    — 解决方案（作者从结论推导出的行动建议）
  ExtractedLogic       — 论证关系（inference: 事实→结论，derivation: 结论→解决方案）
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 验证引用建议
# ---------------------------------------------------------------------------


class SuggestedReference(BaseModel):
    """建议用于验证该事实的权威数据来源"""

    organization: str = Field(
        description=(
            "发布机构全称，必须是政府机构、央行、国际组织或权威学术机构。"
            "例：'美国劳工统计局 (BLS)'、'美联储 (Federal Reserve)'、"
            "'国际货币基金组织 (IMF)'、'中国国家统计局 (NBS)'"
        )
    )
    data_description: str = Field(
        description=(
            "应查阅的具体数据集或报告名称。"
            "例：'Employment Situation Summary（非农就业月报）'、"
            "'联邦公开市场委员会会议纪要 (FOMC Minutes)'"
        )
    )
    url: Optional[str] = Field(
        default=None,
        description=(
            "数据页面的具体 URL（若可确定）。"
            "不确定则填 null，不要猜测或伪造 URL。"
        )
    )
    url_note: Optional[str] = Field(
        default=None,
        description="URL 使用说明，如 '每月第一个周五美东时间8:30发布'"
    )


# ---------------------------------------------------------------------------
# 模型一：事实（Fact）
# ---------------------------------------------------------------------------


class ExtractedFact(BaseModel):
    """一条独立的可核实事实

    事实是文章中出现的陈述，可以独立于结论和解决方案进行核实。
    与结论/解决方案解耦——同一个事实可以支撑多个结论。
    """

    claim: str = Field(
        description=(
            "事实的核心陈述，保留信息源原意，简洁精炼（≤80字）。"
            "例：'美国2025年1月非农就业新增130K，比预期多一倍'"
        )
    )
    canonical_claim: Optional[str] = Field(
        default=None,
        description=(
            "概念归一化标准形式（≤60字）。"
            "将原始陈述转化为标准术语，使语义相同但表述不同的事实能被识别为同一概念。"
            "规则：统一使用标准金融/经济术语（如'美联储'而非'Fed'或'美国央行'）；"
            "保留核心量化指标；剔除修辞性限定词；中英混合内容统一转为中文。"
            "例：原文'七成大国财富清零'和'7 of 10 great powers saw wealth wiped out'"
            "都应归一化为'1900-1945年间七成大国经历财富清零事件'。"
        )
    )
    verifiable_expression: Optional[str] = Field(
        default=None,
        description=(
            "转化为可客观核实的具体表达，必须含量化指标或明确可观测事件。"
            "若无法转化（纯主观判断），填 null。"
        )
    )
    is_verifiable: bool = Field(
        description="是否已成功转化为可验证形式"
    )
    verification_method: Optional[str] = Field(
        default=None,
        description=(
            "具体验证操作：对比哪段时间的哪个数据，判定阈值是什么。"
            "例：'查阅BLS 2025年1月就业报告，确认新增就业人数及修订前后数字'"
        )
    )
    validity_start_note: Optional[str] = Field(
        default=None,
        description="事实有效期起点，如 '2025年1月起' 或 null"
    )
    validity_end_note: Optional[str] = Field(
        default=None,
        description="事实有效期终点，如 '2025年12月31日前' 或 null"
    )
    suggested_references: list[SuggestedReference] = Field(
        default_factory=list,
        description="验证此事实需要查阅的权威数据来源（1-3条），必须是真实存在的机构"
    )


# ---------------------------------------------------------------------------
# 模型二：结论（Conclusion）
# ---------------------------------------------------------------------------


class ExtractedConclusion(BaseModel):
    """作者的分析判断（含回顾型和预测型）

    conclusion_type 区分两类：
      retrospective — 对过去/当前事件的判断（"X 已经发生"，"Y 现在处于状态 Z"）
      predictive    — 对未来事件的判断（"预计/将会/可能 Z"）
    """

    topic: str = Field(description="所属话题，简短精炼，如 '美国关税政策'")
    claim: str = Field(description="核心结论陈述，第三人称，≤80字")
    canonical_claim: Optional[str] = Field(
        default=None,
        description=(
            "概念归一化标准形式（≤60字）。使用标准术语，使不同来源的相同判断能被识别。"
            "例：'大宗商品在货币贬值周期中跑赢金融资产'。"
        )
    )
    conclusion_type: Literal["retrospective", "predictive"] = Field(
        default="retrospective",
        description=(
            "结论类型：retrospective=对过去/当前的判断（已发生/正在发生）；"
            "predictive=对未来的判断（预计/将会/可能）"
        )
    )
    time_horizon_note: Optional[str] = Field(
        default=None,
        description="结论的有效时效描述，如 '裁决生效后150天内有效' 或 null"
    )
    valid_until_note: Optional[str] = Field(
        default=None,
        description="仅 predictive 类型：预测有效期原文表达，如 '2025年年底前' 或 null"
    )


# ---------------------------------------------------------------------------
# 模型三：解决方案（Solution）
# ---------------------------------------------------------------------------


class ExtractedSolution(BaseModel):
    """作者从结论推导出的具体行动建议"""

    topic: str = Field(description="所属话题，简短精炼")
    claim: str = Field(description="建议内容，第三人称，≤100字")
    action_type: Optional[str] = Field(
        default=None,
        description="行动类型：buy/sell/hold/short/diversify/hedge/reduce"
    )
    action_target: Optional[str] = Field(
        default=None,
        description="标的物，如'黄金ETF'，'美国10年期国债'，'人民币资产'"
    )
    action_rationale: Optional[str] = Field(
        default=None,
        description="一句话说明此建议如何从引用的结论推导而来"
    )
    source_conclusion_indices: list[int] = Field(
        default_factory=list,
        description="推导此建议所基于的结论，指向 conclusions[] 数组的 0-based 索引列表"
    )


# ---------------------------------------------------------------------------
# 模型四：逻辑（Logic）
# ---------------------------------------------------------------------------


class ExtractedLogic(BaseModel):
    """论证/推导关系

    inference 类型：事实→结论的论证关系
      - target_index: conclusions[] 的索引
      - supporting_fact_indices: 已知支撑事实
      - assumption_fact_indices: 假设条件

    derivation 类型：结论→解决方案的推导关系
      - solution_index: solutions[] 的索引
      - source_conclusion_indices: 推导所基于的结论索引
    """

    logic_type: Literal["inference", "derivation"] = Field(
        default="inference",
        description="逻辑类型：inference=事实→结论，derivation=结论→解决方案"
    )

    # inference 类型字段
    target_index: Optional[int] = Field(
        default=None,
        description="inference 类型：目标结论在 conclusions[] 中的 0-based 下标"
    )
    supporting_fact_indices: list[int] = Field(
        default_factory=list,
        description=(
            "inference 类型：支撑该结论的已知事实，指向 facts[] 数组的 0-based 下标列表。"
            "这些事实是作者明确援引的、已经发生或可独立核实的证据。"
        )
    )
    assumption_fact_indices: list[int] = Field(
        default_factory=list,
        description=(
            "inference 类型：结论成立所依赖的假设条件，指向 facts[] 数组的 0-based 下标列表。"
            "若无假设条件，填空数组 []。"
        )
    )

    # derivation 类型字段
    solution_index: Optional[int] = Field(
        default=None,
        description="derivation 类型：目标解决方案在 solutions[] 中的 0-based 下标"
    )
    source_conclusion_indices: list[int] = Field(
        default_factory=list,
        description="derivation 类型：推导所基于的结论索引，指向 conclusions[] 数组的 0-based 下标列表"
    )


# ---------------------------------------------------------------------------
# 完整提取结果
# ---------------------------------------------------------------------------


class ExtractionResult(BaseModel):
    is_relevant_content: bool
    skip_reason: Optional[str] = None

    facts: list[ExtractedFact] = Field(default_factory=list)
    conclusions: list[ExtractedConclusion] = Field(default_factory=list)
    solutions: list[ExtractedSolution] = Field(default_factory=list)
    logics: list[ExtractedLogic] = Field(default_factory=list)

    extraction_notes: Optional[str] = None
