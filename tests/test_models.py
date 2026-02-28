"""核心模型结构测试

验证 Prediction / Interpretation / Sentiment 三类观点及 Condition 可以正常实例化，
字段类型和枚举值符合预期。不依赖数据库连接。
"""

from datetime import datetime

from anchor.models import (
    Author,
    AuthorRelation,
    Condition,
    ConditionStatus,
    EvidenceAccuracy,
    Interpretation,
    LogicCompleteness,
    Prediction,
    PredictionStatus,
    RawPost,
    Sentiment,
    Topic,
)


def _make_topic() -> Topic:
    return Topic(name="美联储2025降息预期", description="市场对2025年美联储降息节奏的预期")


def _make_author() -> Author:
    return Author(name="测试用户", platform="twitter", platform_id="12345")


def test_topic_instantiation():
    t = _make_topic()
    assert t.name == "美联储2025降息预期"
    assert t.id is None  # 未入库时无 ID


def test_author_instantiation():
    a = _make_author()
    assert a.platform == "twitter"


def test_condition_defaults():
    c = Condition(abstract_expression="美联储今年大概率会降息")
    assert c.is_verifiable is False
    assert c.status == ConditionStatus.PENDING
    assert c.verifiable_expression is None


def test_condition_after_layer1_processing():
    c = Condition(
        abstract_expression="美联储今年大概率会降息",
        verifiable_expression="2025年内 Fed Funds Rate 下调至少 25bp",
        is_verifiable=True,
        status=ConditionStatus.PENDING,
    )
    assert c.is_verifiable is True


def test_condition_verified():
    c = Condition(
        abstract_expression="美联储今年大概率会降息",
        verifiable_expression="2025年内 Fed Funds Rate 下调至少 25bp",
        is_verifiable=True,
        status=ConditionStatus.VERIFIED_TRUE,
        verified_at=datetime(2025, 9, 18),
        verification_evidence="2025-09-18 FOMC 会议决议：下调利率 25bp",
    )
    assert c.status == ConditionStatus.VERIFIED_TRUE


def test_prediction_default_status():
    p = Prediction(
        topic_id=1,
        author_id=1,
        claim="标普500指数将在2025年底突破6000点",
        summary="某分析师预测标普500将于2025年底突破6000点，依赖美联储降息和企业盈利增长。",
        valid_until=datetime(2025, 12, 31),
        source_url="https://twitter.com/example/status/1",
        source_platform="twitter",
        posted_at=datetime(2025, 1, 15),
    )
    assert p.status == PredictionStatus.PENDING
    assert p.pending_condition_id is None


def test_interpretation_quality_fields():
    i = Interpretation(
        topic_id=1,
        author_id=1,
        conclusion="DeepSeek的发布将导致英伟达AI芯片需求下滑",
        summary="分析师认为DeepSeek的低成本训练方案将削减对高端GPU的需求，逻辑存在跳跃。",
        evidence_accuracy=EvidenceAccuracy.MEDIUM,
        logic_completeness=LogicCompleteness.WEAK,
        logic_note="未考虑AI应用规模扩展带来的额外算力需求",
        source_url="https://weibo.com/example/123",
        source_platform="weibo",
        posted_at=datetime(2025, 1, 27),
    )
    assert i.evidence_accuracy == EvidenceAccuracy.MEDIUM
    assert i.logic_completeness == LogicCompleteness.WEAK


def test_sentiment_fields():
    s = Sentiment(
        topic_id=1,
        author_id=1,
        summary="持仓投资者对DeepSeek发布后英伟达股价暴跌表达强烈恐慌情绪。",
        trigger_event="DeepSeek R1 发布，英伟达单日跌幅约17%",
        trigger_event_time=datetime(2025, 1, 27),
        emotion_cause="重仓英伟达，账面亏损超过20%",
        author_relation=AuthorRelation.DIRECT,
        author_relation_note="持有英伟达股票",
        emotion_label="恐慌",
        emotion_intensity=0.9,
        source_url="https://twitter.com/example/status/2",
        source_platform="twitter",
        posted_at=datetime(2025, 1, 27),
    )
    assert s.author_relation == AuthorRelation.DIRECT
    assert s.emotion_intensity == 0.9


def test_raw_post_default_state():
    rp = RawPost(
        source="twitter",
        external_id="tweet_123",
        content="我认为美联储今年必然降息，AI需求将推动纳斯达克新高",
        author_name="某分析师",
        url="https://twitter.com/example/status/3",
        posted_at=datetime(2025, 2, 1),
    )
    assert rp.is_processed is False
    assert rp.context_fetched is False
