# Anchor — 产品需求文档 (PRD)

> 版本：v4.0（三链路 + 六实体 + 显式关系边表）
> 更新：2026-03-05
> 基于实际代码实现编写

---

## 目录

1. [产品定位](#1-产品定位)
2. [系统架构（v4）](#2-系统架构)
3. [数据模型（DB Schema）](#3-数据模型)
4. [三条链路设计](#4-三条链路设计)
5. [六实体提取（v4 提示词）](#5-六实体提取)
6. [Chain 3 验证规则](#6-chain-3-验证规则)
7. [配置与环境](#7-配置与环境)
8. [技术栈](#8-技术栈)
9. [文件结构](#9-文件结构)

---

## 1. 产品定位

Anchor 是一个**多层观点提取与事实验证系统**，专为分析社交媒体（Twitter/X、微博等）上的经济、金融、政治、社会类观点而设计。

**v4 核心改进（相比 v2.2）：**
- **六实体模型**：将旧版 Conclusion（含预测）和 Condition（含假设+隐含）拆分为独立实体，类型更清晰
- **显式关系边表**：用 `relationships` 表替代 Logic 表的 JSON 数组，支持 SQL 直接查询
- **三条独立链路**：Chain1（提取）/ Chain2（作者）/ Chain3（验证）各自独立运行，互不耦合
- **时效作为一等公民**：Prediction 有独立的 `temporal_validity` 字段（has_timeframe / no_timeframe）

---

## 2. 系统架构

```
输入 URL
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│  基础设施（保留不动）                                          │
│  anchor/collector/ — 采集（Twitter/Weibo/URL）                │
│  anchor/llm_client.py — LLM 接口                              │
│  anchor/tracker/web_searcher.py — Tavily 搜索                 │
│  anchor/database/session.py — 数据库 Session 工厂             │
└─────────────────────┬───────────────────────────────────────┘
                      │ RawPost（原始帖子）
                      ▼
┌─────────────────────────────────────────────────────────────┐
│  Chain 1 — 逻辑提炼（chain1_extractor.py）                    │
│  Prompt v4_sixentity（八步 A-H）                              │
│  输出：Fact/Assumption/ImplicitCondition/                     │
│        Conclusion/Prediction/Solution                        │
│  + EntityRelationship 边表                                   │
│  DAG 分析：is_core_conclusion / is_in_cycle                  │
│  时效标注：Prediction.temporal_validity                       │
└─────────────────────┬───────────────────────────────────────┘
                      │                │
                      ▼                ▼
┌────────────────────┐  ┌──────────────────────────────────────┐
│ Chain 2            │  │ Chain 3 — 验证（chain3_verifier.py）  │
│ 作者分析           │  │ Step1: Fact → fact_verdict            │
│ AuthorProfiler +   │  │ Step2: Assumption → assumption_verdict│
│ LLM 立场分析       │  │ Step3: ImplicitCond → implicit_verdict│
│ → AuthorStance     │  │ Step4: 规则推导 Conclusion verdict    │
│   Profile          │  │ Step5: 监控 Prediction verdict        │
└────────────────────┘  └──────────────────────────────────────┘
```

---

## 3. 数据模型

### 基础设施表（保留，不变）

| 表名 | 描述 |
|------|------|
| `author_groups` | 跨平台作者实体 |
| `topics` | 话题 |
| `authors` | 观点作者（含档案字段）|
| `monitored_sources` | 监控源 |
| `raw_posts` | 原始帖子 |

### 六实体表（v4 新/重写）

| 实体 | 表名 | 核心字段 | Chain3 verdict 字段 |
|------|------|----------|---------------------|
| Fact（事实依据）| `facts` | claim, verifiable_statement, temporal_type/note | `fact_verdict`: credible\|vague\|unreliable\|unavailable |
| Assumption（假设条件）| `assumptions` | condition_text, verifiable_statement | `assumption_verdict`: high_probability\|medium_probability\|low_probability\|unavailable |
| ImplicitCondition（隐含条件）| `implicit_conditions` | condition_text, is_obvious_consensus | `implicit_verdict`: consensus\|contested\|false |
| Conclusion（结论）| `conclusions` | claim, author_confidence, is_core_conclusion, is_in_cycle | `conclusion_verdict`: confirmed\|refuted\|partial\|unverifiable\|pending |
| Prediction（预测）| `predictions` | claim, temporal_note, temporal_validity, monitoring_start/end | `prediction_verdict`: pending\|accurate\|directional\|off_target\|wrong |
| Solution（解决方案）| `solutions` | claim, action_type, action_target | 不验证 |

### 关系边表（v4 新增）

```sql
CREATE TABLE relationships (
    id          INTEGER PRIMARY KEY,
    raw_post_id INTEGER REFERENCES raw_posts(id),
    source_type TEXT NOT NULL,  -- fact|assumption|implicit_condition|conclusion|prediction|solution
    source_id   INTEGER NOT NULL,
    target_type TEXT NOT NULL,
    target_id   INTEGER NOT NULL,
    edge_type   TEXT NOT NULL,  -- EdgeType 枚举
    note        TEXT,
    created_at  DATETIME NOT NULL
);
```

**EdgeType 枚举（6种）：**
- `fact_supports_conclusion`
- `assumption_conditions_conclusion`
- `implicit_conditions_conclusion`
- `conclusion_supports_conclusion`
- `conclusion_leads_to_prediction`
- `conclusion_enables_solution`

### 评估与统计表（保留）

| 表名 | 描述 |
|------|------|
| `post_quality_assessments` | 单篇内容质量评估 |
| `author_stance_profiles` | 作者立场档案（Chain2 写入）|
| `author_stats` | 作者综合统计 |

---

## 4. 三条链路设计

### Chain 1 — 逻辑提炼（`anchor/chains/chain1_extractor.py`）

```
URL → process_url() → RawPost
    → Extractor(v4_sixentity).extract() → 六实体 + EntityRelationship 边
    → DAG 分析：is_core_conclusion（无出边到其他结论的结论）
    → DFS 循环检测：is_in_cycle
    → 时效标注：temporal_note 有值 → has_timeframe；否则 no_timeframe
    → 写入 DB
```

**作者确信度（author_confidence）：**
- `certain` / `likely` / `uncertain` / `speculative`
- 从文本措辞中提取（LLM 判断）

### Chain 2 — 作者分析（`anchor/chains/chain2_author.py`）

```
author_id
    → AuthorProfiler().profile()（写 role/expertise/credibility_tier）
    → 读取近期 RawPost（最多10条）
    → LLM 立场分析 → {stance_label, audience, core_message, author_summary}
    → 写入 AuthorStanceProfile
```

**LLM 输出格式（立场分析）：**
```json
{
  "stance_label": "看涨/多头|看跌/空头|中立/客观|警告/防御|批判/质疑|政策倡导|教育/分析",
  "audience": "目标受众（≤40字）",
  "core_message": "核心信息（≤80字）",
  "author_summary": "以...身份，持...立场，向...传达...（≤100字）"
}
```

### Chain 3 — 验证（`anchor/chains/chain3_verifier.py`）

5个子步骤，各自独立可跳过：

| 步骤 | 输入实体 | 输出字段 | 方法 |
|------|---------|---------|------|
| Step 1 | Fact | `fact_verdict` | Tavily + LLM |
| Step 2 | Assumption | `assumption_verdict` | Tavily + LLM |
| Step 3 | ImplicitCondition | `implicit_verdict` | 共识快速通道 or Tavily + LLM |
| Step 4 | Conclusion（读 Relationship 表）| `conclusion_verdict` | 规则推导 |
| Step 5 | Prediction | `prediction_verdict` | 时效检查 + Tavily + LLM |

---

## 5. 六实体提取

### 提示词版本：v4_sixentity（`anchor/extract/prompts/v4_sixentity.py`）

8步（A-H）：

- **Step A**：相关性判断
- **Step B**：提取 Fact（可独立核查的客观陈述）
- **Step C**：提取 Assumption（明确的"如果X则Y"前提）
- **Step D**：提取 ImplicitCondition（推理依赖但未说出的前提）
- **Step E**：提取 Conclusion（对过去/当前状态的判断，**回顾型**）
- **Step F**：提取 Prediction（指向未来，必须提取 temporal_note）
- **Step G**：提取 Solution（具体行动建议）
- **Step H**：建立关系边（6种 EdgeType）

**关键区分规则（结论 vs 预测）：**
- 结论 = 对已发生事件或当前形势的判断（"现在/过去 X 是 Y"）
- 预测 = 明确指向未来（含"将"、"未来"、"会"、"预计"等时态词）
- 预测必须有 temporal_note；无则填 null（Chain1 自动标注 no_timeframe）

---

## 6. Chain 3 验证规则

### Fact verdict 推导

| source_tier | is_vague | fact_verdict |
|-------------|----------|--------------|
| authoritative/mainstream_media/market_data | false | credible |
| authoritative/mainstream_media/market_data | true | vague |
| rumor | - | unreliable |
| no_source | - | unavailable |

### Conclusion verdict 规则推导（读 EntityRelationship 表）

| 条件 | conclusion_verdict |
|------|-------------------|
| 任意 low_probability 假设 | refuted |
| 任意 unreliable 事实 | refuted |
| 全部事实 unavailable（无其他实体） | unverifiable |
| 无任何支撑实体 | pending |
| 全部事实 credible/vague，无异常条件 | confirmed |
| 部分 unavailable 或 contested 隐含条件 | partial |

### Prediction 监控规则

- `temporal_validity=no_timeframe` → 保持 pending，跳过
- `monitoring_end` 未到 → pending，跳过
- `monitoring_end` 已过 或 无 → Tavily + LLM 验证

---

## 7. 配置与环境

```bash
# 必需环境变量
ANTHROPIC_API_KEY=...       # LLM 接口
DATABASE_URL=sqlite+aiosqlite:///./anchor.db   # 数据库

# 可选
TAVILY_API_KEY=...          # 联网搜索（无则降级为纯 LLM）
```

---

## 8. 技术栈

- **Python 3.11+**
- **LLM**：Anthropic Claude（claude-sonnet-4-6 默认）
- **ORM**：SQLModel（SQLAlchemy 异步）
- **DB**：SQLite（开发）/ PostgreSQL（生产可选）
- **搜索**：Tavily Search API
- **采集**：Twitter Syndication API / Weibo / BeautifulSoup

---

## 9. 文件结构

```
anchor/
├── models.py                    # 数据模型（六实体 + EntityRelationship + 基础设施）
├── llm_client.py                # LLM 接口
├── config.py                    # 配置
├── chains/                      # 三条链路编排（入口）
│   ├── chain1_extractor.py      # Chain 1 — 逻辑提炼
│   ├── chain2_author.py         # Chain 2 — 作者分析
│   └── chain3_verifier.py       # Chain 3 — 验证
├── collect/                     # 数据采集（Twitter/Weibo/URL）
│   ├── input_handler.py
│   ├── twitter.py / weibo.py / youtube.py / rss.py
│   ├── media_describer.py
│   └── content_duplicate_checker.py
├── extract/                     # 六实体提取引擎（Chain 1 使用）
│   ├── extractor.py             # Extractor 主类
│   ├── schemas.py               # Pydantic schema（六实体 + 关系边）
│   └── prompts/
│       ├── v4_sixentity.py      # 八步提示词（DEFAULT）
│       └── v3_unified.py / ...  # 历史版本保留
├── verify/                      # 验证工具（Chain 2/3 使用）
│   ├── author_profiler.py       # 作者档案分析
│   ├── web_searcher.py          # Tavily 搜索
│   ├── author_group_matcher.py  # 跨平台作者匹配
│   └── _deprecated/             # 归档旧流水线（v2.2）
├── datasources/                 # FRED/BLS/IMF 等数据源
└── database/
    └── session.py               # 数据库 Session 工厂

run_v4_test.py                   # v4 端到端测试脚本
```

---

> 文档状态：与实际代码同步（v4.0）
