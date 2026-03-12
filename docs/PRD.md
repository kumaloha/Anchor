# Anchor — 产品需求文档 (PRD)

> 版本：v7.2
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
10. [七实体提取（标准模式）](#10-七实体提取标准模式)
    - [10.1 v6 Top-Down Pipeline（默认）](#101-v6-top-down-pipeline默认)
    - [10.2 v5 Bottom-Up Pipeline（保留）](#102-v5-bottom-up-pipeline保留)
11. [政策模式](#11-政策模式)
12. [产业链研究模式](#12-产业链研究模式)
13. [事实验证规则](#13-事实验证规则)
14. [监控流水线](#14-监控流水线)
15. [配置与环境](#15-配置与环境)
16. [技术栈](#16-技术栈)
17. [文件结构](#17-文件结构)

**附录**

18. [系统局限与边界](#18-系统局限与边界)
19. [路线图](#19-路线图)

---

# 产品篇

## 1. 产品定位与愿景

**让每一篇财经分析、政策文件、技术论文、公司财报，都能被系统性地读懂并转化为结构化知识。**

Anchor 是**理解模型**——一个多模式的信息提取与事实验证引擎。它从非结构化文本中提取结构化实体（观点 DAG、政策条目、产业结构、公司财务、论文分析），并通过事实核查验证可信度。

Anchor 是三层投资分析系统的第一层：

```
文章/文件 → [Anchor 理解模型] → 结构化实体
                                      ↓
         市场数据 → [Axion 产业模型]  → 价格预测
                                      ↓
         当前定价 → [Polaris 量化投资] → 投资决策
```

**Anchor 的职责边界**：只做信息理解和结构化提取，不做世界建模、价格预测或投资决策。产出的结构化实体供下游 Axion 消费。

**历史演进**：
- v1-v5：观点提取 + 事实验证系统（七实体 DAG + 事实验证）
- v6：Top-down 提取 + 产业链/论文/财报多模式 + 关系图
- **v7（本版本）：明确定位为理解模型，与 Axion（产业模型）和 Polaris（量化投资）分离**

**v6 核心改进（相比 v5）：**
- **Top-down 提取**：先找核心结论/理论锚点，再按相关性过滤提取支撑实体，替代 v5 的 bottom-up 全量提取
- **相关性过滤替代数量约束**：天然限制实体数量，不再需要机械的节点数量限制
- **显式抽象步骤**：确保每个实体表达包含明确的主体/对象（Call 3）
- **LLM 建关系**：由 LLM 在全局视角下建立实体间关系，替代 v5 的规则推导
- **简化流程**：不再生成 ImplicitCondition，减少一次 LLM 调用
- **归一化**：合并步骤确保同一概念跨文章产出一致表达

**v5.1 核心改进（相比 v5.0）：**
- **内容分类重构**：content_type 从 8 种精简为 6 种，新增"财经分析"大类（含5种子分类）替代"市场分析"，提升分类精度
- **实际发言人识别**：通用判断新增 real_author_name，自动识别个人品牌账号背后的真实发言人
- **立场分析 4 维度**：从单一 stance_label 升级为意识形态/地缘立场/利益代表/客观性四维度分析（v7.1 已移除）
- **监控流水线**：新增 run_monitor.py，从 watchlist.yaml 批量监控 RSS/YouTube/Bilibili/Weibo 订阅源，含内容质量过滤和 Notion 同步
- **Bilibili 采集**：新增 Bilibili 视频采集器（yt-dlp + Whisper 转录），与 YouTube 共用配置

---

## 2. 用户画像

### 2.1 政策研究员

**痛点**：每年政府工作报告数万字，需要与上年报告逐条对比，手动标记"新增/调整/延续/删除"的政策条目，同时追踪各项政策的实际执行情况。

**Anchor 的解法**：
- 自动解析报告全文，按政策主旨（财政、货币、对台政策、国防等）分组
- 自动获取上年同类报告，逐条比对、标注变化类型
- 事实验证实时检索各项政策的最新执行新闻，给出"已落地/推进中/受阻/未启动"的判断

### 2.2 宏观投资分析师

**痛点**：需要快速判断一篇财经 KOL 的观点是否有事实支撑，论证链是否成立，预测是否与历史记录一致。

**Anchor 的解法**：
- 七实体提取（事实、假设、隐含条件、结论、预测、解决方案、理论框架）构建论证 DAG
- 事实验证：调用网络检索核查数据真实性，给出"可信/模糊/不可信"判断
- 结论可信度：综合验证所有支撑证据后，给出"已确认/待验证/已否定"

### 2.3 内容合规/媒体监控

**痛点**：需要识别一篇文章的真实立场和意图，区分"传递信息"与"影响观点"或"推广宣传"。

**Anchor 的解法**：
- 通用判断自动识别内容类型（6 种）、作者意图（8 种）
- 作者档案（信誉等级 1-5 + 四维立场标签）可跨内容聚合
- 对同一作者历史内容的立场一致性进行追踪

### 2.4 产业投资者

**痛点**：需要从大量研报、财报、政策文件中快速提取关键信息，理解产业格局和公司基本面。

**Anchor 的解法**：
- 产业链结构化提取：节点、玩家、议题、技术路线、指标
- 公司财报深度分析：业务概况、财务指标、管理层评估
- 政策文件条目化提取 + 年度对比 + 执行追踪
- 财经分析文章的观点 DAG：论证链可视化、事实核查
- 所有结构化实体供下游 Axion 产业模型消费

---

## 3. 核心使用场景

### 场景 A：政府工作报告年度比对

**用户操作**：提交 2026 年政府工作报告 URL（或直接粘贴全文）

**Anchor 处理流程**：

```
1. 通用判断（前置）：识别为"政策解读"，发文机关 = 国务院
2. 内容提取（政策模式）：
   - 按主旨分组（财政、货币、科技、民生、对台、国防等）
   - 每个主旨下列出具体政策条目
   - 为每个主旨填写"背景与目的"（为何此时出台）
   - 标注每条政策的紧迫程度（强制/鼓励/试点/渐进）
   - 标注硬性量化目标（GDP 增长 5%、赤字率 4% 等）
   - 标注组织保障（配套机构、资金安排）
3. 自动比对：系统检索 2025 年报告，完成双文档对比
   - 标注每条政策的变化类型（新增/调整/延续）
   - 列出 2025 年有而 2026 年删除的政策
4. 事实验证（执行追踪）：
   - 检索各条政策的最新执行新闻
   - 给出执行状态（已落地/推进中/受阻/未启动）
```

**用户价值**：1 小时完成原需 2 天的政策比对工作，并附带实时执行状态。

---

### 场景 B：财经 KOL 观点深度分析

**用户操作**：提交一条 Twitter/X 长推文 URL，作者是某知名宏观经济评论员

**Anchor 处理流程**：

```
1. 通用判断（前置）：识别为"财经分析"，意图 = 影响观点
2. 内容提取（标准模式，五步流水线）：
   - Step 1：提取所有原始声明（20+ 条事实性/推断性陈述）
   - Step 2：去重合并语义重复的声明
   - Step 3：分类为 Fact/Assumption/Conclusion/Prediction/Theory，构建 DAG
   - Step 4：识别作者未明言的隐含条件
   - Step 5：生成 2-3 句叙事摘要
3. 通用判断（后置）：更新作者档案，记录本次立场
4. 事实验证：
   - 逐条核查 Fact（可信/模糊/不可信）
   - 评估 Assumption 概率（高/中/低）
   - 推导 Conclusion 可信度（已确认/待验证/已否定）
   - 标注 Prediction 是否已可验证
```

**用户价值**：快速识别哪些结论建立在低概率假设或不可信事实之上，避免被误导性观点影响投资决策。

---

### 场景 C：作者信誉追踪

**用户操作**：分析某作者过去 3 个月的所有帖子

**Anchor 处理流程**：

```
1. 批量提交作者历史内容 URL
2. 每条内容经内容提取 + 通用判断处理，生成完整实体图
3. 事实验证后，系统统计：
   - 已验证为真的事实比例
   - 已被否定的结论数量
   - 极端/激进立场的频次
4. AuthorProfile 更新信誉等级（1-5 级）和立场标签
```

**用户价值**：建立可追溯的作者可信度记录，历史评估结果持久化，不依赖主观印象。

---

## 4. 功能概览

| 功能 | 说明 | 支持的内容类型 |
|------|------|----------------|
| 智能采集 | Twitter/X、微博、YouTube、Bilibili、Truth Social、通用 Web | 全部 |
| 订阅监控 | 从 watchlist.yaml 批量监控 RSS/Substack/YouTube/Bilibili 订阅源 | 全部 |
| 内容质量过滤 | 付费墙检测、视频时长过滤（<3分钟跳过）、文章字数过滤（<200字跳过） | 全部 |
| 内容分类 | 6 种内容类型 + 5 种财经分析子分类 + 8 种作者意图 | 全部 |
| 实际发言人识别 | 识别个人品牌账号/转载频道背后的真实发言人 | 全部 |
| 发文机关识别 | 识别政策类文件的发布机关及其权威级别 | 政策类 |
| 七实体提取 | 事实、假设、隐含条件、结论、预测、解决方案、理论框架 | 财经分析类 |
| 论证 DAG | 可视化各实体的支撑/推导关系 | 财经分析类 |
| 政策主旨分组 | 按政策领域分组，含背景/紧迫性/量化目标/保障 | 政策类 |
| 双文档对比 | 自动获取上年文件，标注增/调/延/删 | 政策类 |
| 执行情况追踪 | 实时检索政策执行新闻，给出落地状态 | 政策类 |
| 事实核查 | 网络检索验证，输出可信度判断 | 财经分析类 |
| 结论可信度 | 综合所有支撑证据，输出最终判断 | 财经分析类 |
| 作者档案 | 信誉等级（1-5）、四维立场标签、历史记录 | 全部 |

---

## 5. 输出示例

### 5.1 政策主旨输出示例

```
【财政政策】✓ （有执行保障）
发文机关：国务院 / 国家级

背景与目的：
  外需不确定性加大，内需不足问题仍突出，需要通过积极财政政策
  托底经济，维持合理增长区间。

组织保障：
  财政部负责统筹协调，各地方政府配套落实。

政策条目（4条）：
  [新增] 强制 赤字率提升至4%
    内容：将财政赤字率从3%提高至4%，增加财政支出空间
    量化目标：4%  硬性约束
    执行状态：✅ 已落地（全国人大批准预算案，财政部已下达额度）

  [调整] 强制 超长期特别国债 2万亿
    内容：连续多年发行超长期特别国债，用于重大战略领域投资
    量化目标：2万亿元  硬性约束
    执行状态：🔄 推进中（首批已招标发行，二期计划中）
```

### 5.2 七实体 DAG 输出示例

```
事实 [可信] 2025年Q4 CPI同比 +0.1%
  ↓ 支撑
结论 [已确认] 通缩压力持续存在
  ↓ 支撑
结论 [待验证] ★ 政策宽松周期将延长
  ↓ 推导
预测 [待验证] 2026年降息2次（时间窗口：未明确）

理论框架 债务大周期理论：经济体在长期债务积累后必然进入去杠杆阶段
  ↓ 推导
预测 [待验证] 未来2-3年进入实质性去杠杆阶段

假设 [低概率] 全球大宗商品价格保持稳定
  ↓ 条件
结论 [已否定] 输入性通胀不构成约束

隐含条件 [有争议] 消费者信心能在短期内回升
  ↓ 条件
结论 [待验证] 促消费政策将有明显效果
```

### 5.3 作者档案示例

```
作者：@MacroAnalyst_HK
信誉等级：3 / 5（中等可信）
立场标签：偏悲观 / 鹰派货币

历史记录（最近3个月）：
  分析内容  12 篇
  可信事实比例  78%
  已确认结论  5 个
  已否定结论  3 个
  待验证结论  8 个
  极端立场  偶发（2次）
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
           │    通用判断 Step 0（前置）                    │
           │    assess_post() — 内容分类 + 发文机关        │
           │    → content_domain / content_nature        │
           └────────────┬─────────────┬────────────────┘
                        │             │
              content_mode=standard   content_mode=policy
                        │             │
                        ▼             ▼
        ┌───────────────┐   ┌─────────────────────────────┐
        │   内容提取      │   │        内容提取               │
        │  标准七实体模式  │   │       政策专用模式             │
        │  v5 五步流水线  │   │  PolicyTheme + PolicyItem    │
        │                │   │  background / urgency /      │
        │  Fact          │   │  metric / enforcement        │
        │  Assumption    │   │                              │
        │  ImplicitCond  │   │  ↓ compare_policies()        │
        │  Conclusion    │   │  change_type 标注             │
        │  Prediction    │   │  (新增/调整/延续/删除)         │
        │  Solution      │   └──────────────┬──────────────┘
        │  Theory        │                  │
        └───────┬────────┘                  │
                │                           │
                └──────────────┬────────────┘
                               │
           ┌───────────────────▼──────────────────────┐
           │              通用判断                       │
           │  作者档案（AuthorProfiler）                  │
           │  + 利益冲突检测（has_conflict）               │
           │  credibility_tier 1-5 / assessment_summary │
           └───────────────────┬──────────────────────┘
                               │
           ┌───────────────────▼──────────────────────┐
           │              事实验证                       │
           │  标准模式：七实体验证（Tavily + LLM）         │
           │  政策模式：PolicyItem 执行情况追踪             │
           │  execution_status（已落地/推进中/受阻/...）   │
           └──────────────────────────────────────────┘
```

---

## 6a. 下游系统对接

Anchor 是三层投资分析系统的信息理解层，产出结构化实体供下游消费：

```
┌─────────────────────────────────────────────────────────┐
│  Anchor（理解模型）                                        │
│  文章/文件 → 结构化实体                                     │
│                                                           │
│  产出：                                                    │
│  ├── 六实体 DAG（财经分析/公司调研）                          │
│  ├── PolicyTheme / PolicyItem（政策文件）                    │
│  ├── Industry entities（产业研究）                           │
│  ├── EarningsAnalysis（公司财报）                            │
│  ├── PaperAnalysis（技术论文）                               │
│  └── AuthorProfile（作者档案）                               │
├─────────────────────────────────────────────────────────┤
│  Axion（产业模型）— 独立项目                                 │
│  Anchor 实体 + 市场数据 → 世界格局建模 → 价格预测             │
├─────────────────────────────────────────────────────────┤
│  Polaris（量化投资）— 独立项目                               │
│  Axion 预测 + 当前定价 → 投资决策                            │
└─────────────────────────────────────────────────────────┘
```

**Anchor 的输出接口**：所有实体通过 SQLite DB 暴露，Axion 直接读取 Anchor 的数据库表。

**代码依赖**：Anchor 通过 `pip install -e ../Axion` 依赖 Axion Python 包。产业知识库表（CanonicalPlayer, PlayerAlias, SupplyNode, LayerSchema）由 Axion 定义，Anchor 的 `models.py` re-export 以保持兼容。归一化逻辑（玩家匹配、节点去重、指标 schema 匹配）由 `axion.normalization` 提供。

详见：
- Axion PRD — 产业模型架构（五层：周期/状态/力量/传导/学习）、IPOCC 政策分析模型、公司分析模型
- Polaris PRD — 量化投资系统

---

## 7. 数据模型

### 7.1 基础设施表

| 表名 | 描述 | 关键字段 |
|------|------|---------|
| `authors` | 观点作者档案 | name, platform, platform_id, role, expertise_areas, known_biases, credibility_tier(1-5), profile_note(≤80字), situation_note(≤150字) |
| `raw_posts` | 原始帖子/文档 | source, external_id, content, enriched_content, posted_at, is_processed, **content_domain**(政策\|产业\|公司\|期货\|技术), **content_nature**(一手信息\|第三方分析), **assessment_summary**(≤80字), **has_conflict**(bool), **conflict_note**(≤80字), content_type(过渡兼容), issuing_authority, authority_level, content_summary, notion_page_id, policy_delta |
| `monitored_sources` | 监控源 | url, source_type, platform, fetch_interval_minutes, last_fetched_at |
| `author_groups` | 跨平台作者实体 | canonical_name, canonical_role |
| `topics` | 话题标签 | name, description, tags |

### 7.2 七实体表

**四层表达模型（Fact/Conclusion/Theory 区分标准）：**

| 层次 | 作者在做什么 | 实体类型 |
|------|-------------|---------|
| 1. 发生了什么 | 引用事件/数据/现象（有外部来源） | **Fact**（事实） |
| 2. 解释发生了什么 | 梳理/归纳现状（专家解读） | **Conclusion**（结论） |
| 3. 为什么发生 | 归因/因果推理（必然带 Fact 依据） | **Conclusion**（结论） |
| 4. 作者的理论框架 | 建立模型/理论/原则 → 推出预测/行动 | **Theory**（理论框架） |

**关键区分规则**：
- 即使 A、B 都是可验证事实，「A 导致 B」属于归因推理，归为 Conclusion；A 和 B 分别作为 Fact 支撑
- Theory 是作者用来推演预测和行动的理论框架（如"债务大周期理论""全天候策略"），由 Fact/Conclusion 支撑，向下推出 Prediction/Solution
- Theory 可被事实说明（fact_supports_theory），如"这也是为什么这套理论成立"
- Theory 可延伸出子理论（theory_supports_theory），也可反过来支撑结论（theory_supports_conclusion）
- Theory 和 Solution 不纳入事实验证

辅助实体：
- **Assumption**（假设）：明确的"如果 X 则 Y"前提条件
- **ImplicitCondition**（隐含条件）：推理依赖但作者未说出的暗含前提
- **Solution**（解决方案）：具体行动建议（买/卖/持有/倡导等）

| 实体 | 表名 | 核心字段 | 事实验证 verdict |
|------|------|---------|----------------|
| Fact（事实依据）| `facts` | claim(≤120字), verifiable_statement, temporal_type, temporal_note, summary(≤15字) | `fact_verdict`: credible\|vague\|unreliable\|unavailable |
| Assumption（假设条件）| `assumptions` | condition_text(≤120字), verifiable_statement, temporal_note, summary | `assumption_verdict`: high_probability\|medium_probability\|low_probability\|unavailable |
| ImplicitCondition（隐含条件）| `implicit_conditions` | condition_text(≤120字), is_obvious_consensus, summary | `implicit_verdict`: consensus\|contested\|false |
| Conclusion（结论）| `conclusions` | claim(≤120字), author_confidence, is_core_conclusion, is_in_cycle, summary | `conclusion_verdict`: confirmed\|refuted\|partial\|unverifiable\|pending |
| Prediction（预测）| `predictions` | claim(≤120字), temporal_note, temporal_validity, monitoring_start, monitoring_end, author_confidence, summary | `prediction_verdict`: pending\|accurate\|directional\|off_target\|wrong |
| Solution（解决方案）| `solutions` | claim(≤120字), action_type, action_target, action_rationale, summary | 不验证 |
| Theory（理论框架）| `theories` | claim(≤120字), summary(≤15字) | 不验证 |

### 7.3 关系边表

```sql
relationships (
    source_type  -- fact|assumption|implicit_condition|conclusion|prediction|solution|theory|policy_item
    source_id
    target_type
    target_id
    edge_type    -- 见下方枚举
    note         -- ≤80字说明
)
```

**EdgeType 枚举（13种）：**

| EdgeType | 含义 |
|----------|------|
| `fact_supports_conclusion` | 事实支撑结论 |
| `assumption_conditions_conclusion` | 假设条件结论 |
| `implicit_conditions_conclusion` | 隐含前提制约结论 |
| `conclusion_supports_conclusion` | 结论支撑更高层结论 |
| `conclusion_leads_to_prediction` | 结论推出预测 |
| `conclusion_enables_solution` | 结论使解决方案成立 |
| `policy_supports_conclusion` | 政策条目支撑政策结论 |
| `fact_supports_theory` | 事实支撑理论 |
| `conclusion_supports_theory` | 结论支撑理论 |
| `theory_supports_theory` | 理论延伸出子理论 |
| `theory_supports_conclusion` | 理论支撑结论 |
| `theory_leads_to_prediction` | 理论推出预测 |
| `theory_enables_solution` | 理论使解决方案成立 |

### 7.4 政策专用表（Policy Mode）

**PolicyTheme（政策主旨）**

| 字段 | 类型 | 说明 |
|------|------|------|
| theme_name | VARCHAR | 主旨名称（≤6字，如"财政政策""对台政策"） |
| background | TEXT | 背景与目的（≤200字，内容提取从文件推断"为什么是现在"） |
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
| execution_status | VARCHAR | implemented\|in_progress\|stalled\|not_started\|unknown（事实验证填写） |
| execution_note | VARCHAR | ≤80字执行情况说明 |

### 7.5 评估与统计表

| 表名 | 描述 |
|------|------|
| `author_stance_profiles` | 作者立场档案（stance_distribution JSON, dominant_stance, audience, core_message, author_summary） |
| `post_quality_assessments` | 单篇内容质量评估 |
| `author_stats` | 作者综合统计（准确率、可信度评分） |

### 7.6 产业链研究表（Industry Research Mode）

**全局去重实体（跨文章共享）：**

**CanonicalPlayer（规范化行业参与者）**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 全局唯一 ID |
| canonical_name | VARCHAR UNIQUE | 规范名称（英文优先，如 "TSMC"） |
| entity_type | VARCHAR | company \| government \| research_org \| consortium \| individual |
| headquarters | VARCHAR | 总部所在地 |
| description | TEXT | ≤120字简介 |

**PlayerAlias（多语言别名）**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | |
| canonical_player_id | INT FK | → canonical_players.id |
| alias | VARCHAR | 别名（如"台积电""TSMC""台灣積體電路"） |
| language | VARCHAR | zh \| en \| ja \| ko \| other |

**SupplyNode（供应链节点）**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 全局唯一 ID |
| industry_chain | VARCHAR | 所属产业链（如 "ai", "ev", "semiconductor"） |
| tier_id | INT | 层级编号（0=最上游） |
| layer_name | VARCHAR | 层名称（如"能源/电力""材料/有色金属"） |
| node_name | VARCHAR | 节点名称（如"HBM 封装""EUV 光刻"） |
| description | TEXT | ≤120字说明 |

唯一约束：`(industry_chain, tier_id, node_name)`

**LayerSchema（层级关键指标定义 —— "必答题"）**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | |
| industry_chain | VARCHAR | 产业链（如 "ai"） |
| tier_id | INT | 层级编号 |
| metric_name | VARCHAR | 指标名称（如 "PUE""良率""token成本"） |
| unit | VARCHAR | 单位（如 "%""$/kWh""TFLOPS"） |
| description | VARCHAR | 指标含义说明 |

唯一约束：`(industry_chain, tier_id, metric_name)`

**每篇文章提取的实体（per-article）：**

**Issue（关键问题）**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | |
| raw_post_id | INT FK | 来源文章 |
| supply_node_id | INT FK | 所属供应链节点（nullable） |
| issue_text | VARCHAR | ≤120字问题描述 |
| severity | VARCHAR | critical \| high \| medium \| low |
| status | VARCHAR | active \| mitigating \| resolved \| emerging |
| resolution_progress | VARCHAR | ≤80字解决进展 |
| summary | VARCHAR | ≤15字摘要 |

**TechRoute（技术路线）**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | |
| raw_post_id | INT FK | 来源文章 |
| supply_node_id | INT FK | 所属供应链节点（nullable） |
| route_name | VARCHAR | 技术路线名称（如"CoWoS 先进封装""HBM4"） |
| maturity | VARCHAR | research \| pilot \| scaling \| mature |
| competing_routes | JSON | 竞争路线名称列表 |
| summary | VARCHAR | ≤15字摘要 |

**Metric（提取的指标值）**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | |
| raw_post_id | INT FK | 来源文章 |
| supply_node_id | INT FK | 所属供应链节点（nullable） |
| canonical_player_id | INT FK | 关联企业（nullable） |
| metric_name | VARCHAR | 指标名称 |
| metric_value | VARCHAR | 值（文本形式，保留原始精度） |
| unit | VARCHAR | 单位 |
| time_reference | VARCHAR | 时间参考（如"2025Q4""2026E"） |
| evidence_score | INT | 论据充分度：1=无论据仅提及 / 2=有简单论据 / 3=有详细数据或推导 |
| is_schema_metric | BOOLEAN | 是否匹配 LayerSchema 中的预定义指标 |

**Metric 重要性排序规则：**
- 所有指标（预定义 + 发现型）平等对待
- 排序公式：`mention_count × avg(evidence_score)`
- `mention_count`：该指标在所有文章中被提及的次数
- `evidence_score`（1-3）：作者是否给出支撑论据
- 预定义指标因高频提及自然排前；发现型指标若论据充分也可排前

**产业链研究扩展关系边（追加到 EdgeType 枚举）：**

| EdgeType | source → target | 含义 |
|----------|----------------|------|
| `player_dominates_node` | Player → SupplyNode | 企业在该节点占主导地位 |
| `player_enters_node` | Player → SupplyNode | 企业进入该节点（新玩家） |
| `issue_cascades_issue` | Issue → Issue | 问题级联传导（跨层） |
| `issue_blocks_node` | Issue → SupplyNode | 问题阻碍供应链节点 |
| `issue_constrains_player` | Issue → Player | 问题限制企业发展 |
| `techroute_mitigates_issue` | TechRoute → Issue | 技术路线缓解问题 |
| `techroute_competes_techroute` | TechRoute → TechRoute | 技术路线竞争 |
| `metric_evidences_issue` | Metric → Issue | 指标数据支撑问题判断 |
| `fact_supports_issue` | Fact → Issue | 事实支撑问题存在 |
| `conclusion_about_player` | Conclusion → Player | 结论涉及企业 |
| `conclusion_about_node` | Conclusion → SupplyNode | 结论涉及供应链节点 |

**示例：AI 产业链 8 层 LayerSchema**

| tier_id | layer_name | 预定义指标（举例） |
|---------|-----------|-----------------|
| 0 | 能源/电力 | 电价($/kWh), 可再生占比(%), 供电缺口(GW), PUE |
| 1 | 材料/有色金属 | 铜价($/ton), HBM良率(%), 光刻胶纯度, 稀土配额(ton) |
| 2 | 设备 | EUV产能(台/年), 交付周期(月), 国产化率(%) |
| 3 | EDA/IP | 先进制程支持率(%), license成本, 国产EDA覆盖率(%) |
| 4 | 制造/代工 | 良率(%), 产能利用率(%), 制程节点(nm), 代工报价($/wafer) |
| 5 | 芯片/组件 | 算力(TFLOPS), 功耗比(TFLOPS/W), 库存周期(天), HBM容量(GB) |
| 6 | 数据中心 | 机柜数, 上架率(%), PUE, capex($/MW), 液冷渗透率(%) |
| 7 | 应用/模型 | token成本($/Mtok), 推理延迟(ms), MAU, 模型参数量(B) |

---

## 8. 三条链路设计

### 内容提取（`anchor/chains/content_extraction.py`）

**职责**：URL → 七实体 / 政策实体 → DB

**流程：**
```
URL
 → process_url()           — 采集 RawPost（去重、存库）
 → assess_post()           — 通用判断前置分类（确定 content_mode）
 → Extractor.extract()     — 标准模式或政策模式提取
 → fetch_prior_year_and_compare()  — 政策模式：自动搜索上年文档并比对
 → 返回所有实体汇总
```

**内容提取在 RawPost 上写入的字段：**
- `content_summary`（Step5 叙事摘要）
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

- **content_domain（5种）**：政策 \| 产业 \| 公司 \| 期货 \| 技术
- **content_nature（2种）**：一手信息 \| 第三方分析
- **content_type（过渡兼容）**：财经分析 \| 市场动向 \| 产业链研究 \| 公司调研 \| 技术论文 \| 公司财报 \| 政策解读

**摘要（assessment_summary）**：什么人在干什么事（≤80字描述性句子），替代旧版 author_intent

**利益冲突（has_conflict + conflict_note）**：
- has_conflict=true：推销产品/服务/付费内容、持有相关头寸、受雇于分析对象
- has_conflict=false：独立分析、学术研究、官方信息发布

**实际发言人识别（real_author_name）：**
- 标题点名某人（如"付鹏最新分析"）→ 填该人姓名
- 个人品牌账号（如"付鹏的财经世界"）→ 提取真实发言人
- 转载/解读账号 → 找被采访/被分析的人；无法判断填 null

**立场分析**：已移除（v7.1），AuthorStanceProfile 表保留但不再写入新数据。

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

**职责**：检验七实体的可信度；追踪政策执行情况

**验证门控（v7.1）**：
- `content_nature = "一手信息"` → 跳过标准实体验证（一手信息不验证）
- 政策执行追踪（PolicyItem）始终运行，不受门控影响
- `content_nature = "第三方分析"` 或旧数据（无 content_nature）→ 正常执行验证

**标准模式（5步验证，仅第三方分析）：**

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

## 9. 内容路由逻辑

v7.1 采用统一路由函数 `resolve_content_mode(domain, nature, content_type)`，所有调用点共享：

```python
# anchor/chains/general_assessment.py
def resolve_content_mode(domain, nature, content_type=None) -> str:
    """2D（domain × nature）→ content_mode，含旧 content_type 降级兼容。"""
    if domain and nature:
        if domain == "政策" and nature == "一手信息": return "policy"
        if domain == "产业": return "industry"
        if domain == "公司" and nature == "一手信息": return "earnings"
        if domain == "技术" and nature == "一手信息": return "tech_paper"
        return "standard"
    # 旧数据降级
    if content_type in {"政策宣布", "政策解读"}: return "policy"
    if content_type in {"产业链研究", "财经分析"}: return "industry"
    if content_type == "技术论文": return "tech_paper"
    if content_type == "公司财报": return "earnings"
    return "standard"
```

**2D 路由表：**

| domain | nature | content_mode | 说明 |
|--------|--------|-------------|------|
| 政策 | 一手信息 | policy | 政策原文 → PolicyTheme + PolicyItem |
| 政策 | 第三方分析 | standard | 政策解读 → 六实体 |
| 产业 | 一手/第三方 | industry | 产业结构分析 |
| 公司 | 一手信息 | earnings | SEC 披露 → EarningsAnalysis |
| 公司 | 第三方分析 | standard | 公司调研 → 六实体 |
| 技术 | 一手信息 | tech_paper | 论文 → PaperAnalysis |
| 技术 | 第三方分析 | standard | 技术趋势分析 → 六实体 |
| 期货 | 一手/第三方 | standard | 宏观/市场分析 → 六实体 |

**路由影响：**

| 维度 | 标准模式（standard） | 政策模式（policy） | 产业链模式（industry） | 财报模式（earnings） | 论文模式（tech_paper） |
|------|---------------------|----------------------|------------------------|---------------------|---------------------|
| 提取管线 | v6 Top-Down（六实体） | Step1Policy | 产业实体 + 七实体 | EarningsAnalysis | PaperAnalysis |
| 事实验证行为 | 七实体验证 | 执行追踪 + 七实体验证 | 七实体验证 | — | — |
| 验证门控 | content_nature="第三方分析"才验证 | 政策追踪始终执行 | 同标准模式 | 一手信息跳过验证 | 一手信息跳过验证 |

**监控流水线内容过滤**（`run_monitor.py`）：
- 视频 < 180 秒 → 跳过
- 文章正文 < 200 字 → 跳过
- 付费墙检测命中 → 跳过

---

## 10. 七实体提取（标准模式）

六实体：Fact / Assumption / Conclusion / Prediction / Solution / Theory
（v6 不再生成 ImplicitCondition，简化流程。DB 表保留，新数据不写入。）

**四层表达模型（分类基础）：**
- **Fact**（事实）= 有外部来源的事件/数据/现象（作者引用现实）
- **Conclusion**（结论）= 作者的解读/归因判断（含「A导致B」型归因，A和B分别作为Fact支撑）
- **Theory**（理论框架）= 作者建立的模型/理论/原则，用来推演预测和行动（不验证）
- **Prediction**（预测）= 明确指向未来，含"将/未来/会/预计"等时态词
- **Assumption**（假设）= 明确的"如果X则Y"前提
- **Solution** = 具体行动建议（买/卖/持有/倡导等）

### 10.1 v6 Top-Down Pipeline（默认）

v6 采用 top-down 方法：先找核心结论和理论，再按相关性过滤提取支撑实体，最后抽象/归一化/建关系。相关性过滤天然限制实体数量，不需要机械数量约束。

| 步骤 | 提示词文件 | 输入 | 输出 | Token 预算 |
|------|-----------|------|------|-----------|
| Call 1 (Steps 1+2) | v6_step1_anchors.py | 全文 + 上下文 | 核心结论 + 关键理论 | 2000 |
| Call 2 (Steps 3+4) | v6_step2_supporting.py | 全文 + 锚点 | 事实/子结论/假设/预测/方案 | 4000 |
| Call 3 (Step 5) | v6_step3_abstract.py | 全部实体 | 精炼后实体（确保主体/对象） | 3000 |
| Call 4 (Step 6) | v6_step4_merge.py | 精炼实体 | 合并 + 归一化 + 重新编号 | 3000 |
| Call 5 (Step 7) | v6_step5_relationships.py | 最终实体 | 有向边列表 | 3000 |
| Call 6 (Summary) | v5_step5_summary.py | 核心结论 + 事实 | 叙事摘要 | 1000 |
| Python | extractor.py | 边 + 实体 | 验证边类型、Theory cap、周期检测 | — |

**v6 数据流：**
```
文章内容 → Call 1 → TopDownAnchorsResult
         → Call 2 → SupportingScanResult
         → Python → List[TypedEntity] (统一格式)
         → Call 3 → AbstractedResult
         → Call 4 → MergedResult
         → Call 5 → RelationshipResult
         → Call 6 → article_summary
         → Python → 后处理 + DB Write
```

**v6 vs v5 关键差异：**
- **Top-down** 提取：先锚点后支撑（v5 是先全量提取再分类）
- **相关性过滤**替代数量约束（v5 依赖机械节点数量限制）
- **显式抽象步骤**：确保每个实体表达包含明确主体/对象
- **LLM 建关系**替代规则推导（v5 由 Python 根据类型推导 edge_type）
- **不生成 ImplicitCondition**（v5 Step4 专门生成）

**DAG 分析（Python 后处理）：**
- `is_core_conclusion`：无出边指向其他结论的叶结论（最终判断）
- `is_in_cycle`：DFS 检测环路，环内结论在事实验证 Step 4 跳过
- Theory cap = 2：超出的 Theory 降级为 Conclusion
- edge_type 验证：LLM 给出的 edge_type 按 source/target 类型自动修正

### 10.2 v5 Bottom-Up Pipeline（保留）

设置 `DEFAULT_PROMPT_VERSION = "v5"` 可切回 v5 流水线。

| 步骤 | 提示词文件 | 输入 | 输出 | Token 预算 |
|------|-----------|------|------|-----------|
| Step 1 | v5_step1_claims.py | 全文 + 上下文 | 原始声明列表 + 边列表 | 4000 |
| Step 2 | v5_step2_merge.py | 声明列表 | 去重合并方案 | 2000 |
| Step 3 | v5_step3_classify.py | 声明 + DAG 结构 | 每条声明的实体类型 | 4000 |
| Step 4 | v5_step4_implicit.py | 推理对（前提→结论） | 隐含条件列表 | 3000 |
| Step 5 | v5_step5_summary.py | 核心结论 + 关键事实 | 叙事摘要（2-3句） | 1000 |

**Step 5 资本流向规则：**
当同时存在防御资产（HALO 板块：黄金/国债/避险货币）和 AI 受益资产时，摘要需明确区分两条资本流向，不可笼统描述为"市场避险"。

---

## 11. 政策模式

### 11.1 提取框架（五维分析）

| 维度 | 链路 | 字段 | 说明 |
|------|------|------|------|
| ① 定调术语（紧迫度） | 内容提取 | `PolicyItem.urgency` | mandatory/encouraged/pilot/gradual |
| ② 关键指标（硬约束） | 内容提取 | `metric_value + is_hard_target` | 有量化数值+目标年份则为硬约束 |
| ③ 组织保障 | 内容提取 | `enforcement_note + has_enforcement_teeth` | 谁牵头、是否纳入考核 |
| ④ 背景与目的 | 内容提取 | `PolicyTheme.background` | 从文件推断"为什么是现在" |
| ⑤ 发文机关 | 通用判断 | `issuing_authority + authority_level` | 顶层设计\|部委联合\|部委独立 |

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

### 11.2 双文档比对（`compare_policies`）

```
current_post（当年）+ prior_post（上年）
    → 读取两年的 PolicyItem 列表
    → LLM 比对 → change_type 标注：新增|调整|延续
    → deleted_summaries → 写入当年 Fact（[删除]前缀）
    → 幂等：已有 change_type 则跳过
```

**政策变化类型说明：**

| 变化类型 | 含义 |
|---------|------|
| 新增 | 本年首次出现的政策方向或措施 |
| 调整 | 相比上年有实质性变化（力度、目标、表述等） |
| 延续 | 与上年基本一致，继续推进 |
| 删除 | 上年有、本年未再提及（显示政策退出或降级） |

### 11.3 自动搜索上年文档（`fetch_prior_year_and_compare`）

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

### 11.4 执行追踪（事实验证）

**execution_status 枚举：**

| 状态 | 含义 |
|------|------|
| implemented（✅ 已落地） | 有明确数据或公告证明 |
| in_progress（🔄 推进中） | 已有具体行动但尚未完成 |
| stalled（⚠️ 受阻） | 推进受阻或明显低于预期 |
| not_started（⏳ 未启动） | 尚无任何落地迹象 |
| unknown（❓ 未知） | 搜索结果不足以判断 |

**追踪优先级**：is_hard_target=True > urgency=mandatory > 其余

---

## 12. 产业链研究模式

### 12.1 触发条件

```python
content_mode = "industry" if content_type == "产业链研究" else ...
```

当通用判断分类结果为 `content_type = "产业链研究"` 时，内容提取切换为产业链研究提取管线。

### 12.2 提取目标

产业链研究模式在标准七实体之外，额外提取 5 类产业结构化实体：

| 实体 | 全局去重 | 说明 |
|------|---------|------|
| Player（行业参与者） | 是 | 企业/机构/组织，跨文章归一化 |
| SupplyNode（供应链节点） | 是 | 产业链层级中的具体环节 |
| Issue（关键问题） | 否（per-article） | 子行业面临的核心问题/瓶颈 |
| TechRoute（技术路线） | 否（per-article） | 竞争性技术方案 |
| Metric（关键指标） | 否（per-article） | 量化数据点，可跨文章聚合 |

### 12.3 产业知识库（Axion 管理）

跨文章归一化的产业实体由 **Axion** 项目定义和维护：
- `CanonicalPlayer` / `PlayerAlias` — 产业玩家归一化（`axion.models`）
- `SupplyNode` — 供应链节点去重（`axion.models`）
- `LayerSchema` — 层级指标定义 + 种子数据（`axion.models` + `axion.seeds`）

归一化逻辑（别名匹配、节点去重、schema 匹配）由 `axion.normalization` 提供，Anchor industry pipeline 调用。

### 12.4 Player 归一化流程

Player 必须全局去重，支持多语言别名：

```
文章提到 "台积电"
  → 查 player_aliases WHERE alias = "台积电"
  → 找到 canonical_player_id = 42 (canonical_name = "TSMC")
  → 复用已有实体

文章提到 "Taiwan Semiconductor"
  → 查 player_aliases → 同样匹配到 TSMC
  → 复用

文章提到全新企业 "某初创公司"
  → 无匹配 → 创建新 CanonicalPlayer + PlayerAlias
```

归一化策略：
- **精确匹配**：先查 player_aliases 表
- **模糊匹配**：LLM 判断"台積電"与"台积电"是否为同一实体
- **canonical_name 规则**：英文优先，取最常用的国际名称（如 TSMC 而非 Taiwan Semiconductor Manufacturing Company）

### 12.5 SupplyNode 归一化

SupplyNode 按 `(industry_chain, tier_id, node_name)` 去重：
- 同一产业链、同一层级、同一节点名 → 复用
- 不同产业链可以有同名节点（如"铜"可同时属于 AI 产业链 Tier 1 和新能源车产业链 Tier 2）

### 12.6 Metric 重要性排序

所有指标平等对待，无需人工审核：

```
importance_score = mention_count × avg(evidence_score)
```

| evidence_score | 含义 | 示例 |
|----------------|------|------|
| 1 | 仅提及，无论据 | "台积电产能不足" |
| 2 | 有简单论据 | "台积电产能不足，CoWoS 月产能约 15K 片" |
| 3 | 有详细数据或推导 | "台积电 CoWoS 月产能 15K 片，NVIDIA 单季需求 20K+，缺口 25%以上" |

- **预定义指标**（LayerSchema）：因多篇报告都会提及，mention_count 自然高 → 排前
- **发现型指标**：若论据充分（evidence_score=3），即使低频也可能排前，这正是独到见解或重要遗漏的信号

### 12.7 Issue 跨层传导

Issue 通过 `issue_cascades_issue` 边建立跨层传导链：

```
[Tier 0] 美国电力基础设施老化（severity=critical, status=active）
  ↓ cascades
[Tier 6] 数据中心选址困难，电力供应不足（severity=high, status=active）
  ↓ cascades
[Tier 7] AI 训练产能受限，成本上升（severity=high, status=mitigating）
  ↓ mitigated_by (techroute_mitigates_issue)
[TechRoute] 小型模块化核反应堆（SMR）（maturity=pilot）
```

### 12.8 产业链提取管线（规划）

```
文章内容
  → Call 1: 识别 industry_chain + 涉及的 tier 范围
  → Call 2: 提取 Player + SupplyNode（全局去重匹配）
  → Call 3: 提取 Issue + TechRoute + Metric
  → Call 4: 建立产业实体关系边
  → Call 5: 标准七实体提取（复用 v6 pipeline）
  → Call 6: 叙事摘要
  → Python: Player/SupplyNode 归一化 + Metric 聚合 + DB Write
```

**与标准模式的关系**：产业链研究模式是标准七实体模式的**超集**——同时提取观点实体（Fact/Conclusion/Prediction 等）和产业结构实体（Player/SupplyNode/Issue 等），通过扩展关系边（如 `conclusion_about_player`）连接两层。

### 12.9 行业无关设计

产业链模型**不绑定任何特定行业**：
- `industry_chain` 字段区分不同产业链（"ai"/"ev"/"semiconductor"/...）
- `tier_id` 是整数，层数不固定（AI = 8 层，新能源车可能 = 6 层）
- `LayerSchema` 按 `(industry_chain, tier_id)` 定义，每条产业链独立维护
- 同一企业（如三星）可出现在多条产业链的不同 SupplyNode 中

---

## 12b. 技术论文模式

### 触发条件

```
content_mode = "tech_paper" if content_type == "技术论文" else ...
```

通用判断分类为 `"技术论文"` 时触发。不复用 v6 观点提取——论文的分析框架（问题/方法/效果）与观点提取（事实/结论/理论）本质不同。

### Pipeline（2 次 LLM 调用）

```
extract_paper(raw_post, session, content, ...)
  │
  ├── Call 1: 论文结构 (4000 tokens)
  │   → paper_type, problem_statement, methodology, key_contributions
  │   → is_relevant_content 过滤
  │
  ├── Call 2: 实验评估 (4000 tokens)
  │   → results_summary, limitations, ablation_summary, has_ablation
  │   → datasets_used, baselines_compared, metrics_reported
  │   → paper_summary (≤50字中文摘要)
  │
  └── DB Write: PaperAnalysis 表 (1:1 with RawPost)
```

### PaperAnalysis 表

| 字段 | 类型 | 说明 |
|------|------|------|
| raw_post_id | FK → raw_posts | 唯一索引 |
| paper_type | str? | empirical\|theoretical\|survey\|benchmark\|system |
| problem_statement | str | 问题陈述（含已有方法不足） |
| methodology | str | 核心方法/创新点 |
| key_contributions | JSON list | 3-5 条贡献 |
| results_summary | str | 效果总结（含数值对比） |
| limitations | str? | 局限性（区分作者自述/潜在问题） |
| has_ablation | bool | 是否有消融实验 |
| ablation_summary | str? | 消融实验发现 |
| paper_summary | str? | ≤50字中文摘要 |
| datasets_used | JSON list | 数据集名称 |
| baselines_compared | JSON list | 对比基线 |
| metrics_reported | JSON list | {metric_name, best_value} |

### arXiv URL 处理

arXiv URL 自动重定向到 ar5iv（HTML 渲染版本），Jina Reader 解析效果远优于 PDF：
- `https://arxiv.org/abs/2501.12948` → `https://ar5iv.labs.arxiv.org/html/2501.12948`
- `https://arxiv.org/pdf/2501.12948v2` → `https://ar5iv.labs.arxiv.org/html/2501.12948v2`

### 关键文件

| 文件 | 说明 |
|------|------|
| `anchor/extract/schemas/paper.py` | Pydantic schemas |
| `anchor/extract/prompts/paper/step1_structure.py` | Prompt: 论文结构 |
| `anchor/extract/prompts/paper/step2_evaluation.py` | Prompt: 实验评估 |
| `anchor/extract/pipelines/paper.py` | 核心管线 |
| `anchor/collect/web.py` | arXiv→ar5iv 重定向 |

---

## 12c. 公司财报分析模式

### 触发条件

```
content_mode = "earnings" if content_type == "公司财报" else ...
```

通用判断分类为 `"公司财报"` 时触发。支持多国年报格式：美国 SEC 10-K/10-Q、中国 CSRC 年报/半年报、欧盟 IFRS 年报、日本有価証券報告書等。第三方分析师对财报的解读应归入 `"公司调研"`。

### Pipeline（4 次 LLM 调用）

```
extract_earnings(raw_post, session, content, ...)
  │
  ├── Call 1: 公司概况 + 护城河 (5000 tokens)
  │   → company_name, ticker, fiscal_year, industry
  │   → business_description, revenue_segments
  │   → moat_type, moat_description, competitive_advantages, key_risks
  │   → is_relevant_content 过滤（非正式财报 → 跳过）
  │
  ├── Call 2: 核心财务指标 (6000 tokens)
  │   → revenue, net_income, operating_income (当年/去年)
  │   → eps, margins (gross/operating/net), roe, roic
  │   → debt, equity, D/E ratio, cash
  │   → OCF, capex, FCF, owner_earnings
  │   → cash_flow_vs_earnings (质量判断)
  │
  ├── Call 3: 管理层 + 资本配置 (4000 tokens)
  │   → dividends, buybacks, acquisitions
  │   → capex classification (growth vs maintenance)
  │   → management_tone, forward_statements
  │   → insider_ownership_note, compensation_note
  │
  ├── Call 4: 估值参考 (3000 tokens)
  │   → 基于 Call 2 数据的简化 DCF
  │   → intrinsic_value_low / intrinsic_value_high
  │   → valuation_method, assumptions_note
  │   → book_value_per_share, earnings_yield
  │   → earnings_summary (≤80字中文)
  │
  └── DB Write: EarningsAnalysis 表 (1:1 with RawPost)
```

### EarningsAnalysis 表

所有财务数值用 `str` 存储（保留原始单位如 "$146.0B"、"35.8%"），避免单位转换丢失。

| 字段 | 类型 | 说明 |
|------|------|------|
| raw_post_id | FK → raw_posts | 唯一索引 |
| company_name | str? | 公司全称 |
| ticker | str? | 股票代码 |
| fiscal_year | str? | "FY2025" |
| industry | str? | 行业 |
| business_description | str? | ≤200字业务描述 |
| revenue_segments | JSON list | [{name, revenue, percentage_of_total}] |
| moat_type | str? | brand\|switching_cost\|network_effect\|cost_advantage\|intangible\|none\|multiple |
| moat_description | str? | ≤300字护城河描述 |
| competitive_advantages | JSON list | 竞争优势 |
| key_risks | JSON list | 关键风险 |
| revenue_current / revenue_prior | str? | 营收（当年/去年） |
| net_income_current / net_income_prior | str? | 净利润 |
| operating_income_current / operating_income_prior | str? | 营业利润 |
| eps_basic / eps_diluted | str? | 每股收益 |
| gross_margin / operating_margin / net_margin | str? | 利润率 |
| roe / roic | str? | 资本回报率 |
| total_assets / total_equity / total_debt | str? | 资产负债 |
| cash_and_equivalents / debt_to_equity | str? | 现金 / 杠杆 |
| operating_cash_flow / capex / free_cash_flow | str? | 现金流 |
| owner_earnings / owner_earnings_note | str? | Owner Earnings + 说明 |
| cash_flow_vs_earnings | str? | 现金流质量判断 |
| dividends_paid / shares_repurchased | str? | 分红 / 回购 |
| buyback_note / acquisition_spending | str? | 回购策略 / 收购支出 |
| capex_classification / capex_note | str? | growth\|maintenance\|mixed + 说明 |
| management_tone | str? | 管理层措辞 |
| key_forward_statements | JSON list | 前瞻性陈述 |
| insider_ownership_note / compensation_note | str? | 高管持股 / 薪酬 |
| intrinsic_value_low / intrinsic_value_high | str? | 内在价值范围 |
| valuation_method / assumptions_note | str? | 估值方法 / 假设 |
| book_value_per_share / earnings_yield | str? | 每股净资产 / 盈利收益率 |
| earnings_summary | str? | ≤80字中文摘要 |

### 设计要点

- **全文传入**：每次 LLM 调用发完整年报文本，靠 prompt 聚焦不同 section
- **所有数值用 str**：保留原始单位，不做数值转换
- **Graceful degradation**：任一 Call 失败写部分结果，不阻塞管线
- **估值无股价**：10-K 内无市价，只基于 FCF/EPS 给内在价值范围
- **公司财报 vs 公司调研**：正式 SEC 披露文件 → 公司财报，第三方分析/研报 → 公司调研

### 关键文件

| 文件 | 说明 |
|------|------|
| `anchor/extract/schemas/earnings.py` | Pydantic schemas |
| `anchor/extract/prompts/earnings/step1_business.py` | Prompt: 公司概况 + 护城河 |
| `anchor/extract/prompts/earnings/step2_financial.py` | Prompt: 核心财务指标 |
| `anchor/extract/prompts/earnings/step3_management.py` | Prompt: 管理层 + 资本配置 |
| `anchor/extract/prompts/earnings/step4_valuation.py` | Prompt: 估值参考 |
| `anchor/extract/pipelines/earnings.py` | 核心管线 |

---

## 13. 事实验证规则

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

## 14. 监控流水线

### 14.1 架构概览（`run_monitor.py`）

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
      ├── run_assessment()       — 内容分类 + 作者档案
      ├── Extractor.extract()   — 仅处理 content_type=财经分析
      └── sync_post_to_notion() — Notion 同步
```

### 14.2 内容质量过滤规则

| 条件 | 结果 |
|------|------|
| `raw_metadata.is_video_only=True` + youtube_redirect 存在 | 递归改抓 YouTube 链接 |
| `raw_metadata.is_video_only=True` + 无重定向 | 跳过（video_only） |
| 视频时长 `duration_s < 180` 秒 | 跳过（video_short） |
| 文章正文 < 200 字 | 跳过（text_short） |
| 付费墙正则匹配命中 | 跳过（paywall_skip） |
| `content_type != "财经分析"` | 跳过（non_market） |

### 14.3 feed_fetcher 支持的平台（`anchor/monitor/feed_fetcher.py`）

| 平台 | 方法 |
|------|------|
| RSS / Atom（Substack、Project Syndicate、IMF 等） | feedparser |
| YouTube 频道 | yt-dlp 平铺视频列表 |
| Bilibili 空间 | 官方 API |
| Weibo 用户 | 公开 HTML 抓取 |
| Twitter / LinkedIn | 暂不支持（跳过，需人工） |

### 14.4 源质量休眠机制

当一个订阅源连续 **365 天** 没有产出 `content_type=财经分析` 的有效长内容时，系统自动将其标记为「休眠」并在后续运行中跳过。

**判定条件**（三条同时满足）：
1. 该源的 `source_feed_url` 在 `raw_posts` 表中有记录
2. 最早一条记录距今 ≥ 365 天（已监控满一年）
3. 该源在最近 365 天内 0 篇 `content_type='财经分析'` 的内容

**数据溯源**：`RawPost.source_feed_url` 字段记录每篇文章来自哪个 watchlist 订阅源 URL，用于按源分组统计。

**CLI 选项**：
- `--show-dormant`：列出所有休眠源及原因，不执行爬取
- `--include-dormant`：本次运行强制包含休眠源

### 14.5 命令行用法

```bash
python run_monitor.py                      # 跑全部来源
python run_monitor.py --dry-run            # 仅预览新 URL，不执行
python run_monitor.py --source "付鹏"      # 仅处理指定作者
python run_monitor.py --limit 5            # 每个来源最多处理 5 条
python run_monitor.py --since 2026-03-01   # 自定义日期截止
python run_monitor.py --show-dormant       # 查看休眠源列表
python run_monitor.py --include-dormant    # 强制包含休眠源
```

---

## 15. 配置与环境

```bash
# 必需
DATABASE_URL=sqlite+aiosqlite:///./anchor.db

# LLM（统一接口，支持 Anthropic/OpenAI 兼容）
LLM_PROVIDER=anthropic          # 或 openai
LLM_API_KEY=...
LLM_MODEL=...
ANTHROPIC_API_KEY=...           # Anthropic 专用

# 可选 — 联网搜索（事实验证必需，无则降级为纯 LLM）
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

## 16. 技术栈

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

## 17. 文件结构

```
anchor/
├── models.py                        # 数据模型（七实体 + 政策 + 基础设施）
├── llm_client.py                    # LLM 统一接口
├── config.py                        # 配置（Pydantic Settings）
│
├── chains/                          # 三条链路编排（入口）
│   ├── content_extraction.py        # 内容提取 — 提取编排
│   ├── general_assessment.py        # 通用判断 — 2D分类 + 摘要 + 利益冲突
│   ├── fact_verification.py         # 事实验证 — 验证 + 执行追踪
│   └── prompts/
│       ├── post_analysis.py         # 通用判断内容分类 + 发文机关提示词
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
├── extract/                         # 提取引擎（内容提取使用）
│   ├── extractor.py                 # Extractor 主类（v5 + 政策模式）
│   ├── schemas.py                   # Pydantic schema（七实体 + 政策 + 比对结果）
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

# 附录

## 18. 系统局限与边界

### 当前不支持的能力

| 局限 | 说明 |
|------|------|
| 非文本内容 | 图表中的信息暂不提取（视频/音频已通过 Whisper 转录支持） |
| Solution / Theory 验证 | 解决方案与理论框架实体目前不纳入事实验证 |
| 多语言混合 | 中英混合内容可处理，其他语言效果未充分测试 |
| Twitter 监控 | 监控流水线暂不支持 Twitter/X 自动抓取，需人工提交 URL |

### 设计边界

- **政策模式** 适用于政策宣布/解读类内容，市场分析类走标准七实体路径，两者互斥
- **比对功能** 依赖 Tavily 搜索能找到先前年份的类似文件，若检索不到则需手动提供
- **执行追踪** 基于 Tavily 实时搜索，结果受检索质量影响，仅供参考
- **职责边界**：Anchor 只做信息理解和结构化提取。产业建模（周期定位、传导链、IPOCC 政策分析、公司深度分析）属于 Axion 项目；定价判断和投资决策属于 Polaris 项目
- **提取 ≠ 分析**：Anchor 从政策文件中提取 PolicyTheme/PolicyItem（文本结构化），但不做政策力学分析（IPOCC 六步工作流属于 Axion）
- **提取 ≠ 建模**：Anchor 从财报中提取 EarningsAnalysis（财务数据结构化），但不做商业模式/护城河/管理层深度评估（属于 Axion）

---

## 19. 路线图

### 已完成（v5.1）

- [x] 七实体提取体系（Fact / Assumption / ImplicitCondition / Conclusion / Prediction / Solution / Theory）
- [x] v5 五步流水线（Step1→5：提取→合并→分类→隐含→摘要）
- [x] 政策专用模式（PolicyTheme + PolicyItem）
- [x] 五维政策分析框架（紧迫性/量化目标/组织保障/背景/发文机关）
- [x] 双文档比对（自动获取上年报告，标注增/调/延/删）
- [x] 政策执行追踪（事实验证实时检索执行状态）
- [x] 作者档案与信誉评级
- [x] 论证 DAG（核心结论识别、循环检测）
- [x] 支持 Twitter/X、微博、YouTube、Bilibili、Truth Social、通用 Web
- [x] 订阅监控流水线（watchlist.yaml → RSS/YouTube/Bilibili/Weibo → Notion）
- [x] 内容质量过滤（付费墙/视频时长/文章字数）
- [x] 内容分类重构（6种类型 + 5种财经分析子分类）
- [x] 实际发言人识别（real_author_name）
- [x] ~~立场分析 4 维度~~ → v7.1 替换为利益冲突检测（has_conflict + conflict_note）
- [x] Web UI（FastAPI，port 8765，支持政策对比 + 标准模式渲染）
- [x] **技术论文模式** — PaperAnalysis 表 + 2-call 提取管线 + arXiv→ar5iv 重定向
- [x] **公司财报模式** — EarningsAnalysis 表 + 4-call 提取管线（巴菲特视角 + 简化 DCF）

### 近期规划（v6.x）

- [ ] **产业链研究模式** — DB 表 + 提取管线 + Player 归一化 + Metric 聚合
- [ ] **AI 产业链 LayerSchema 初始化** — 8 层预定义指标种子数据
- [ ] Twitter/X 自动监控（订阅流水线集成）
- [ ] DAG 可视化（导出为 PNG/SVG，支持交互式浏览）
- [ ] 批量处理 API（批量 URL 提交、进度查询）
- [ ] 政策时间轴（多年报告连续对比）

### v7.1 通用判断重构

- [x] **2D 分类**：content_domain(5) × content_nature(2) 替代扁平 content_type 枚举
- [x] **利益冲突检测**：has_conflict + conflict_note 替代 4 维度立场分析（减少 1 次 LLM 调用）
- [x] **摘要替代意图**：assessment_summary（什么人在干什么事）替代 author_intent 动宾短语
- [x] **统一路由函数**：resolve_content_mode() 所有调用点共享
- [x] **验证门控**：一手信息不验证（content_nature="一手信息" → 跳过标准实体验证）

### v7.2 全球多国覆盖（中美欧日）

- [x] **通用判断多国适配**：一手信息定义覆盖 SEC/CSRC/FCA/FSA 等各国监管披露；示例覆盖中美欧日
- [x] **财报模式多国年报**：支持美国 10-K、中国 CSRC 年报、欧盟 IFRS 年报、日本有価証券報告書
- [x] **多币种支持**：财务数据保留原文货币（$/¥/€/£），不做换算
- [x] **提取提示词去美国中心化**：抽象简化/合并归一化/产业指标示例覆盖中美欧日机构与市场
- [x] **发文机关多国覆盖**：识别中国（国务院等）/美国（Fed/Treasury/SEC）/欧盟（ECB/EC）/日本（日銀/財務省）

### v7.0 项目分离与接口定义

- [ ] 明确 Anchor 输出接口（DB schema 作为 Axion 的输入契约）
- [ ] 公司实体归一化（跨文章识别同一公司，为 Axion 提供一致的公司 ID）
- [ ] 提取质量提升：政策条目结构化增强、财报提取字段扩展
- [ ] 新闻/快讯事件记录（为 Axion 的地缘宏观力量层提供输入）

> 产业建模（IPOCC 政策分析、公司深度分析、传导链、周期定位）→ 见 Axion PRD
> 量化投资（定价、仓位、执行）→ 见 Polaris PRD

### 中长期规划

- [ ] 自动监控模式（定期抓取特定作者/关键词的新内容）
- [ ] 跨文档引用追踪（政策文件引用关系图谱）
- [ ] 预测验证自动化（到期预测自动触发事后验证）
- [ ] 多语言支持（英语内容提取与验证）
- [ ] Solution 实体验证（评估解决方案可行性）
- [ ] 产业链跨行业扩展（新能源车、半导体等 LayerSchema）

---

> 文档状态：v7.0（2026-03-11），定位为理解模型（信息提取引擎），产业建模移至 Axion，量化投资移至 Polaris
