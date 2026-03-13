# Anchor — 产品需求文档 (PRD)

> 版本：v8.0
> 更新：2026-03-12
> 基于实际代码实现编写

---

## 目录

**产品篇**

1. [产品定位与愿景](#1-产品定位与愿景)
2. [用户画像](#2-用户画像)
3. [核心使用场景](#3-核心使用场景)
4. [功能概览](#4-功能概览)
5. [输出示例](#5-输出示例)

**技术篇**

6. [系统架构](#6-系统架构)
    - [6a. 下游系统对接](#6a-下游系统对接)
7. [数据模型](#7-数据模型)
8. [三条链路设计](#8-三条链路设计)
9. [内容路由逻辑](#9-内容路由逻辑)
10. [统一提取管线（6 领域 × 2-call）](#10-统一提取管线6-领域--2-call)
11. [6 个内容领域](#11-6-个内容领域)
12. [事实验证规则](#12-事实验证规则)
13. [监控流水线](#13-监控流水线)
14. [配置与环境](#14-配置与环境)
15. [技术栈](#15-技术栈)
16. [文件结构](#16-文件结构)

**附录**

17. [系统局限与边界](#17-系统局限与边界)
18. [路线图](#18-路线图)

---

# 产品篇

## 1. 产品定位与愿景

**让每一篇财经分析、政策文件、技术论文、公司财报，都能被系统性地读懂并转化为结构化知识。**

Anchor 是**理解模型**——一个多模式的信息提取与事实验证引擎。它从非结构化文本中提取**节点**（领域特定的结构化实体）和**边**（节点间的关系），并通过事实核查验证可信度。

Anchor 是三层投资分析系统的第一层：

```
文章/文件 → [Anchor 理解模型] → 节点 + 边（结构化知识图）
                                      ↓
         市场数据 → [Axion 产业模型]  → 价格预测
                                      ↓
         当前定价 → [Polaris 量化投资] → 投资决策
```

**Anchor 的职责边界**：只做信息理解和结构化提取，不做世界建模、价格预测或投资决策。产出的节点和边供下游 Axion 消费。

**历史演进**：
- v1-v5：观点提取 + 事实验证系统（七实体 DAG + 事实验证）
- v6：Top-down 提取 + 产业链/论文/财报多模式 + 关系图
- v7：明确定位为理解模型，与 Axion（产业模型）和 Polaris（量化投资）分离
- **v8（本版本）：通用节点+边架构 — 6 个领域 × 统一 2-call 管线，替代所有专用管线**

**v8 核心改进（相比 v7）：**
- **统一数据模型**：所有领域共用 `Node` + `Edge` 两张表，替代 7 实体表 + 5 专用表
- **领域特定节点类型**：每个领域定义自己的节点类型（如政策域 9 种、产业域 6 种），替代全局七实体分类
- **统一 2-call 管线**：所有 6 个领域使用同一提取管线（Call 1 提取节点 → Call 2 发现边 + 摘要），只换提示词
- **新增 3 个领域**：期货 (futures)、公司 (company)、专家分析 (expert)，与原有政策/产业/技术并列
- **简化路由**：第三方分析统一进入 expert 域，一手信息按 domain 分发

---

## 2. 用户画像

### 2.1 政策研究员

**痛点**：每年政府工作报告数万字，需要理解政策意图、战略优先级、执行保障。

**Anchor 的解法**：
- 自动提取政策节点（主旨/目标/战略/战术/资源/考核/约束/反馈/外溢 9 种类型）
- 建立节点间关系（目标→战略→战术→资源 的传导链）
- 事实验证追踪政策执行反馈

### 2.2 宏观投资分析师

**痛点**：需要快速判断一篇财经 KOL 的观点是否有事实支撑，判断和预测是否合理。

**Anchor 的解法**：
- 专家分析域提取 4 种节点（事实/判断/预测/建议），构建论证结构
- 事实核查：搜索验证事实类节点的可信度
- 判断推导：综合支撑边推导判断类节点的可信度

### 2.3 产业投资者

**痛点**：需要从大量研报中快速提取产业格局、驱动因素、趋势和投资标的。

**Anchor 的解法**：
- 产业域提取 6 种节点（格局/驱动/趋势/资金流向/机会威胁/标的）
- 技术域提取 5 种节点（问题/方案/效果性能/局限场景/玩家）
- 公司域提取 5 种节点（表现/归因/指引/风险/叙事）
- 所有结构化节点供下游 Axion 产业模型消费

### 2.4 期货交易员

**痛点**：需要快速理解供需平衡变化、库存异动、宏观冲击对商品价格的影响。

**Anchor 的解法**：
- 期货域提取 6 种节点（供给/需求/库存/头寸/冲击/缺口）
- 建立节点间关系（冲击→供给→缺口 的传导链）

---

## 3. 核心使用场景

### 场景 A：专家分析深度解构

**用户操作**：提交一条 Twitter/X 长推文 URL，作者是某知名宏观经济评论员

**Anchor 处理流程**：

```
1. 采集 RawPost
2. 通用判断：domain=产业, nature=第三方分析 → content_mode=expert
3. 内容提取（expert 域，2-call 管线）：
   - Call 1：提取事实/判断/预测/建议节点
   - Call 2：发现节点间关系 + 生成摘要
4. 事实验证：
   - 事实节点 → Serper + LLM 核查可信度
   - 判断节点 → 从支撑边推导可信度
   - 预测节点 → 时间窗口监控
```

**用户价值**：快速识别哪些判断建立在不可信事实之上，避免被误导性观点影响投资决策。

---

### 场景 B：政策文件结构化

**用户操作**：提交政府工作报告 URL

**Anchor 处理流程**：

```
1. 采集 RawPost
2. 通用判断：domain=政策, nature=一手信息 → content_mode=policy
3. 内容提取（policy 域，2-call 管线）：
   - Call 1：提取主旨/目标/战略/战术/资源/考核/约束/反馈/外溢节点
   - Call 2：发现节点间关系（目标→战略→战术）+ 生成摘要
4. 事实验证：
   - 反馈节点 → 执行追踪（搜索最新执行新闻）
```

**用户价值**：快速理解政策意图和执行路径，无需逐字阅读数万字报告。

---

### 场景 C：公司财报分析

**用户操作**：提交 SEC 10-K URL

**Anchor 处理流程**：

```
1. 采集 RawPost
2. 通用判断：domain=公司, nature=一手信息 → content_mode=company
3. 内容提取（company 域，2-call 管线）：
   - Call 1：提取表现/归因/指引/风险/叙事节点
   - Call 2：发现节点间关系 + 生成摘要
4. 事实验证：
   - 表现节点 → 核实财务数据
```

---

## 4. 功能概览

| 功能 | 说明 | 适用领域 |
|------|------|---------|
| 智能采集 | Twitter/X、微博、YouTube、Bilibili、Truth Social、通用 Web | 全部 |
| 订阅监控 | 从 sources.yaml 批量监控 RSS/Substack/YouTube/Bilibili 订阅源 | 全部 |
| 内容质量过滤 | 付费墙检测、视频时长过滤（<3分钟跳过）、文章字数过滤（<200字跳过） | 全部 |
| 2D 内容分类 | content_domain(5) × content_nature(2) → 6 种 content_mode | 全部 |
| 实际发言人识别 | 识别个人品牌账号/转载频道背后的真实发言人 | 全部 |
| 发文机关识别 | 识别政策类文件的发布机关及其权威级别 | 政策 |
| 统一节点提取 | 每个领域的特定节点类型（4-9 种） | 全部 |
| 关系边发现 | LLM 全局视角发现节点间关系 | 全部 |
| 事实核查 | 网络检索验证，输出可信度判断 | expert, company |
| 作者档案 | 信誉等级（1-5）、历史记录 | 全部 |

---

## 5. 输出示例

### 5.1 专家分析域输出示例

```
domain=expert  5 nodes  4 edges

[事实] 2025年Q4 CPI同比 +0.1%
  → 支撑 → [判断] 通缩压力持续存在
    → 支撑 → [判断] 政策宽松周期将延长
      → 推导 → [预测] 2026年降息2次

[建议] 增配长久期国债

摘要: Ray Dalio 认为当前通缩压力下政策宽松周期将延长，建议增配长久期国债。
```

### 5.2 政策域输出示例

```
domain=policy  8 nodes  6 edges

[主旨] 积极财政政策
  → 目标 → [目标] 扩大内需，维持合理增长
  → 实现 → [战略] 提升赤字率
    → 落地 → [战术] 赤字率提至4%
    → 保障 → [资源] 2万亿超长期特别国债
  → 考核 → [考核] GDP增长5%
  → 约束 → [约束] 地方债务风险管控

[反馈] 首批国债已招标发行（执行中）
```

### 5.3 产业域输出示例

```
domain=industry  6 nodes  4 edges

[格局] AI芯片市场NVIDIA占据90%+份额
[驱动] 大模型训练算力需求指数级增长
  → 推动 → [趋势] 先进封装成为产能瓶颈
    → 创造 → [机会威胁] CoWoS产能争夺加剧
[标的] 台积电（先进封装龙头）
[资金流向] AI基础设施资本开支持续上升
```

---

# 技术篇

## 6. 系统架构

```
输入 URL / 文档内容
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  采集层（anchor/collect/）                                          │
│  支持：Twitter/X · 微博 · YouTube · Truth Social · RSS · 通用 Web  │
│  输出：RawPost（原始帖子/文档）存入 DB                               │
└──────────────────────────────┬───────────────────────────────────┘
                               │
           ┌───────────────────▼──────────────────────┐
           │    通用判断（前置）                           │
           │    assess_post() — 内容分类 + 发文机关        │
           │    → content_domain / content_nature        │
           │    → resolve_content_mode() → 6 种模式       │
           └────────────────────┬─────────────────────┘
                                │
              ┌─────────────────▼─────────────────────┐
              │     统一 2-call 提取管线                  │
              │     extract_generic(content_mode)       │
              │                                         │
              │  Call 1: 提取节点（领域特定节点类型）        │
              │  Call 2: 发现边 + 生成摘要                 │
              │                                         │
              │  → Node 表 + Edge 表                     │
              │                                         │
              │  支持 6 个领域：                           │
              │  policy | industry | technology          │
              │  futures | company | expert              │
              └────────────────────┬──────────────────┘
                                   │
           ┌───────────────────────▼──────────────────────┐
           │              通用判断                           │
           │  作者档案（AuthorProfiler）                      │
           │  + 利益冲突检测（has_conflict）                   │
           │  credibility_tier 1-5 / assessment_summary     │
           └───────────────────────┬──────────────────────┘
                                   │
           ┌───────────────────────▼──────────────────────┐
           │              事实验证                           │
           │  注册表式验证：(domain, node_type) → 验证函数    │
           │  事实类 → Serper + LLM 核查                    │
           │  判断类 → 支撑边推导                            │
           │  预测类 → 时间窗口 + Serper + LLM              │
           └──────────────────────────────────────────────┘
```

---

## 6a. 下游系统对接

Anchor 是三层投资分析系统的信息理解层，产出结构化节点和边供下游消费：

```
┌─────────────────────────────────────────────────────────┐
│  Anchor（理解模型）                                        │
│  文章/文件 → Node + Edge（6 个领域的结构化知识图）           │
│                                                           │
│  产出：                                                    │
│  ├── policy 节点+边（政策文件）                              │
│  ├── industry 节点+边（产业研究）                            │
│  ├── technology 节点+边（技术论文）                          │
│  ├── futures 节点+边（期货分析）                             │
│  ├── company 节点+边（公司财报）                             │
│  ├── expert 节点+边（专家/第三方分析）                       │
│  └── AuthorProfile（作者档案）                               │
├─────────────────────────────────────────────────────────┤
│  Axion（产业模型）— 独立项目                                 │
│  Anchor 节点+边 + 市场数据 → 世界格局建模 → 价格预测         │
├─────────────────────────────────────────────────────────┤
│  Polaris（量化投资）— 独立项目                               │
│  Axion 预测 + 当前定价 → 投资决策                            │
└─────────────────────────────────────────────────────────┘
```

**Anchor 的输出接口**：所有节点和边通过 SQLite DB 暴露（`nodes` + `edges` 表），Axion 直接读取。

详见：
- Axion PRD — 产业模型架构（五层：周期/状态/力量/传导/学习）
- Polaris PRD — 量化投资系统

---

## 7. 数据模型

### 7.1 基础设施表

| 表名 | 描述 | 关键字段 |
|------|------|---------|
| `authors` | 观点作者档案 | name, platform, platform_id, role, expertise_areas, known_biases, credibility_tier(1-5), profile_note(≤80字), situation_note(≤150字) |
| `raw_posts` | 原始帖子/文档 | source, external_id, content, enriched_content, posted_at, is_processed, **content_domain**(政策\|产业\|公司\|期货\|技术), **content_nature**(一手信息\|第三方分析), **assessment_summary**(≤80字), **has_conflict**(bool), **conflict_note**(≤80字), content_type(过渡兼容), issuing_authority, authority_level, content_summary, notion_page_id |
| `monitored_sources` | 监控源 | url, source_type, platform, fetch_interval_minutes, last_fetched_at |
| `author_groups` | 跨平台作者实体 | canonical_name, canonical_role |
| `topics` | 话题标签 | name, description, tags |

### 7.2 Node 表（统一节点）

```sql
nodes (
    id              INTEGER PRIMARY KEY,
    raw_post_id     INTEGER FK → raw_posts.id,  -- 来源文章
    domain          VARCHAR INDEX,               -- policy|industry|technology|futures|company|expert
    node_type       VARCHAR INDEX,               -- 领域内的节点类型（见 7.4 领域节点类型注册表）
    claim           TEXT,                         -- 主要内容（≤150字）
    summary         VARCHAR,                     -- 短摘要（≤15字）
    metadata_json   TEXT,                        -- 领域特定扩展数据（JSON）
    verdict         VARCHAR,                     -- 验证结论
    verdict_evidence TEXT,                       -- 验证证据
    verdict_verified_at DATETIME,                -- 验证时间
    created_at      DATETIME
)
```

### 7.3 Edge 表（统一边）

```sql
edges (
    id                INTEGER PRIMARY KEY,
    source_node_id    INTEGER FK → nodes.id,     -- 源节点
    target_node_id    INTEGER FK → nodes.id,     -- 目标节点
    edge_type         VARCHAR DEFAULT 'connected', -- 关系类型
    note              VARCHAR,                   -- ≤80字说明
    added_by_post_id  INTEGER FK → raw_posts.id, -- 来源文章
    created_at        DATETIME
)
```

### 7.4 领域节点类型注册表

| 领域 | content_mode | 节点类型 |
|------|-------------|---------|
| 政策（含地缘） | policy | 主旨 · 目标 · 战略 · 战术 · 资源 · 考核 · 约束 · 反馈 · 外溢 (9) |
| 产业 | industry | 格局 · 驱动 · 趋势 · 技术路线 · 资金流向 · 机会威胁 · 标的 (7) |
| 技术 | technology | 问题 · 方案 · 效果性能 · 局限场景 · 玩家 (5) |
| 期货 | futures | 供给 · 需求 · 库存 · 头寸 · 冲击 · 缺口 (6) |
| 公司 | company | 表现 · 归因 · 指引 · 风险 · 叙事 (5) |
| 专家分析 | expert | 事实 · 判断 · 预测 · 建议 (4) |

```python
# anchor/models.py
DOMAIN_NODE_TYPES = {
    "policy":     ["主旨", "目标", "战略", "战术", "资源", "考核", "约束", "反馈", "外溢"],
    "industry":   ["格局", "驱动", "趋势", "技术路线", "资金流向", "机会威胁", "标的"],
    "technology": ["问题", "方案", "效果性能", "局限场景", "玩家"],
    "futures":    ["供给", "需求", "库存", "头寸", "冲击", "缺口"],
    "company":    ["表现", "归因", "指引", "风险", "叙事"],
    "expert":     ["事实", "判断", "预测", "建议"],
}
```

### 7.5 评估与统计表

| 表名 | 描述 |
|------|------|
| `author_stance_profiles` | 作者立场档案（保留只读，不再写入新数据） |
| `post_quality_assessments` | 单篇内容质量评估 |
| `author_stats` | 作者综合统计（准确率、可信度评分） |

### 7.6 旧表（v7 遗留，保留只读）

以下表在 v8 中不再写入新数据，DB 中保留供历史数据查询：

- `facts`, `assumptions`, `implicit_conditions`, `conclusions`, `predictions`, `solutions`, `theories` — 旧七实体表
- `relationships` — 旧关系边表
- `policy_themes`, `policy_items` — 旧政策专用表
- `issues`, `tech_routes`, `metrics` — 旧产业专用表
- `paper_analyses` — 旧论文分析表
- `earnings_analyses` — 旧财报分析表

---

## 8. 三条链路设计

### 内容提取（`anchor/chains/content_extraction.py`）

**职责**：URL → 节点 + 边 → DB

**流程：**
```
URL
 → process_url()           — 采集 RawPost（去重、存库）
 → assess_post()           — 通用判断前置分类（确定 content_mode）
 → Extractor.extract()     — 统一 2-call 管线（extract_generic）
 → 返回 {nodes, edges, summary}
```

**内容提取在 RawPost 上写入的字段：**
- `content_summary`（Call 2 摘要）
- `is_processed`, `processed_at`

**内容提取不写的字段（由通用判断写）：**
- `content_domain`, `content_nature`, `assessment_summary`, `has_conflict`, `conflict_note`
- `content_type`（过渡兼容）, `issuing_authority`, `authority_level`

---

### 通用判断（`anchor/chains/general_assessment.py`）

**职责**：理解"谁说的"和"说的什么类型" + 利益冲突检测

**两步：**

| 步骤 | 输入 | 输出 | 写入表 |
|------|------|------|-------|
| Step 1 AuthorProfiler | author_id | role, expertise, credibility_tier, situation_note | authors |
| Step 2 内容分类 + 摘要 + 利益冲突 | RawPost | content_domain, content_nature, assessment_summary, has_conflict, conflict_note, content_type（过渡兼容）, real_author_name, issuing_authority, authority_level | raw_posts |

**2D 分类（领域 × 性质）：**

- **content_domain（5种）**：政策 | 产业 | 公司 | 期货 | 技术
- **content_nature（2种）**：一手信息 | 第三方分析
- **content_type（过渡兼容）**：财经分析 | 市场动向 | 产业链研究 | 公司调研 | 技术论文 | 公司财报 | 政策解读

**摘要（assessment_summary）**：什么人在干什么事（≤80字描述性句子）

**利益冲突（has_conflict + conflict_note）**：
- has_conflict=true：推销产品/服务/付费内容、持有相关头寸、受雇于分析对象
- has_conflict=false：独立分析、学术研究、官方信息发布

**实际发言人识别（real_author_name）：**
- 标题点名某人（如"付鹏最新分析"）→ 填该人姓名
- 个人品牌账号（如"付鹏的财经世界"）→ 提取真实发言人
- 转载/解读账号 → 找被采访/被分析的人；无法判断填 null

**作者可信度分级（credibility_tier）：**

| 等级 | 描述 | 示例 |
|------|------|------|
| 1 | 顶级权威 | 现任央行行长、诺贝尔经济学奖得主、万亿基金创始人 |
| 2 | 行业专家 | 知名对冲基金经理、首席经济学家、前国家领导人 |
| 3 | 知名评论员 | 财经媒体主播、有记录的分析师 |
| 4 | 一般媒体/KOL | 社交媒体账号、无明显专业背景 |
| 5 | 未知 | 无可检索背景信息 |

---

### 事实验证（`anchor/chains/fact_verification.py`）

**职责**：验证节点可信度

**验证门控（v8）**：
- `content_nature = "一手信息"` → 跳过验证（一手信息不验证）
- `content_nature = "第三方分析"` 或旧数据（无 content_nature）→ 正常执行验证

**注册表式验证**：按 `(domain, node_type)` 查找对应验证函数：

| (domain, node_type) | 验证方法 | verdict 枚举 |
|---------------------|---------|-------------|
| (expert, 事实) | Serper + LLM 搜索核实 | credible \| vague \| unreliable \| unavailable |
| (expert, 判断) | 从支撑边推导 | confirmed \| refuted \| partial \| pending |
| (expert, 预测) | 时间窗口 + Serper + LLM | pending \| accurate \| off_target \| wrong |
| (company, 表现) | Serper + LLM 核实财务数据 | credible \| vague \| unreliable \| unavailable |
| (policy, 反馈) | 搜索执行新闻 + LLM | implemented \| in_progress \| stalled \| not_started \| unknown |

未在注册表中的 (domain, node_type) 组合不做验证。

---

## 9. 内容路由逻辑

v8 采用统一路由函数 `resolve_content_mode(domain, nature, content_type)`，所有调用点共享：

```python
# anchor/chains/general_assessment.py
def resolve_content_mode(domain, nature, content_type=None) -> str:
    if domain and nature:
        if nature == "第三方分析": return "expert"
        if domain == "政策": return "policy"
        if domain == "产业": return "industry"
        if domain == "技术": return "technology"
        if domain == "期货": return "futures"
        if domain == "公司": return "company"
        return "expert"  # fallback
    # 旧数据降级
    return "expert"
```

**路由表：**

| domain | nature | content_mode | 说明 |
|--------|--------|-------------|------|
| 任意 | 第三方分析 | expert | 第三方分析统一进入专家域 |
| 政策 | 一手信息 | policy | 政策原文 → 9 种政策节点 |
| 产业 | 一手信息 | industry | 产业报告 → 6 种产业节点 |
| 技术 | 一手信息 | technology | 技术论文 → 5 种技术节点 |
| 期货 | 一手信息 | futures | 期货报告 → 6 种期货节点 |
| 公司 | 一手信息 | company | 公司财报 → 5 种公司节点 |

**路由影响：**

| 维度 | 各 content_mode |
|------|----------------|
| 提取管线 | 统一 2-call（extract_generic），只换领域提示词 |
| 节点类型 | 由 DOMAIN_NODE_TYPES[content_mode] 决定 |
| 事实验证 | 由 VERIFIABLE_TYPES 注册表决定 |
| 验证门控 | content_nature="一手信息" → 跳过 |

**监控流水线内容过滤**（`anchor monitor`）：
- 视频 < 180 秒 → 跳过
- 文章正文 < 200 字 → 跳过
- 付费墙检测命中 → 跳过

---

## 10. 统一提取管线（6 领域 × 2-call）

### 10.1 管线架构

所有 6 个领域使用同一管线 `extract_generic()`，只换提示词。

管线已拆分为 **compute + write 两阶段**，支持并发提取：

```
extract_generic_compute(content, platform, author, today, domain)
  │
  ├── Call 1: 提取节点（纯 LLM，可并发）
  │   输入: content + domain + 领域节点类型定义
  │   输出: NodeExtractionResult → valid_nodes
  │   → 验证 node_type ∈ DOMAIN_NODE_TYPES[domain]
  │
  ├── Call 2: 发现边 + 生成摘要（纯 LLM，可并发）
  │   输入: content + Call 1 的节点列表
  │   输出: EdgeExtractionResult → edge_results + summary
  │
  └── 返回: ExtractionComputeResult（无 DB 操作）

extract_generic_write(raw_post, session, domain, compute_result)
  │
  ├── 清除旧数据：DELETE Node/Edge WHERE raw_post_id = ?
  ├── 写入 Node 表 + canonical_node_id 初始化
  ├── 写入 Edge 表
  ├── 更新 RawPost.content_summary
  │
  └── 返回: {is_relevant_content, nodes, edges, summary, skip_reason}

extract_generic() — 向后兼容包装，串行调用 compute + write
```

### 10.2 Pydantic Schema

```python
# anchor/extract/schemas/nodes.py
class ExtractedNode(BaseModel):
    temp_id: str           # "n0", "n1", ...
    node_type: str         # 必须属于该领域的合法类型
    claim: str             # ≤150字
    summary: str           # ≤15字
    metadata: dict | None = None

class NodeExtractionResult(BaseModel):
    is_relevant_content: bool = True
    skip_reason: str | None = None
    nodes: list[ExtractedNode] = []

class ExtractedEdge(BaseModel):
    source_id: str         # temp_id 引用
    target_id: str
    note: str | None = None

class EdgeExtractionResult(BaseModel):
    edges: list[ExtractedEdge] = []
    summary: str | None = None
```

### 10.3 提示词设计

每个领域一个提示词文件（`anchor/extract/prompts/domains/{domain}.py`），包含：

| 组件 | 说明 |
|------|------|
| `NODE_TYPE_DESCRIPTIONS` | dict: 节点类型 → 定义 + 提取指导（每类 100+ 字） |
| `SYSTEM_CALL1` | 节点提取系统提示 |
| `SYSTEM_CALL2` | 边发现系统提示 |
| `build_user_message_call1(content, platform, author, today)` | Call 1 用户消息构建器 |
| `build_user_message_call2(content, nodes_json)` | Call 2 用户消息构建器 |

共享模板在 `anchor/extract/prompts/domains/_base.py` 中定义。

### 10.4 arXiv URL 处理

arXiv URL 自动重定向到 ar5iv（HTML 渲染版本）：
- `https://arxiv.org/abs/2501.12948` → `https://ar5iv.labs.arxiv.org/html/2501.12948`
- `https://arxiv.org/pdf/2501.12948v2` → `https://ar5iv.labs.arxiv.org/html/2501.12948v2`

---

## 11. 6 个内容领域

### 11.1 政策域 (policy)

**适用内容**：政府工作报告、政策文件、法规、地缘新闻

**9 种节点类型**：

| 节点类型 | 含义 |
|---------|------|
| 主旨 | 政策大方向或核心关切 |
| 目标 | 具体想要达成的结果（含量化指标） |
| 战略 | 实现目标的整体路径或方法论 |
| 战术 | 具体措施、项目、行动 |
| 资源 | 资金、人力、制度等投入保障 |
| 考核 | 评估标准、KPI、验收条件 |
| 约束 | 法律红线、政治底线、国际承诺 |
| 反馈 | 执行效果、落地情况、社会反应 |
| 外溢 | 对其他领域/国家的连带影响 |

### 11.2 产业域 (industry)

**适用内容**：产业研报、行业分析（一手信息）

**6 种节点类型**：

| 节点类型 | 含义 |
|---------|------|
| 格局 | 市场结构、竞争态势、份额分布 |
| 驱动 | 推动行业变化的核心因素 |
| 趋势 | 正在发生或即将发生的方向性变化 |
| 资金流向 | 投资、融资、资本开支的方向 |
| 机会威胁 | 潜在的投资机会或行业风险 |
| 标的 | 具体公司、产品、技术的投资标的 |

### 11.3 技术域 (technology)

**适用内容**：技术论文、专利文献（一手信息）

**5 种节点类型**：

| 节点类型 | 含义 |
|---------|------|
| 问题 | 研究问题、已有方案不足 |
| 方案 | 提出的技术方案或方法 |
| 效果性能 | 实验结果、性能指标 |
| 局限场景 | 局限性、适用场景 |
| 玩家 | 关键研究机构或企业 |

### 11.4 期货域 (futures)

**适用内容**：大宗商品报告、供需分析（一手信息）

**6 种节点类型**：

| 节点类型 | 含义 |
|---------|------|
| 供给 | 产量、产能、开工率 |
| 需求 | 消费量、下游需求变化 |
| 库存 | 库存水平、周转天数 |
| 头寸 | 持仓结构、多空比 |
| 冲击 | 突发事件对供需的影响 |
| 缺口 | 供需缺口、预期平衡点 |

### 11.5 公司域 (company)

**适用内容**：SEC 10-K/10-Q、CSRC 年报、IFRS 年报（一手信息）

**5 种节点类型**：

| 节点类型 | 含义 |
|---------|------|
| 表现 | 财务指标、经营数据 |
| 归因 | 业绩变化的原因分析 |
| 指引 | 管理层前瞻性指引 |
| 风险 | 披露的风险因素 |
| 叙事 | 管理层叙事、战略定位 |

### 11.6 专家分析域 (expert)

**适用内容**：所有第三方分析（财经评论、研报解读、KOL 观点），不区分 domain

**4 种节点类型**：

| 节点类型 | 含义 |
|---------|------|
| 事实 | 有外部来源的事件/数据/现象 |
| 判断 | 作者的解读、归因、结论 |
| 预测 | 指向未来的预期 |
| 建议 | 具体行动建议 |

---

## 12. 事实验证规则

### 验证注册表

```python
VERIFIABLE_TYPES = {
    ("expert", "事实"):   verify_fact,       # Serper + LLM 搜索核实
    ("expert", "判断"):   derive_verdict,    # 从支撑边推导
    ("expert", "预测"):   monitor_prediction,# 时间窗口 + Serper + LLM
    ("company", "表现"):  verify_fact,       # 财务数据核实
    ("policy", "反馈"):   track_execution,   # 执行追踪
}
```

### 事实核查（verify_fact）

| 搜索结果 | verdict |
|---------|---------|
| 权威来源确认 | credible |
| 信息模糊或部分确认 | vague |
| 与事实矛盾 | unreliable |
| 无法找到相关信息 | unavailable |

### 判断推导（derive_verdict）

从支撑该判断的所有边的源节点 verdict 推导：

| 条件 | verdict |
|------|---------|
| 有 unreliable 源 | refuted |
| 全部源 credible | confirmed |
| 部分 unavailable | partial |
| 无支撑边 | pending |

### 预测监控（monitor_prediction）

| verdict | 含义 |
|---------|------|
| pending | 尚未到验证时间 |
| accurate | 预测准确 |
| off_target | 方向正确但幅度偏差大 |
| wrong | 预测错误 |

### 执行追踪（track_execution）

仅用于 policy 域的"反馈"节点：

| verdict | 含义 |
|---------|------|
| implemented | 已落地 |
| in_progress | 推进中 |
| stalled | 受阻 |
| not_started | 未启动 |
| unknown | 信息不足 |

---

## 13. 监控流水线

### 13.1 架构概览（`anchor monitor`）

```
sources.yaml
    │
    ▼
iter_fetchable_sources()    — 遍历 authors + channels，过滤 accessible=true
    │
    ▼
feed_fetcher.fetch_source() — 平台专属抓取（RSS/YouTube/Bilibili/Weibo）
    │
    ▼ 去重（对比已处理 URL 集合）
    │
    ▼ run_pipeline(url)
      ├── process_url()         — 采集 RawPost
      ├── 内容质量过滤            — 见下方
      ├── run_assessment()       — 内容分类 + 作者档案
      ├── Extractor.extract()   — 统一 2-call 管线
      └── sync_post_to_notion() — Notion 同步
```

### 13.2 内容质量过滤规则

| 条件 | 结果 |
|------|------|
| `raw_metadata.is_video_only=True` + youtube_redirect 存在 | 递归改抓 YouTube 链接 |
| `raw_metadata.is_video_only=True` + 无重定向 | 跳过（video_only） |
| 视频时长 `duration_s < 180` 秒 | 跳过（video_short） |
| 文章正文 < 200 字 | 跳过（text_short） |
| 付费墙正则匹配命中 | 跳过（paywall_skip） |

### 13.3 feed_fetcher 支持的平台（`anchor/monitor/feed_fetcher.py`）

| 平台 | 方法 |
|------|------|
| RSS / Atom（Substack、Project Syndicate、IMF 等） | feedparser |
| YouTube 频道 | yt-dlp 平铺视频列表 |
| Bilibili 空间 | 官方 API |
| Weibo 用户 | 公开 HTML 抓取 |
| Twitter / LinkedIn | 暂不支持（跳过，需人工） |

### 13.4 源质量休眠机制

当一个订阅源连续 **365 天** 没有产出有效长内容时，系统自动将其标记为「休眠」并在后续运行中跳过。

### 13.5 命令行用法

```bash
anchor monitor                             # 跑全部来源
anchor monitor --dry-run                   # 仅预览新 URL，不执行
anchor monitor --source "付鹏"             # 仅处理指定作者
anchor monitor --limit 5                   # 每个来源最多处理 5 条
anchor monitor --since 2026-03-01          # 自定义日期截止
anchor monitor --concurrency 10            # 10 个并行 worker
```

---

## 14. 配置与环境

```bash
# 必需
DATABASE_URL=sqlite+aiosqlite:///./anchor.db

# LLM（统一接口，支持 Anthropic/OpenAI 兼容）
LLM_PROVIDER=anthropic          # 或 openai
LLM_API_KEY=...
LLM_MODEL=...
ANTHROPIC_API_KEY=...           # Anthropic 专用

# 可选 — 联网搜索（事实验证必需，无则降级为纯 LLM）
SERPER_API_KEY=...

# 可选 — 社交媒体采集
TWITTER_BEARER_TOKEN=...
WEIBO_COOKIE=...
TRUTHSOCIAL_ACCESS_TOKEN=...

# 可选 — 音频转录（YouTube）
ASR_API_KEY=...
ASR_BASE_URL=...
ASR_MODEL=whisper-1
YOUTUBE_MAX_DURATION=1800       # 秒，默认 30 分钟

# 可选 — 宏观数据源
FRED_API_KEY=...
BLS_API_KEY=...
```

---

## 15. 技术栈

| 层次 | 技术 |
|------|------|
| 语言 | Python 3.11+ |
| LLM | Anthropic Claude（claude-sonnet-4-6 默认）/ OpenAI 兼容接口（Qwen、DeepSeek 等） |
| ORM | SQLModel（SQLAlchemy 2.0 异步） |
| 数据库 | SQLite（开发）/ PostgreSQL（生产） |
| 联网搜索 | Serper Search API |
| 采集 | Twitter Syndication API / Weibo AJAX / BeautifulSoup / Jina Reader |
| 监控订阅 | feedparser（RSS/Atom）/ yt-dlp（YouTube/Bilibili 频道列表）|
| 音频转录 | Whisper（YouTube / Bilibili 视频内容提取） |
| 异步框架 | asyncio + asyncpg/aiosqlite |

---

## 16. 文件结构

```
anchor/
├── __init__.py                     # __version__ = "8.0.0"
├── models.py                       # Node + Edge + DOMAIN_NODE_TYPES + 基础设施表
├── llm_client.py                   # LLM 统一接口（含 Batch API）
├── config.py                       # 配置（Pydantic Settings）
├── cli.py                          # Click CLI 入口
├── __main__.py                     # python -m anchor 支持
│
├── chains/                         # 处理链路编排
│   ├── content_extraction.py       # 内容提取 — URL → Node/Edge
│   ├── general_assessment.py       # 通用判断 — 2D分类 + 作者档案
│   ├── fact_verification.py        # 事实验证 — 批量搜索 + LLM（双语交叉验证）
│   └── prompts/
│       ├── post_analysis.py        # 通用判断提示词
│       └── policy_compare.py       # 政策对比提示词
│
├── collect/                        # 数据采集
│   ├── input_handler.py            # URL 解析 + RawPost 创建入口
│   ├── twitter.py                  # Twitter/X
│   ├── weibo.py                    # 微博
│   ├── youtube.py                  # YouTube（字幕/转录/描述 3 层策略）
│   ├── bilibili.py                 # Bilibili（yt-dlp + Whisper 转录）
│   ├── truthsocial.py              # Truth Social
│   ├── rss.py                      # RSS/Atom
│   ├── web.py                      # 通用 Web（Jina Reader + arXiv→ar5iv）
│   └── manager.py                  # 采集轮询调度
│
├── monitor/                        # 订阅监控
│   ├── feed_fetcher.py             # 平台专属 URL 抓取器
│   └── index_crawler.py            # 索引页爬虫
│
├── extract/                        # 提取引擎
│   ├── extractor.py                # Extractor 入口 → 统一分发 extract_generic
│   ├── schemas/
│   │   ├── __init__.py             # 导出 NodeExtractionResult 等
│   │   └── nodes.py                # Pydantic schema（ExtractedNode/Edge）
│   ├── pipelines/
│   │   ├── _base.py                # call_llm / call_llm_batch / parse_json
│   │   └── generic.py              # 统一 2-call 管线（含智能分段 + 批量提交）
│   └── prompts/
│       └── domains/
│           ├── __init__.py         # 领域注册表
│           ├── _base.py            # 共享模板（system prompt + user message）
│           ├── policy.py           # 政策域 (9 节点类型)
│           ├── industry.py         # 产业域 (7 节点类型)
│           ├── technology.py       # 技术域 (5 节点类型)
│           ├── futures.py          # 期货域 (6 节点类型)
│           ├── company.py          # 公司域 (5 节点类型)
│           └── expert.py           # 专家域 (4 节点类型)
│
├── verify/                         # 验证工具
│   ├── author_profiler.py          # 作者档案分析（credibility_tier 1-5）
│   └── web_searcher.py             # Serper.dev 搜索集成
│
├── pipeline/                       # 批量 pipeline 执行
│   └── concurrent.py              # ConcurrentBatchRunner + WritePool（并发提取 + FIFO 写入）
│
├── chains/
│   ├── content_extraction.py      # Chain 1 封装
│   ├── general_assessment.py      # Chain 2（2D 分类 + 作者档案）
│   ├── fact_verification.py       # Chain 3（注册表式验证）
│   └── canonicalize.py            # 节点归一化（embedding 预筛 + LLM 精判 + Union-Find）
│
├── commands/                       # CLI 命令实现
│   ├── run_url.py                  # anchor run-url
│   ├── monitor.py                  # anchor monitor
│   └── serve.py                    # anchor serve
│
└── database/
    └── session.py                  # 异步 DB Session 工厂

sources.yaml                        # 信息源订阅列表
docs/
├── PRD.md                          # 产品需求文档
└── content_classification.md       # 内容分类体系
```

---

# 附录

## 17. 系统局限与边界

### 当前不支持的能力

| 局限 | 说明 |
|------|------|
| 非文本内容 | 图表中的信息暂不提取（视频/音频已通过 Whisper 转录支持） |
| 多语言混合 | 中英混合内容可处理，其他语言效果未充分测试 |
| Twitter 监控 | 监控流水线暂不支持 Twitter/X 自动抓取，需人工提交 URL |

### 设计边界

- **6 个领域互斥**：每篇内容只进入一个领域的提取管线
- **第三方分析 → expert**：所有第三方分析/解读类内容统一进入专家域，不区分原始 domain
- **一手信息 → 对应领域**：只有一手信息才进入 policy/industry/technology/futures/company 领域
- **职责边界**：Anchor 只做信息理解和结构化提取。产业建模（周期定位、传导链）属于 Axion；定价判断和投资决策属于 Polaris
- **提取 ≠ 分析**：Anchor 提取节点和边（文本结构化），但不做深度分析（属于 Axion）

---

## 18. 路线图

### 已完成（v8.0）

- [x] **通用节点+边架构**：统一 Node + Edge 表，替代 7 实体表 + 5 专用表
- [x] **6 领域 × 统一 2-call 管线**：替代 5+ 专用管线（v6 六步、政策、产业、论文、财报）
- [x] **领域特定节点类型**：每个领域 4-9 种节点类型
- [x] **注册表式验证**：(domain, node_type) → 验证函数
- [x] **简化路由**：第三方分析 → expert，一手信息 → 对应领域
- [x] 2D 分类（content_domain × content_nature）
- [x] 利益冲突检测（has_conflict + conflict_note）
- [x] 统一路由函数 resolve_content_mode()
- [x] 验证门控（一手信息不验证）
- [x] 支持 Twitter/X、微博、YouTube、Bilibili、Truth Social、通用 Web
- [x] 订阅监控流水线（sources.yaml → RSS/YouTube/Bilibili/Weibo → Notion）
- [x] 内容质量过滤（付费墙/视频时长/文章字数）
- [x] 实际发言人识别（real_author_name）
- [x] Web UI（FastAPI，Node/Edge 渲染）
- [x] arXiv→ar5iv 重定向

### 已完成（v8.1）

- [x] **两层 DB 架构**：ExtractionNode/Edge（per-article）+ KnowledgeNode/Edge（cross-article）
- [x] **节点归一化**（`anchor/chains/canonicalize.py`）：Embedding 预筛 + LLM 精判 + Union-Find 合并，写回 canonical_node_id
- [x] **Embedding API 集成**（`anchor/llm_client.py`）：OpenAI 兼容 embedding API，用于归一化预筛
- [x] **12 种有类型边**：causes / produces / derives / supports / contradicts / implements / constrains / amplifies / mitigates / resolves / measures / competes
- [x] **权威等级字段**：Node/Edge.authority（0=一手信息，1+=作者 tier）
- [x] **有效期字段**：valid_from / valid_until（LLM 判断的内容时效性）
- [x] **并发批量提取**（`anchor/pipeline/concurrent.py`）：
  - `ConcurrentBatchRunner`：asyncio.Semaphore 控制并发数
  - `WritePool`：FIFO 队列串行写入 DB，避免并发写入冲突
  - `extract_generic` 拆分为 `extract_generic_compute`（纯 LLM，可并发）+ `extract_generic_write`（纯 DB，走 WritePool）
  - 架构：LLM 调用并发 → 提取完放入队列 → DB 写入 FIFO 串行

### 近期规划

- [ ] Twitter/X 自动监控（订阅流水线集成）
- [ ] 节点可视化（导出为 PNG/SVG，支持交互式知识图浏览）
- [ ] 批量处理 API（批量 URL 提交、进度查询）

### 中长期规划

- [ ] 跨文档引用追踪（政策文件引用关系图谱）
- [ ] 预测验证自动化（到期预测自动触发事后验证）
- [ ] 多语言支持（英语内容提取与验证）
- [ ] 领域扩展（新增领域 + 节点类型，只需添加提示词文件）

---

> 文档状态：v8.1（2026-03-12），两层 DB 架构 + 并发提取 + 节点归一化
