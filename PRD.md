# Anchor — 产品需求文档（PRD）

> 版本：v2.1
> 更新日期：2026-03-04
> 状态：进行中

---

## 一、产品概述

### 1.1 产品定位

Anchor 是一个**观点分析与事实验证系统**，针对社交媒体（X/Twitter、微博、Youtube、新闻媒体网站等）上的文字、图片、视频内容，自动完成以下工作：

1. **信息采集**：抓取指定帖子的原始内容（文字 + 媒体），也可以订阅作者内容，后续作者有更新则自动采集对应内容
2. **分类提取**：用 LLM 将原始内容解构为结构化实体（依据、条件、结论、解决方案）
3. **逻辑推理**：梳理实体之间的逻辑关系，构建有向无环图（DAG），识别核心结论
4. **事实验证**：对每个实体进行验证
5. **辅助评估**：对作者的专业性、立场进行评估（这部分跟核心流程无关）
6. **信誉累计**：对作者过去所有文章的价值进行汇总（这部分跟核心流程无关）

### 1.2 核心价值主张

| 问题 | Anchor 的解法 |
|---|---|
| 社交媒体观点真假难辨 | 对每个事实进行现实对齐验证 |
| 论点结构不透明 | 构建逻辑 DAG，可视化推理链 |
| 无法区分核心观点与辅助论据 | 自动识别核心结论 |
| 无法评估作者可信度 | 多维度作者评估（专业性 × 历史准确率 × 立场） |
| 不知道预测是否兑现 | 对预测型内容建立监控窗口，到期验证 |

### 1.3 目标用户

- 需要快速判断信息可信度的研究员、记者、分析师
- 需要对公开观点进行溯源验证的机构

---

## 二、系统架构

### 2.1 五层管道

```
Layer 1 — 信息采集
  ↓
Layer 2 — 分类提取（六实体）
  ↓
Layer 3 - 逻辑提炼
  描述实体之间的关系，已有向无环图表示
Layer 4 — 信息验证
    Step 0: 核心观点探寻
    Step 1: 事实准确率判断
    Step 2: 逻辑自洽性判断
    Step 3: 结论时效准确性判断
Layer 5 - 评估
    内容质量评估
    作者综合统计更新
其他 - 辅助信息
    作者的专业背景和立场调研
```

### 2.2 六实体体系

| 实体 | 含义 | 举例 |
|---|---|---|
| **Fact（事实）** | 作者援引的可核实陈述（过去/当前） | "美联储2024年降息50BP" |
| **Conclusion（结论）** | 作者基于事实推导的结论(包括回顾型和预测型) | "美联储政策转向已确认" |
| **Condition（条件）** | 包括假设条件「显式的"如果X则Y"前提条件」，和隐含条件「未说出的暗含前提」 | "如果美元保持强势"/"美联储不会再升息" |
| **Solution（解决方案）** | 作者建议的行动方案 | "建议减持美债" |

---

## 三、Layer 1 — 信息采集

### 3.1 输入格式

- X (Twitter) 帖子 URL
- 微博帖子 URL
- 通用网页 URL（降级模式）
- Youtube URL

### 3.2 采集内容

- 原始文字内容
- 媒体内容（图片描述、视频转录）
- 元数据（发布时间、点赞数、转发数等）
- 作者基本信息（名称、平台 ID）

### 3.3 去重机制

- **来源级去重**：同一 URL 不重复入库（MonitoredSource 层）
- **内容级去重**：跨平台判断是否为相同内容（ContentDuplicateChecker，LLM 辅助）
- **作者跨平台合并**：同一作者在不同平台的身份识别（AuthorGroupMatcher，置信度 ≥ 0.80）

---

## 四、Layer 2 — 分类提取

### 4.1 提取流程（v2 八步 Prompt）

| 步骤 | 任务 |
|---|---|
| A | 提取事实（Fact） |
| B | 提取结论（Conclusion，回顾型和预测型） |
| C | 提取解决方案（Solution） |
| D | 提取条件（Condition，包括假设条件和隐含条件） |

