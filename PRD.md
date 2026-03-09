# Anchor — 产品需求文档 (PRD)

> 版本：v5.1
> 更新：2026-03-09
> 基于实际代码实现编写

---

## 目录

1. [产品定位](#1-产品定位)
2. [系统架构](#2-系统架构)
3. [数据模型](#3-数据模型)
4. [三条链路设计](#4-三条链路设计)
5. [内容路由逻辑](#5-内容路由逻辑)
6. [六实体提取（标准模式）](#6-六实体提取标准模式)
7. [政策模式](#7-政策模式)
8. [Chain 3 验证规则](#8-chain-3-验证规则)
9. [监控流水线](#9-监控流水线)
10. [配置与环境](#10-配置与环境)
11. [技术栈](#11-技术栈)
12. [文件结构](#12-文件结构)

---

## 1. 产品定位

Anchor 是一个**多层观点提取、政策分析与事实验证系统**，专为分析社交媒体（Twitter/X、微博等）及官方文件（政府工作报告、政策公告）上的经济、金融、政治、社会类观点而设计。

**v5.1 核心改进（相比 v5.0）：**
- **内容分类重构**：content_type 从 8 种精简为 6 种，新增"财经分析"大类（含5种子分类）替代"市场分析"，提升分类精度
- **实际发言人识别**：Chain 2 新增 real_author_name，自动识别个人品牌账号背后的真实发言人
- **立场分析 4 维度**：从单一 stance_label 升级为意识形态/地缘立场/利益代表/客观性四维度分析
- **监控流水线**：新增 run_monitor.py，从 watchlist.yaml 批量监控 RSS/YouTube/Bilibili/Weibo 订阅源，含内容质量过滤和 Notion 同步
- **Bilibili 采集**：新增 Bilibili 视频采集器（yt-dlp + Whisper 转录），与 YouTube 共用配置

**v5 核心改进（相比 v4）：**
- **v5 五步流水线**：将单次 LLM 提取拆分为 Step1（原始声明）→ Step2（去重合并）→ Step3（DAG感知分类）→ Step4（隐含条件发现）→ Step5（叙事摘要），提升抽取精度
- **政策专用模式**：针对政府工作报告、政策公告等文件的独立提取路径（PolicyTheme + PolicyItem）
- **双文档比对**：两年政策文件分别提取后，专用 LLM 标注 change_type（新增/调整/延续）
- **三链路职责明确化**：Chain 1 提取内容、Chain 2 识别作者与机关、Chain 3 验证事实并追踪执行

---

## 2. 系统架构

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
           │    Chain 2 Step 0（前置）                   │
           │    classify_post() — 内容分类 + 发文机关      │
           │    → content_type / issuing_authority       │
           └────────────┬─────────────┬────────────────┘
                        │             │
              content_mode=standard   content_mode=policy
                        │             │
                        ▼             ▼
        ┌───────────────┐   ┌─────────────────────────────┐
        │    Chain 1     │   │         Chain 1              │
        │  标准六实体模式  │   │       政策专用模式             │
        │  v5 五步流水线  │   │  PolicyTheme + PolicyItem    │
        │                │   │  background / urgency /      │
        │  Fact          │   │  metric / enforcement        │
        │  Assumption    │   │                              │
        │  ImplicitCond  │   │  ↓ compare_policies()        │
        │  Conclusion    │   │  change_type 标注             │
        │  Prediction    │   │  (新增/调整/延续/删除)         │
        │  Solution      │   └──────────────┬──────────────┘
        └───────┬────────┘                  │
                │                           │
                └──────────────┬────────────┘
                               │
           ┌───────────────────▼──────────────────────┐
           │              Chain 2                       │
           │  作者档案（AuthorProfiler）                  │
           │  + 立场聚合（AuthorStanceProfile）           │
           │  credibility_tier 1-5 / stance_label       │
           └───────────────────┬──────────────────────┘
                               │
           ┌───────────────────▼──────────────────────┐
           │              Chain 3                       │
           │  标准模式：六实体验证（Tavily + LLM）         │
           │  政策模式：PolicyItem 执行情况追踪             │
           │  execution_status（已落地/推进中/受阻/...）   │
           └──────────────────────────────────────────┘
```

---

## 3. 数据模型

### 3.1 基础设施表

| 表名 | 描述 | 关键字段 |
|------|------|---------|
| `authors` | 观点作者档案 | name, platform, platform_id, role, expertise_areas, known_biases, credibility_tier(1-5), profile_note(≤80字), situation_note(≤150字) |
| `raw_posts` | 原始帖子/文档 | source, external_id, content, enriched_content, posted_at, is_processed, content_type, content_subtype, content_type_secondary, author_intent, issuing_authority, authority_level, content_summary, notion_page_id, policy_delta |
| `monitored_sources` | 监控源 | url, source_type, platform, fetch_interval_minutes, last_fetched_at |
| `author_groups` | 跨平台作者实体 | canonical_name, canonical_role |
| `topics` | 话题标签 | name, description, tags |

### 3.2 六实体表

| 实体 | 表名 | 核心字段 | Chain3 verdict |
|------|------|---------|----------------|
| Fact（事实依据）| `facts` | claim(≤120字), verifiable_statement, temporal_type, temporal_note, summary(≤15字) | `fact_verdict`: credible\|vague\|unreliable\|unavailable |
| Assumption（假设条件）| `assumptions` | condition_text(≤120字), verifiable_statement, temporal_note, summary | `assumption_verdict`: high_probability\|medium_probability\|low_probability\|unavailable |
| ImplicitCondition（隐含条件）| `implicit_conditions` | condition_text(≤120字), is_obvious_consensus, summary | `implicit_verdict`: consensus\|contested\|false |
| Conclusion（结论）| `conclusions` | claim(≤120字), author_confidence, is_core_conclusion, is_in_cycle, summary | `conclusion_verdict`: confirmed\|refuted\|partial\|unverifiable\|pending |
| Prediction（预测）| `predictions` | claim(≤120字), temporal_note, temporal_validity, monitoring_start, monitoring_end, author_confidence, summary | `prediction_verdict`: pending\|accurate\|directional\|off_target\|wrong |
| Solution（解决方案）| `solutions` | claim(≤120字), action_type, action_target, action_rationale, summary | 不验证 |

### 3.3 关系边表

```sql
relationships (
    source_type  -- fact|assumption|implicit_condition|conclusion|prediction|solution|policy_item
    source_id
    target_type
    target_id
    edge_type    -- 见下方枚举
    note         -- ≤80字说明
)
```

**EdgeType 枚举（7种）：**

| EdgeType | 含义 |
|----------|------|
| `fact_supports_conclusion` | 事实支撑结论 |
| `assumption_conditions_conclusion` | 假设条件结论 |
| `implicit_conditions_conclusion` | 隐含前提制约结论 |
| `conclusion_supports_conclusion` | 结论支撑更高层结论 |
| `conclusion_leads_to_prediction` | 结论推出预测 |
| `conclusion_enables_solution` | 结论使解决方案成立 |
| `policy_supports_conclusion` | 政策条目支撑政策结论 |

### 3.4 政策专用表（Policy Mode）

**PolicyTheme（政策主旨）**

| 字段 | 类型 | 说明 |
|------|------|------|
| theme_name | VARCHAR | 主旨名称（≤6字，如"财政政策""对台政策"） |
| background | TEXT | 背景与目的（≤200字，Chain 1 从文件推断"为什么是现在"） |
| enforcement_note | VARCHAR | 组织保障（≤80字，谁牵头、是否纳入考核） |
| has_enforcement_teeth | BOOLEAN | 是否有执行主体且纳入考核 |

**PolicyItem（政策条目）**

| 字段 | 类型 | 说明 |
|------|------|------|
| summary | VARCHAR | ≤15字摘要 |
| policy_text | VARCHAR | 政策内容（≤120字） |
| urgency | VARCHAR | mandatory\|encouraged\|pilot\|gradual |
| metric_value | VARCHAR | 量化指标（如"4%""1.3万亿"），无则 null |
| target_year | VARCHAR | 目标年份（如"2026"），无则 null |
| is_hard_target | BOOLEAN | 是否量化硬约束（有数值+年份则 true） |
| change_type | VARCHAR | 新增\|调整\|延续（比对后填写，提取时为 null） |
| change_note | VARCHAR | ≤30字变化说明 |
| execution_status | VARCHAR | implemented\|in_progress\|stalled\|not_started\|unknown（Chain 3 填写） |
| execution_note | VARCHAR | ≤80字执行情况说明 |

### 3.5 评估与统计表

| 表名 | 描述 |
|------|------|
| `author_stance_profiles` | 作者立场档案（stance_distribution JSON, dominant_stance, audience, core_message, author_summary） |
| `post_quality_assessments` | 单篇内容质量评估 |
| `author_stats` | 作者综合统计（准确率、可信度评分） |

---

## 4. 三条链路设计

### Chain 1 — 提取（`anchor/chains/chain1_extractor.py`）

**职责**：URL → 六实体 / 政策实体 → DB

**流程：**
```
URL
 → process_url()           — 采集 RawPost（去重、存库）
 → classify_post()         — Chain 2 前置分类（确定 content_mode）
 → Extractor.extract()     — 标准模式或政策模式提取
 → fetch_prior_year_and_compare()  — 政策模式：自动搜索上年文档并比对
 → 返回所有实体汇总
```

**Chain 1 在 RawPost 上写入的字段：**
- `content_summary`（Step5 叙事摘要）
- `is_processed`, `processed_at`

**Chain 1 不写的字段（由 Chain 2 写）：**
- `issuing_authority`, `authority_level`
- `content_type`, `author_intent`

---

### Chain 2 — 作者分析（`anchor/chains/chain2_author.py`）

**职责**：理解"谁说的"和"说的什么类型"

**三步：**

| 步骤 | 输入 | 输出 | 写入表 |
|------|------|------|-------|
| Step 1 AuthorProfiler | author_id | role, expertise, credibility_tier, situation_note | authors |
| Step 2 内容分类 | RawPost | content_type, content_subtype, author_intent, real_author_name, issuing_authority, authority_level | raw_posts |
| Step 3 立场聚合 | 近期10条帖子 | 4维度立场, audience, core_message, author_summary | author_stance_profiles |

**分类枚举：**

- **content_type（6种）**：财经分析 \| 市场动向 \| 产业链研究 \| 公司调研 \| 技术论文 \| 政策解读
- **content_subtype（仅财经分析，5种）**：市场分析 \| 地缘分析 \| 政策分析 \| 技术影响 \| 混合分析
- **author_intent（8种，开放式）**：传递信息 \| 影响观点 \| 警示风险 \| 推荐行动 \| 教育科普 \| 引发讨论 \| 推广宣传 \| 政治动员

**实际发言人识别（real_author_name）：**
- 标题点名某人（如"付鹏最新分析"）→ 填该人姓名
- 个人品牌账号（如"付鹏的财经世界"）→ 提取真实发言人
- 转载/解读账号 → 找被采访/被分析的人；无法判断填 null

**立场分析 4 维度：**

| 维度 | 说明 | 示例 |
|------|------|------|
| 意识形态 | 政治/经济主张倾向 | 自由市场主义 / 凯恩斯主义 / 民族主义 |
| 地缘立场 | 国际关系倾向 | 亲美 / 亲中 / 亲俄 / 多极主义 |
| 利益代表 | 观点服务于谁 | 独立分析师 / 所在机构 / 华尔街 |
| 客观性 | 整体中立程度 | 相对客观 / 有明显倾向 / 立场鲜明 |

**作者可信度分级（credibility_tier）：**

| 等级 | 描述 | 示例 |
|------|------|------|
| 1 | 顶级权威 | 现任央行行长、诺贝尔经济学奖得主、万亿基金创始人 |
| 2 | 行业专家 | 知名对冲基金经理、首席经济学家、前国家领导人 |
| 3 | 知名评论员 | 财经媒体主播、有记录的分析师 |
| 4 | 一般媒体/KOL | 社交媒体账号、无明显专业背景 |
| 5 | 未知 | 无可检索背景信息 |

---

### Chain 3 — 验证（`anchor/chains/chain3_verifier.py`）

**职责**：检验六实体的可信度；追踪政策执行情况

**两种模式，根据帖子是否含 PolicyItem 自动切换：**

**标准模式（5步验证）：**

| 步骤 | 实体 | 方法 | 输出字段 |
|------|------|------|---------|
| Step 1 | Fact | Tavily + LLM | `fact_verdict` |
| Step 2 | Assumption | Tavily + LLM | `assumption_verdict` |
| Step 3 | ImplicitCondition | 共识快速通道 or Tavily + LLM | `implicit_verdict` |
| Step 4 | Conclusion | 规则推导（读 relationships 表） | `conclusion_verdict` |
| Step 5 | Prediction | 时效检查 + Tavily + LLM | `prediction_verdict` |

**政策模式（额外步骤）：**
- 检测到 PolicyItem → 逐条搜索执行进展
- 优先追踪硬约束（is_hard_target=True）和强制类（urgency=mandatory）
- 输出 `execution_status` + `execution_note`

---

## 5. 内容路由逻辑

```python
# chain1_extractor.py / run_monitor.py
content_mode = "policy" if content_type == "政策解读" else "standard"
```

**注意**：`政策宣布` 已从 content_type 枚举中移除（原始政策文件直接归入 `政策解读`）。

**路由影响：**

| 维度 | 标准模式（财经分析等） | 政策模式（政策解读） |
|------|---------------------|----------------------|
| 提取管线 | v5 五步（Fact/Assumption/...） | Step1Policy（PolicyTheme+Item） |
| Chain 1 输出 | 六实体 + 关系边 | 政策主旨 + 政策条目 + Facts + Conclusions |
| Chain 3 行为 | 六实体验证 | 执行情况追踪（额外） + 六实体验证 |
| 双文档比对 | 无 | 自动搜索上年文档并比对 change_type |

**监控流水线额外过滤**（`run_monitor.py`）：
- `content_type != "财经分析"` → 跳过（不进 Chain 1 提取器）
- 视频 < 180 秒 → 跳过
- 文章正文 < 200 字 → 跳过
- 付费墙检测命中 → 跳过

---

## 6. 六实体提取（标准模式）

### v5 五步流水线（`anchor/extract/extractor.py`）

| 步骤 | 提示词文件 | 输入 | 输出 | Token 预算 |
|------|-----------|------|------|-----------|
| Step 1 | v5_step1_claims.py | 全文 + 上下文 | 原始声明列表 + 边列表 | 4000 |
| Step 2 | v5_step2_merge.py | 声明列表 | 去重合并方案 | 2000 |
| Step 3 | v5_step3_classify.py | 声明 + DAG 结构 | 每条声明的实体类型 | 4000 |
| Step 4 | v5_step4_implicit.py | 推理对（前提→结论） | 隐含条件列表 | 3000 |
| Step 5 | v5_step5_summary.py | 核心结论 + 关键事实 | 叙事摘要（2-3句） | 1000 |

**Step 3 关键区分规则：**
- **Conclusion**（结论）= 对过去/当前状态的判断（回顾型）
- **Prediction**（预测）= 明确指向未来，含"将/未来/会/预计"等时态词
- **Fact**（事实）= 可独立核查的客观陈述
- **Assumption**（假设）= 明确的"如果X则Y"前提
- **ImplicitCondition** = 推理依赖但未说出的暗含前提
- **Solution** = 具体行动建议（买/卖/持有/倡导等）

**Step 5 资本流向规则：**
当同时存在防御资产（HALO 板块：黄金/国债/避险货币）和 AI 受益资产时，摘要需明确区分两条资本流向，不可笼统描述为"市场避险"。

**DAG 分析（Python 层）：**
- `is_core_conclusion`：无出边指向其他结论的叶结论（最终判断）
- `is_in_cycle`：DFS 检测环路，环内结论在 Chain 3 Step 4 跳过

---

## 7. 政策模式

### 7.1 提取框架（五维分析）

| 维度 | 链路 | 字段 | 说明 |
|------|------|------|------|
| ① 定调术语（紧迫度） | Chain 1 | `PolicyItem.urgency` | mandatory/encouraged/pilot/gradual |
| ② 关键指标（硬约束） | Chain 1 | `metric_value + is_hard_target` | 有量化数值+目标年份则为硬约束 |
| ③ 组织保障 | Chain 1 | `enforcement_note + has_enforcement_teeth` | 谁牵头、是否纳入考核 |
| ④ 背景与目的 | Chain 1 | `PolicyTheme.background` | 从文件推断"为什么是现在" |
| ⑤ 发文机关 | Chain 2 | `issuing_authority + authority_level` | 顶层设计\|部委联合\|部委独立 |

**urgency 映射规则：**

| urgency | 触发词 |
|---------|--------|
| mandatory | 严禁/必须/不得/强制/明确要求 |
| encouraged | 鼓励/支持/推动/引导/积极 |
| pilot | 探索/试点/研究/开展试验 |
| gradual | 循序渐进/稳步/有序/逐步 |

**扫描范围（不可遗漏）：**
- 经济类：财政、货币、产业、科技、民生、外贸、改革开放、绿色低碳、房地产
- 主权与安全类：对台政策、国防军事、外交

### 7.2 双文档比对（`compare_policies`）

```
current_post（当年）+ prior_post（上年）
    → 读取两年的 PolicyItem 列表
    → LLM 比对 → change_type 标注：新增|调整|延续
    → deleted_summaries → 写入当年 Fact（[删除]前缀）
    → 幂等：已有 change_type 则跳过
```

### 7.3 自动搜索上年文档（`fetch_prior_year_and_compare`）

```
1. 读取当前帖子年份 → prior_year = year - 1
2. 检查 DB 是否已有上年政策帖子
3. 若无 → Tavily 搜索 "{prior_year}年政府工作报告 全文"
   include_domains: gov.cn / xinhuanet.com / npc.gov.cn / people.com.cn
4. Jina Reader 获取全文（<500字则换下一结果）
5. 创建 RawPost（external_id = MD5 去重）
6. 提取政策实体（policy mode）
7. 执行 compare_policies()
```

### 7.4 执行追踪（Chain 3）

**execution_status 枚举：**

| 状态 | 含义 |
|------|------|
| implemented | 已完全落地，有明确数据或公告证明 |
| in_progress | 正在推进，已有具体行动但尚未完成 |
| stalled | 推进受阻或明显低于预期 |
| not_started | 尚无任何落地迹象 |
| unknown | 搜索结果不足以判断 |

**追踪优先级**：is_hard_target=True > urgency=mandatory > 其余

---

## 8. Chain 3 验证规则

### Fact verdict 推导

| source_tier | is_vague | fact_verdict |
|-------------|----------|--------------|
| authoritative / mainstream_media / market_data | false | credible |
| authoritative / mainstream_media / market_data | true | vague |
| rumor | — | unreliable |
| no_source | — | unavailable |

### Conclusion verdict 规则推导

| 条件（优先级从高到低） | conclusion_verdict |
|---------------------|-------------------|
| 任意 low_probability 假设 | refuted |
| 任意 unreliable 事实 | refuted |
| 全部事实 unavailable，无假设/隐含 | unverifiable |
| 无任何支撑实体 | pending |
| 全部事实 credible/vague，无异常 | confirmed |
| 部分 unavailable 或 contested 隐含条件 | partial |

注：`is_in_cycle=True` 的结论跳过推导。

### Prediction 监控规则

- `temporal_validity=no_timeframe` → 保持 pending，跳过
- `monitoring_end` 未到 → pending，跳过
- `monitoring_end` 已过或无值 → Tavily + LLM 验证

---

## 9. 监控流水线

### 9.1 架构概览（`run_monitor.py`）

```
watchlist.yaml
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
      ├── run_chain2()          — 内容分类 + 作者档案
      ├── Extractor.extract()   — 仅处理 content_type=财经分析
      └── sync_post_to_notion() — Notion 同步
```

### 9.2 内容质量过滤规则

| 条件 | 结果 |
|------|------|
| `raw_metadata.is_video_only=True` + youtube_redirect 存在 | 递归改抓 YouTube 链接 |
| `raw_metadata.is_video_only=True` + 无重定向 | 跳过（video_only） |
| 视频时长 `duration_s < 180` 秒 | 跳过（video_short） |
| 文章正文 < 200 字 | 跳过（text_short） |
| 付费墙正则匹配命中 | 跳过（paywall_skip） |
| `content_type != "财经分析"` | 跳过（non_market） |

### 9.3 feed_fetcher 支持的平台（`anchor/monitor/feed_fetcher.py`）

| 平台 | 方法 |
|------|------|
| RSS / Atom（Substack、Project Syndicate、IMF 等） | feedparser |
| YouTube 频道 | yt-dlp 平铺视频列表 |
| Bilibili 空间 | 官方 API |
| Weibo 用户 | 公开 HTML 抓取 |
| Twitter / LinkedIn | 暂不支持（跳过，需人工） |

### 9.4 命令行用法

```bash
python run_monitor.py                      # 跑全部来源
python run_monitor.py --dry-run            # 仅预览新 URL，不执行
python run_monitor.py --source "付鹏"      # 仅处理指定作者
python run_monitor.py --limit 5            # 每个来源最多处理 5 条
python run_monitor.py --since 2026-03-01   # 自定义日期截止
```

---

## 10. 配置与环境

```bash
# 必需
DATABASE_URL=sqlite+aiosqlite:///./anchor.db

# LLM（统一接口，支持 Anthropic/OpenAI 兼容）
LLM_PROVIDER=anthropic          # 或 openai
LLM_API_KEY=...
LLM_MODEL=...
ANTHROPIC_API_KEY=...           # Anthropic 专用

# 可选 — 联网搜索（Chain 3 必需，无则降级为纯 LLM）
TAVILY_API_KEY=...

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

## 11. 技术栈

| 层次 | 技术 |
|------|------|
| 语言 | Python 3.11+ |
| LLM | Anthropic Claude（claude-sonnet-4-6 默认）/ OpenAI 兼容接口（Qwen、DeepSeek 等） |
| ORM | SQLModel（SQLAlchemy 2.0 异步） |
| 数据库 | SQLite（开发）/ PostgreSQL（生产） |
| 联网搜索 | Tavily Search API |
| 采集 | Twitter Syndication API / Weibo AJAX / BeautifulSoup / Jina Reader |
| 监控订阅 | feedparser（RSS/Atom）/ yt-dlp（YouTube/Bilibili 频道列表）|
| 音频转录 | Whisper（YouTube / Bilibili 视频内容提取） |
| 异步框架 | asyncio + asyncpg/aiosqlite |

---

## 12. 文件结构

```
anchor/
├── models.py                        # 数据模型（六实体 + 政策 + 基础设施）
├── llm_client.py                    # LLM 统一接口
├── config.py                        # 配置（Pydantic Settings）
│
├── chains/                          # 三条链路编排（入口）
│   ├── chain1_extractor.py          # Chain 1 — 提取
│   ├── chain2_author.py             # Chain 2 — 作者分析 + 发文机关
│   ├── chain3_verifier.py           # Chain 3 — 验证 + 执行追踪
│   └── prompts/
│       ├── post_analysis.py         # Chain 2 内容分类 + 发文机关提示词
│       └── policy_compare.py        # （备用）
│
├── collect/                         # 数据采集
│   ├── input_handler.py             # URL 解析 + RawPost 创建入口
│   ├── twitter.py                   # Twitter/X
│   ├── weibo.py                     # 微博
│   ├── youtube.py                   # YouTube（3层内容策略）
│   ├── bilibili.py                  # Bilibili（yt-dlp + Whisper 转录）
│   ├── truthsocial.py               # Truth Social
│   ├── rss.py                       # RSS/Atom
│   ├── web.py                       # 通用 Web（BeautifulSoup）
│   └── manager.py                   # 采集轮询调度
│
├── monitor/                         # 订阅监控
│   └── feed_fetcher.py              # 平台专属 URL 抓取器（RSS/YouTube/Bilibili/Weibo）
│
├── extract/                         # 提取引擎（Chain 1 使用）
│   ├── extractor.py                 # Extractor 主类（v5 + 政策模式）
│   ├── schemas.py                   # Pydantic schema（六实体 + 政策 + 比对结果）
│   └── prompts/
│       ├── __init__.py              # DEFAULT_PROMPT_VERSION = "v5"
│       ├── v5_step1_claims.py       # Step1：原始声明提取
│       ├── v5_step2_merge.py        # Step2：语义去重合并
│       ├── v5_step3_classify.py     # Step3：DAG 感知分类
│       ├── v5_step4_implicit.py     # Step4：隐含条件发现
│       ├── v5_step5_summary.py      # Step5：叙事摘要
│       ├── v5_step1_policy.py       # 政策模式 Step1（PolicyTheme+Item）
│       ├── v5_compare_policy.py     # 政策双文档比对
│       └── v4_sixentity.py          # v4 备用（单次LLM，已归档）
│
├── verify/                          # 验证工具
│   ├── author_profiler.py           # 作者档案分析（credibility_tier 1-5）
│   └── web_searcher.py              # Tavily 搜索集成
│
└── database/
    └── session.py                   # 异步 DB Session 工厂

run_monitor.py                       # 监控流水线主脚本（watchlist.yaml → Notion）
run_url.py                           # 单 URL 分析入口
anchor_ui.py                         # FastAPI Web UI（port 8765）
debug_pipeline.py                    # 完整调试脚本（逐步输出到文件）
test_govreport.py                    # 政府工作报告端到端测试
migrate.sql                          # DB 迁移脚本（policy_items 重建）
watchlist.yaml                       # 监控源订阅列表
```

---

> 文档状态：与实际代码同步（v5.1，2026-03-09）