### 4.2 通用实体字段

每个实体在提取时都必须生成：

- `claim` / `condition_text`：原文表达（≤120字）
- `verifiable_statement`：**单句可验证陈述**，供 Layer 4 校验使用
- `temporal_type`：事实依据/回顾型结论/预测型结论/解决方案/假设条件/隐含条件
- `temporal_note`：时间范围标注（如"2024年第四季度"）

## 五、Layer3 - 逻辑提炼
### 5.1 逻辑图（Logic DAG）

这个阶段阶段同时构建有向图，支持以下边类型：

| 逻辑类型 | 方向 | 含义 |
|---|---|---|
| inference | Fact/Condition/Conclusion → Conclusion | 推理关系 |
| derivation | Conclusion → Solution | 行动建议推导 |

**关键规则**：
- 结论可以指向另一个结论（`supporting_conclusion_ids`），形成多级推理链。
- 若某个结论没有指向其他实体的边，则标记此结论为核心观点
- 如果生成的结论成环，则直接对应环上的所有实体标记为无效，后续不再使用

### 5.2 程序化生成 chain_summary

保存所有实体后，程序自动生成每条逻辑的自然语言摘要：

```
inference:    "由[前提A、前提B]推断得到结论：[目标结论]"
recommendation: "基于[前提A、前提B]，建议：[解决方案]"
```

此摘要作为 Layer 4 逻辑自洽性验证（LogicVerifier）的输入。

### 5.3 逻辑严谨性验证
对于每个chain_summary, 通过LLM判断逻辑的严谨性，仅判断逻辑，不做事实准确性的判定
- **核心问题**：这条推理在逻辑上是否自洽而严谨？（不是验证是否与事实吻合，而是验证推理结构本身）
- 输入：`Logic.chain_summary`（自然语言逻辑摘要）
- 输出：
  - `logic_validity`：valid / partial / invalid
  - `logic_issues`：JSON 问题列表（如["中间跳步，缺乏因果链接"]）

---

## 六、Layer 4 — 信息验证

### 6.1 Step 0 — 作者档案分析（AuthorProfiler）

- 输入：作者名称、平台、简介
- 方法：Tavily 搜索 + LLM 分析
- 输出：
  - `role`：职业身份（如"桥水基金创始人"）
  - `expertise_areas`：专业领域
  - `known_biases`：已知立场偏向
  - `credibility_tier`：1（顶级权威）～5（未知）


### 6.1 Step 0 — 现实对齐（RealityAligner）

对以下实体逐一验证：

| 实体 | 验证逻辑 |
|---|---|
| Fact | Tavily 搜索 + LLM 判断，写入 alignment_result |
| Conclusion | 验证其 verifiable_statement 在规定时间内的准确性，如果规定时间未发生或规定时间的数据未产出，则在monitoring_end 到期后触发|
| Condition | 验证假设条件的发生概率 或 验证隐含条件是否为普遍共识 |

**alignment_result 取值**：

| 值 | 含义 |
|---|---|
| `true` | 与现实对齐，有充分证据 |
| `false` | 与现实不符 |
| `uncertain` | 证据不足或存在争议 |
| `unavailable` | 超出知识截止日期 |
| `approximate_ok` | 宽泛描述，但不影响核心结论 |
| `approximate_critical` | 宽泛描述，且影响核心结论 |


对宽泛描述（"大约"、"约"、"估计"等）进行二级判断——是否影响核心结论？若不影响，标记为 `approximate_ok`，不降低核心结论的可信度。
这里的核心结论同5.1中的定义

### 6.2 Step 1 — 预测监控配置（PredictionMonitor）

- 验证预测是否包含时间范围（无时间范围的结论视为无效）
- 对条件型预测（"如果X则Y"）评估假设条件发生概率：
  - negligible → 放弃监控
  - low/medium/high → 配置监控窗口
- 输出：monitoring_start / monitoring_end / monitoring_source_org

### 6.3 Step 2 — 解决方案模拟（SolutionSimulator）

- LLM 模拟执行解决方案
- Tavily 搜索基准价格（action_target 的当前市值/价格）
- 输出：simulated_action_note / baseline_value / monitoring_end

### 6.4 Step 3 — 裁定推导（VerdictDeriver）

基于 Step 2 的 alignment_result 推导最终裁定：

**结论裁定逻辑**：

| 条件 | 裁定 |
|---|---|
| 所有支撑实体 alignment=true 或 (is_core_conclusion = true 且 approximate_ok) | confirmed |
| 指定时间未到 | pending |
| 任意 alignment=false | refuted |
| 混合 true/uncertain | partial |
| 全部 unavailable | unverifiable |

### 6.5 验证优先级

验证资源应优先分配给核心结论及其直接支撑事实；宽泛描述的影响也应以"是否影响核心结论"为判断基准。

建议部分不验证

**预测裁定**：到期后读取 prediction.alignment_result → PredictionVerdict

**解决方案裁定**：聚合源结论/预测的裁定 → SolutionAssessment

## 七、layer 5评估

### 7.1 Step 0 — 作者专业背景评估

评估作者的专业背景与其观点是否匹配：
- `appropriate`：专业背景与观点领域吻合
- `questionable`：略超出核心专业范围
- `mismatched`：明显超出专业范围

### 7.1 Step 0 — 内容质量评估（PostQualityEvaluator）

| 维度 | 权重 | 数据来源 |
|---|---|---|
| 独特性（uniqueness_score 0-1） | 与数据库中已有内容的差异程度；is_first_mover 表示是否首发 ||
| 信息密度（effectiveness_score 0-1） | 信息密度 vs 噪声比（情绪煽动、重复废话等） ||
| 事实准确率 | 20% | FactEvaluation(true) / 已裁定 |
| 结论准确性 | 15% | ConclusionVerdict(confirmed) / 已裁定 |
| 逻辑严谨性 | 15% | Logic.logic_completeness 均值 |

### 7.2 step1 - 作者质量评估
由内容质量评估累计得到作者质量评估, 维度同内容质量评估, 区别在于这是该作者库中所有内容累计评分
| 维度 | 权重 | 数据来源 |
|---|---|---|
| 独特性（uniqueness_score 0-1） | 与数据库中已有内容的差异程度；is_first_mover 表示是否首发 ||
| 信息密度（effectiveness_score 0-1） | 信息密度 vs 噪声比（情绪煽动、重复废话等） ||
| 事实准确率 | 20% | FactEvaluation(true) / 已裁定 |
| 结论准确性 | 15% | ConclusionVerdict(confirmed) / 已裁定 |
| 逻辑严谨性 | 15% | Logic.logic_completeness 均值 |


---

## 七、UI 展示规格（待实现）

分析完成后，前端展示以下四个区域：

### 7.1 实体概括

- 按类型分组展示所有实体（Fact / Conclusion / Condition / Solution）
- 每个实体显示：claim（截断）+ 关键 badge（验证结果、类型标签）
- 核心结论显著标注（如加星或不同颜色）

### 7.2 逻辑推理图（DAG）

- 可交互有向图，节点颜色/形状按类型区分
- 支持点击节点查看详情

| 节点类型 | 默认颜色方案 |
|---|---|
| Fact | 按 alignment_result 着色（绿/红/黄/灰） |
| Conclusion | 按 verdict 着色；核心结论加粗边框 |
| Prediction | 蓝色，pending 显示虚线 |
| Condition | 橙色 |
| Solution | 紫色，按 verdict 着色 |

边的类型：
- supports（实线箭头）
- assumes（虚线箭头）
- predicts（点划线）
- recommends（双线箭头）

### 7.3 验证结果

以核心结论为单位，展示每个核心结论的完整验证链：
1. 核心结论 claim + 最终裁定 badge
2. 支撑链：每个支撑事实 → alignment_result + 证据摘要
3. 逻辑自洽性：logic_validity + 问题列表
4. 宽泛描述提示（如有）

### 7.4 作者与内容评估

分三个子面板：
1. **专业性**：role、credibility_tier、expertise_areas、known_biases
2. **立场档案**：dominant_stance + 分布条形图（来自 AuthorStanceProfile）
3. **内容质量**：uniqueness_score + effectiveness_score + 综合评分雷达图 / 条形图

---

## 八、数据模型摘要

### 8.1 核心表

| 表名 | 关键字段 |
|---|---|
| `facts` | claim, verifiable_statement, temporal_type, alignment_result, alignment_vagueness（待加） |
| `conclusions` | claim, is_core_conclusion（待加）, verdict, alignment_result |
| `condition` | condition_text, verifiable_statement, alignment_result |
| `solutions` | claim, action_type, baseline_value, monitoring_end |
| `logics` | logic_type, chain_summary, chain_type, logic_validity, logic_issues |

### 8.2 评估/裁定表

| 表名 | 用途 |
|---|---|
| `conclusion_verdicts` | 结论裁定，含 role_fit |
| `solution_assessments` | 解决方案裁定，含 role_fit |
| `post_quality_assessments` | 单帖内容质量评估 |
| `author_stats` | 作者七维度综合统计 |
| `author_stance_profiles` | 作者立场分布档案 |
| `author_groups` | 跨平台同人作者组 |

---

## 九、技术规格

### 9.1 技术栈

- **后端**：Python / FastAPI / SQLModel / SQLite (aiosqlite)
- **LLM**：Anthropic Claude（可切换 OpenAI 兼容接口）
- **联网搜索**：Tavily API（可选，降级为纯训练知识）
- **媒体处理**：httpx 下载 + PyAV 音频提取 + Whisper 转录

### 9.2 配置

- `settings.llm_mode`：Anthropic vs OpenAI 兼容
- `settings.tavily_api_key`：联网搜索开关
- `DATABASE_URL`：支持 SQLite（默认）

### 9.3 接口

- `POST /analyze` → 返回 task_id
- `GET /stream/{task_id}` → SSE 流式推送 pipeline 进度 + 最终结果
- `POST /reprofile` → 强制刷新作者档案

---

## 十、待实现功能（Backlog）

| 优先级 | 功能 | 说明 |
|---|---|---|
| 高 | 核心结论标注 | 在 Conclusion 加 `is_core_conclusion` 字段，提取后程序化计算 |
| 高 | 宽泛描述处理 | 在 RealityAligner 中增加 vagueness 判断，输出 `approximate_ok` / `approximate_critical` |
| 高 | UI 四区域改版 | 实体概括 + 逻辑 DAG + 验证结果 + 作者评估 |
| 中 | 逻辑 DAG 可视化 | 使用 vis-network 或 Cytoscape.js 实现交互式有向图 |
| 中 | 多核心结论支持 | 一篇内容可能包含多个并列的核心结论，各自有独立的验证链 |
| 低 | 批量分析 | 对同一作者的多篇帖子进行横向统计 |
| 低 | 时间序列追踪 | 作者历史预测准确率的时间变化 |

---

## 十一、变更历史

| 版本 | 日期 | 主要变更 |
|---|---|---|
| v1.0 | 2026-02-28 | 初始版本：4实体（Fact/Conclusion/Solution/Logic）+ 10步管道 |
| v1.1 | 2026-02-28 | 新增：多LLM核查方案、作者自信度、条件型预测、解决方案基准价格 |
| v1.2 | 2026-03-01 | 新增：ImplicitCondition 提取与验证（Layer3 1b步） |
| v1.3 | 2026-03-03 | 新增：跨平台作者合并、内容去重、立场档案 |
| v2.0 | 2026-03-04 | 重构：6实体体系（Prediction/Assumption独立）+ LogicVerifier + RealityAligner（替代ConditionVerifier/LogicEvaluator） |
| v2.1 | 2026-03-04 | 概念调整：tracking→推理+验证；新增核心结论识别、宽泛描述处理、UI四区域规格（待实现） |
