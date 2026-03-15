# Anchor — 产品需求文档 (PRD)

> 版本：v9.0
> 更新：2026-03-15
> 面向：Axion 团队消费

---

## 目录

**产品篇**

1. [产品定位与愿景](#1-产品定位与愿景)
2. [用户画像](#2-用户画像)
3. [核心使用场景](#3-核心使用场景)
4. [功能概览](#4-功能概览)

**技术篇**

5. [系统架构](#5-系统架构)
6. [数据模型](#6-数据模型)
    - [6a. Company 域 13 张表完整 Schema](#6a-company-域-13-张表完整-schema)
    - [6b. 表间关系](#6b-表间关系)
7. [域开关机制](#7-域开关机制)
8. [Company 专用提取管线](#8-company-专用提取管线)
9. [三条链路设计](#9-三条链路设计)
10. [内容路由逻辑](#10-内容路由逻辑)
11. [监控流水线](#11-监控流水线)
12. [配置与环境](#12-配置与环境)
13. [技术栈](#13-技术栈)
14. [文件结构](#14-文件结构)

**附录**

15. [系统局限与边界](#15-系统局限与边界)
16. [路线图](#16-路线图)

---

# 产品篇

## 1. 产品定位与愿景

**让每一篇公司财报、政策文件、技术论文、财经分析，都能被系统性地读懂并转化为结构化知识。**

Anchor 负责**数字化**——从非结构化文本中提取**域专用结构化数据**。每篇文章独立提取，不做跨文章归一化（归 Axion）。

**v9 核心变更**：从通用 Node/Edge 架构迁移到**每个域专用数据结构**。当前只有 **company 域**已实现专用管线（13 张表），其他 5 域暂时禁用。

Anchor 是三层投资分析系统的第一层：

```
文章/文件 → [Anchor 数字化] → 域专用结构化表（per-article 提取）
                                    ↓
       市场数据 → [Axion 模型化]  → 知识图谱 + 传导链 + 价格预测
                                    ↓
       当前定价 → [Polaris 量化投资] → 投资决策
```

**历史演进**：
- v1-v5：观点提取 + 事实验证（七实体 DAG）
- v6：Top-down 提取 + 多模式
- v7：Anchor/Axion/Polaris 三层分离
- v8：通用 ExtractionNode/Edge + 统一 2-call 管线
- **v9（本版本）：域专用数据结构 — 移除 Node/Edge，company 域 13 张表专用管线**

---

## 2. 用户画像

### 2.1 产业投资者（首要用户）

**痛点**：需要从 10-K/年报中快速提取公司经营数据、商业模式、管理层叙事。

**Anchor 的解法**：
- Company 域自动提取 13 张表：经营议题、叙事、三表、上下游、地域收入、KPI、债务、诉讼、薪酬、持股、关联交易
- 所有数据关联 `company_id` + `period`，支持跨期对比
- 结构化输出供 Axion 建模消费

### 2.2 政策研究员 / 宏观分析师 / 期货交易员

暂时禁用，待后续域专用管线实现后启用。

---

## 3. 核心使用场景

### 场景 A：公司财报分析（已启用）

**用户操作**：提交 SEC 10-K / CSRC 年报 URL

**Anchor 处理流程**：

```
1. 采集 RawPost
2. 通用判断：domain=公司, nature=一手信息 → content_mode=company
3. Company 专用提取管线：
   - LLM 一次调用提取 13 张表全量数据
   - 自动识别公司（ticker）、报告期（period）
   - 写入 company_profiles + 12 张业务表
4. Notion 同步
```

### 场景 B：非公司内容（暂时禁用）

```
1. 采集 + 通用判断 → 识别域
2. 域开关检查 → 域已禁用，跳过提取
3. 显示"域已禁用，跳过提取"
```

---

## 4. 功能概览

| 功能 | 说明 | 状态 |
|------|------|------|
| 智能采集 | Twitter/X、微博、YouTube、Bilibili、Truth Social、通用 Web | 可用 |
| 订阅监控 | sources.yaml 批量监控 | 可用 |
| 内容质量过滤 | 付费墙、视频时长、文章字数 | 可用 |
| 2D 内容分类 | content_domain × content_nature → content_mode | 可用 |
| **Company 专用提取** | **13 张表结构化提取** | **已启用** |
| 域开关 | 按域启用/禁用提取 | 可用 |
| 事实核查 | 网络检索验证 | 暂停（待域专用验证） |
| 作者档案 | 信誉等级 1-5 | 可用 |

---

# 技术篇

## 5. 系统架构

```
输入 URL / 文档内容
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  采集层（anchor/collect/）                                          │
│  输出：RawPost 存入 DB                                              │
└──────────────────────────────┬───────────────────────────────────┘
                               │
           ┌───────────────────▼──────────────────────┐
           │    通用判断（前置）                           │
           │    → content_domain / content_nature        │
           │    → resolve_content_mode() → 6 种模式       │
           └────────────────────┬─────────────────────┘
                                │
              ┌─────────────────▼─────────────────────┐
              │     域开关检查                            │
              │     settings.is_domain_enabled(mode)    │
              │                                         │
              │     ✅ company → Company 专用管线          │
              │     ❌ 其他 5 域 → 跳过提取               │
              └────────────────────┬──────────────────┘
                                   │ (company only)
              ┌────────────────────▼──────────────────┐
              │     Company 专用提取管线                  │
              │     extract_company_compute()           │
              │     → LLM 一次调用提取 13 张表            │
              │     extract_company_write()             │
              │     → 写入 company_profiles + 12 张表    │
              └───────────────────────────────────────┘
```

---

## 6. 数据模型

### 6.1 基础设施表

| 表名 | 描述 |
|------|------|
| `authors` | 观点作者档案 |
| `raw_posts` | 原始帖子/文档 |
| `monitored_sources` | 监控源 |
| `author_groups` | 跨平台作者实体 |
| `topics` | 话题标签 |

### 6.2 评估与统计表

| 表名 | 描述 |
|------|------|
| `post_quality_assessments` | 单篇内容质量评估 |
| `author_stats` | 作者综合统计 |

---

### 6a. Company 域 13 张表完整 Schema

#### `company_profiles` — 公司档案（最小标识）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| name | VARCHAR INDEX | 公司名 "NVIDIA Corporation" |
| ticker | VARCHAR UNIQUE INDEX | 股票代码 "NVDA" / "600519.SH" |
| market | VARCHAR | us\|cn_a\|cn_h\|hk\|jp |
| industry | VARCHAR | 所属行业 |
| summary | VARCHAR | 一句话商业模式 |
| created_at | DATETIME | |
| updated_at | DATETIME | |

#### `operational_issues` — 经营议题

来源：CEO致股东信 / MD&A。每行 = 一个经营议题，四个维度（表现/归因/风险/指引）。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| company_id | INTEGER FK→company_profiles | |
| period | VARCHAR INDEX | "FY2025" / "2025Q4" |
| raw_post_id | INTEGER FK→raw_posts | 溯源 |
| topic | VARCHAR | 议题名 ≤30字 |
| performance | TEXT | 表现（定性，不含财务数字）≤200字 |
| attribution | TEXT | 归因 ≤200字 |
| risk | TEXT | 风险 ≤200字 |
| guidance | TEXT | 指引 ≤200字 |
| created_at | DATETIME | |

#### `company_narratives` — 叙事（管理层承诺）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| company_id | INTEGER FK | |
| raw_post_id | INTEGER FK | |
| narrative | TEXT | 故事/战略承诺 ≤300字 |
| capital_required | FLOAT | 资金量 |
| capital_unit | VARCHAR | 亿美元\|亿人民币 |
| promised_outcome | TEXT | 承诺结果 ≤200字 |
| deadline | DATE | 承诺时间 |
| status | VARCHAR | announced\|in_progress\|delivered\|missed\|abandoned |
| reported_at | DATE | |
| created_at | DATETIME | |

#### `financial_statements` — 财务报表头

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| company_id | INTEGER FK | |
| period | VARCHAR INDEX | "FY2025" |
| period_type | VARCHAR | quarterly\|annual |
| statement_type | VARCHAR INDEX | income\|balance_sheet\|cashflow\|equity\|tax_detail\|sbc_detail |
| currency | VARCHAR | CNY\|USD\|HKD |
| reported_at | DATE | |
| raw_post_id | INTEGER FK | |
| created_at | DATETIME | |

#### `financial_line_items` — 财务科目（长表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| statement_id | INTEGER FK→financial_statements | |
| item_key | VARCHAR INDEX | 标准化键 "revenue" / "operating_income" |
| item_label | VARCHAR | 原始标签 "营业收入" |
| value | FLOAT | 数值 |
| parent_key | VARCHAR | 父科目键（层级结构） |
| ordinal | INTEGER | 排列顺序 |
| note | VARCHAR | 备注 |

#### `downstream_segments` — 下游（客户/收入流）

每期每客户一行，segment 可选(null=公司级)。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| company_id | INTEGER FK | |
| period | VARCHAR INDEX | |
| segment | VARCHAR | 业务线（null=公司级） |
| customer_name | VARCHAR | 客户名或收入流名 |
| customer_type | VARCHAR | direct\|indirect\|channel\|OEM\|distributor |
| products | VARCHAR | 卖给该客户的产品/服务 |
| channels | VARCHAR | 销售渠道 |
| revenue | FLOAT | 收入（百万美元） |
| revenue_pct | FLOAT | 占总收入百分比 |
| growth_yoy | VARCHAR | 同比增速 |
| backlog | FLOAT | 积压订单（百万美元） |
| backlog_note | VARCHAR | 积压说明 |
| pricing_model | VARCHAR | per-unit\|per-user/month\|usage-based\|混合 |
| contract_duration | VARCHAR | one-time\|1-year\|multi-year |
| revenue_type | VARCHAR | product_sale\|subscription\|license\|royalty\|service\|NRE\|cloud_service |
| is_recurring | BOOLEAN | 是否经常性收入 |
| recognition_method | VARCHAR | point_in_time\|over_time |
| description | TEXT | |
| raw_post_id | INTEGER FK | |
| created_at | DATETIME | |

#### `upstream_segments` — 上游（供应商）

每期每供应商一行，segment 可选(null=公司级)。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| company_id | INTEGER FK | |
| period | VARCHAR INDEX | |
| segment | VARCHAR | 业务线（null=公司级） |
| supplier_name | VARCHAR | 供应商名 |
| supply_type | VARCHAR | foundry\|memory\|assembly_test\|substrate\|component\|contract_mfg\|software\|logistics |
| material_or_service | VARCHAR | 供应内容 |
| process_node | VARCHAR | 制程节点 |
| geographic_location | VARCHAR | 所在地 |
| is_sole_source | BOOLEAN | 独家供应 |
| purchase_obligation | FLOAT | 采购义务（百万美元） |
| lead_time | VARCHAR | 交货周期 |
| contract_type | VARCHAR | 长期合约\|purchase_order\|prepaid\|non-cancellable |
| prepaid_amount | FLOAT | 预付金额 |
| concentration_risk | VARCHAR | 集中度风险 |
| description | TEXT | |
| raw_post_id | INTEGER FK | |
| created_at | DATETIME | |

#### `geographic_revenues` — 地域收入

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| company_id | INTEGER FK | |
| period | VARCHAR INDEX | |
| region | VARCHAR | 地域名 |
| revenue | FLOAT | 收入（百万） |
| revenue_share | FLOAT | 占比（0-1） |
| growth_yoy | VARCHAR | 同比增速 |
| note | VARCHAR | |
| raw_post_id | INTEGER FK | |
| created_at | DATETIME | |

#### `non_financial_kpis` — 非财务KPI

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| company_id | INTEGER FK | |
| period | VARCHAR INDEX | |
| kpi_name | VARCHAR | 指标名 |
| kpi_value | VARCHAR | 值（文本，兼容非数值） |
| kpi_unit | VARCHAR | 单位 |
| yoy_change | VARCHAR | 同比变化 |
| category | VARCHAR | workforce\|customer\|product\|esg\|operational |
| note | VARCHAR | |
| raw_post_id | INTEGER FK | |
| created_at | DATETIME | |

#### `debt_obligations` — 债务/义务明细

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| company_id | INTEGER FK | |
| period | VARCHAR INDEX | |
| instrument_name | VARCHAR | 债务工具名 |
| debt_type | VARCHAR | bond\|loan\|lease\|convertible\|credit_facility |
| principal | FLOAT | 本金（百万） |
| currency | VARCHAR | |
| interest_rate | FLOAT | 年利率 |
| maturity_date | DATE | 到期日 |
| is_secured | BOOLEAN | 有担保 |
| is_current | BOOLEAN | 一年内到期 |
| note | VARCHAR | |
| raw_post_id | INTEGER FK | |
| created_at | DATETIME | |

#### `litigations` — 诉讼/或有事项

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| company_id | INTEGER FK | |
| case_name | VARCHAR | 案件名 |
| case_type | VARCHAR | lawsuit\|regulatory\|patent\|antitrust\|environmental\|tax\|other |
| status | VARCHAR | pending\|settled\|dismissed\|ongoing\|appealed |
| counterparty | VARCHAR | 对方 |
| filed_at | DATE | |
| claimed_amount | FLOAT | 索赔金额（百万） |
| accrued_amount | FLOAT | 已计提（百万） |
| currency | VARCHAR | |
| description | TEXT | |
| resolution | TEXT | |
| resolved_at | DATE | |
| raw_post_id | INTEGER FK | |
| created_at | DATETIME | |

#### `executive_compensations` — 管理层/董事薪酬

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| company_id | INTEGER FK | |
| period | VARCHAR INDEX | |
| role_type | VARCHAR | executive\|director |
| name | VARCHAR | |
| title | VARCHAR | CEO\|CFO\|Independent Director |
| base_salary | FLOAT | 基本工资（董事: fees_earned_cash） |
| bonus | FLOAT | |
| stock_awards | FLOAT | |
| option_awards | FLOAT | |
| non_equity_incentive | FLOAT | |
| other_comp | FLOAT | |
| total_comp | FLOAT | |
| currency | VARCHAR | |
| pay_ratio | FLOAT | CEO Pay Ratio（仅 CEO） |
| median_employee_comp | FLOAT | 员工中位数（仅 CEO） |
| raw_post_id | INTEGER FK | |
| created_at | DATETIME | |

#### `stock_ownership` — 持股信息

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| company_id | INTEGER FK | |
| period | VARCHAR INDEX | |
| name | VARCHAR | 持有人 |
| title | VARCHAR | 职位 |
| shares_beneficially_owned | INTEGER | 受益持股数 |
| percent_of_class | FLOAT | 持股比例（0-100） |
| raw_post_id | INTEGER FK | |
| created_at | DATETIME | |

#### `related_party_transactions` — 关联交易

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| company_id | INTEGER FK | |
| period | VARCHAR INDEX | |
| related_party | VARCHAR | 关联方 |
| relationship | VARCHAR | director\|officer\|major_shareholder\|subsidiary\|affiliate\|family |
| transaction_type | VARCHAR | sale\|purchase\|lease\|loan\|guarantee\|service\|license\|other |
| amount | FLOAT | 金额（百万） |
| currency | VARCHAR | |
| terms | TEXT | 交易条件 |
| is_ongoing | BOOLEAN | 持续性交易 |
| description | TEXT | |
| raw_post_id | INTEGER FK | |
| created_at | DATETIME | |

---

### 6b. 表间关系

```
company_profiles (1)
    ├──→ operational_issues (N)     — company_id + period
    ├──→ company_narratives (N)     — company_id
    ├──→ financial_statements (N)   — company_id + period
    │       └──→ financial_line_items (N) — statement_id
    ├──→ downstream_segments (N)    — company_id + period
    ├──→ upstream_segments (N)      — company_id + period
    ├──→ geographic_revenues (N)    — company_id + period
    ├──→ non_financial_kpis (N)     — company_id + period
    ├──→ debt_obligations (N)       — company_id + period
    ├──→ litigations (N)            — company_id
    ├──→ executive_compensations (N)— company_id + period
    ├──→ stock_ownership (N)        — company_id + period
    └──→ related_party_transactions (N) — company_id + period

raw_posts (1)
    └──→ 所有业务表 (N)              — raw_post_id（溯源）
```

**核心关联键**：
- `company_id` — 唯一标识公司（通过 ticker 去重）
- `period` — 报告期（"FY2025" / "2025Q4"）
- `raw_post_id` — 溯源到原始文档

**跨期查询模式**（供 Axion 参考）：
```sql
-- 获取某公司所有期的经营议题
SELECT * FROM operational_issues
WHERE company_id = ? ORDER BY period;

-- 获取某期的全部下游客户
SELECT * FROM downstream_segments
WHERE company_id = ? AND period = 'FY2025';

-- 跨公司比较管理层薪酬
SELECT cp.name, ec.* FROM executive_compensations ec
JOIN company_profiles cp ON ec.company_id = cp.id
WHERE ec.period = 'FY2025' AND ec.role_type = 'executive';
```

---

## 7. 域开关机制

### 配置

```python
# anchor/config.py
enabled_domains: dict[str, bool] = {
    "company": True,      # ✅ 已实现专用管线
    "policy": False,      # ❌ 待实现
    "industry": False,    # ❌ 待实现
    "technology": False,  # ❌ 待实现
    "futures": False,     # ❌ 待实现
    "expert": False,      # ❌ 待实现
}
```

### 行为

| 场景 | 行为 |
|------|------|
| 域已启用 | 路由到专用提取管线 |
| 域已禁用 | 跳过提取，返回 `skip_reason="域已禁用"` |
| 未知域 | 默认禁用 |

### 检查点

域开关在三处生效：
1. **Extractor.extract()** — 单条提取入口
2. **ConcurrentBatchRunner._process_one()** — 批量提取
3. **run_url.py** — CLI 展示

---

## 8. Company 专用提取管线

### 架构

```
extract_company_compute(content, platform, author, today)
  │
  ├── LLM 一次调用：
  │   输入: 公司财报/年报全文
  │   输出: CompanyExtractionResult（13 张表数据 + 公司信息 + 摘要）
  │
  └── 返回: CompanyComputeResult（无 DB 操作）

extract_company_write(raw_post, session, compute_result)
  │
  ├── get_or_create_company(ticker) → CompanyProfile
  ├── 写入 12 张业务表
  ├── 更新 RawPost.content_summary
  │
  └── 返回: {is_relevant_content, table_counts, summary, company_name, company_ticker}
```

### Pydantic Schema

```python
# anchor/extract/schemas/company.py
class CompanyExtractionResult(BaseModel):
    is_relevant_content: bool
    company: CompanyProfile       # {name, ticker, market, industry, summary}
    period: str                   # "FY2025"
    operational_issues: list[...]
    narratives: list[...]
    downstream_segments: list[...]
    upstream_segments: list[...]
    geographic_revenues: list[...]
    non_financial_kpis: list[...]
    debt_obligations: list[...]
    litigations: list[...]
    executive_compensations: list[...]
    stock_ownership: list[...]
    related_party_transactions: list[...]
    summary: str | None
    one_liner: str | None
```

### 与旧架构的区别

| 维度 | v8 Node/Edge | v9 Company 专用 |
|------|-------------|----------------|
| 数据模型 | 2 张通用表 | 13 张专用表 |
| LLM 调用 | 2-call（节点+边） | 1-call（全量提取） |
| 输出 | ExtractionNode + ExtractionEdge | 13 张表各自的行 |
| 关联键 | raw_post_id | company_id + period + raw_post_id |
| 跨期查询 | 不支持 | 原生支持（period 字段） |

---

## 9. 三条链路设计

### 内容提取

```
URL
 → process_url()           — 采集 RawPost
 → assess_post()           — 通用判断（确定 content_mode）
 → 域开关检查
 → Extractor.extract()     — 路由到专用管线
 → 返回 {table_counts, summary}（company 域）
```

### 通用判断（`anchor/chains/general_assessment.py`）

不变。2D 分类（content_domain × content_nature）→ content_mode。

### 事实验证

暂停。company 域跳过验证（使用专用表，无 ExtractionNode）。待后续域专用验证管线实现。

---

## 10. 内容路由逻辑

```python
def resolve_content_mode(domain, nature, content_type=None) -> str:
    if nature == "第三方分析": return "expert"
    if domain == "政策": return "policy"
    if domain == "产业": return "industry"
    if domain == "技术": return "technology"
    if domain == "期货": return "futures"
    if domain == "公司": return "company"
    return "expert"
```

| domain | nature | content_mode | 状态 |
|--------|--------|-------------|------|
| 公司 | 一手信息 | company | **已启用** |
| 政策 | 一手信息 | policy | 禁用 |
| 产业 | 一手信息 | industry | 禁用 |
| 技术 | 一手信息 | technology | 禁用 |
| 期货 | 一手信息 | futures | 禁用 |
| 任意 | 第三方分析 | expert | 禁用 |

---

## 11. 监控流水线

不变。`anchor monitor` 依然可用，但非 company 域的内容会在提取阶段跳过。

---

## 12. 配置与环境

```bash
# 必需
DATABASE_URL=sqlite+aiosqlite:///./anchor.db
LLM_PROVIDER=anthropic
LLM_API_KEY=...

# 可选
SERPER_API_KEY=...
TWITTER_BEARER_TOKEN=...
```

---

## 13. 技术栈

| 层次 | 技术 |
|------|------|
| 语言 | Python 3.11+ |
| LLM | Anthropic Claude / OpenAI 兼容接口 |
| ORM | SQLModel（SQLAlchemy 2.0 异步） |
| 数据库 | SQLite（开发）/ PostgreSQL（生产） |
| 采集 | Twitter API / Weibo / BeautifulSoup / Jina Reader |
| 异步 | asyncio + aiosqlite |

---

## 14. 文件结构

```
anchor/
├── config.py                       # 配置 + enabled_domains 域开关
├── models.py                       # 13 张 company 表 + 政策表 + 技术表 + 基础设施表
├── llm_client.py                   # LLM 统一接口
├── cli.py                          # Click CLI
│
├── extract/
│   ├── router.py                   # Extractor 门面 — 域开关 + 路由
│   ├── schemas/
│   │   ├── company.py              # Company LLM 输出 Pydantic schema
│   │   └── nodes.py                # 旧 Node/Edge schema（保留参考）
│   ├── pipelines/
│   │   ├── _base.py                # call_llm + parse_json + safe_float/safe_str
│   │   ├── company.py              # Company 专用管线（compute + write）
│   │   └── generic.py              # 旧通用管线（已废弃）
│   └── prompts/domains/            # 领域提示词（保留）
│
├── chains/
│   ├── content_extraction.py       # 内容提取链路
│   ├── general_assessment.py       # 通用判断
│   └── fact_verification.py        # 事实验证（company 域跳过）
│
├── pipeline/
│   └── concurrent.py               # 并发批量提取（支持 company 路由）
│
├── commands/
│   └── run_url.py                  # anchor run-url（支持 company 结果展示）
│
├── collect/                        # 数据采集
├── monitor/                        # 订阅监控
├── verify/                         # 验证工具
└── database/session.py             # 异步 DB Session
```

---

# 附录

## 15. 系统局限与边界

- **仅 company 域已实现**：其他 5 域（policy/industry/technology/futures/expert）暂时禁用
- **事实验证暂停**：待域专用验证管线实现
- **ExtractionNode/Edge 已移除**：旧 DB 表数据保留只读，新代码不再写入
- **每篇文章单域**：一篇内容只进入一个域的提取管线

---

## 16. 路线图

### 已完成（v9.0）

- [x] **Company 专用管线**：13 张表一次 LLM 调用提取
- [x] **域开关机制**：enabled_domains 按域启用/禁用
- [x] **移除 ExtractionNode/ExtractionEdge**：从通用架构迁移到域专用
- [x] **safe_float/safe_str 工具迁移**：从 scripts/ 到 pipelines/_base.py
- [x] **并发管线适配**：ConcurrentBatchRunner 支持 company 路由
- [x] **PRD 重写**：面向 Axion，重点 company 13 张表 schema

### 近期规划

- [ ] **Policy 域专用管线**：PolicyDocument + PolicyDirective + PolicyLink
- [ ] **Technology 域专用管线**：TechInsight + PatentRight + PatentCommercial
- [ ] **Expert 域专用管线**：待设计专用数据结构
- [ ] **域专用事实验证**：每个域独立的验证逻辑

### 中长期规划

- [ ] Industry / Futures 域专用管线
- [ ] 跨期对比报告生成
- [ ] 多公司横向比较

---

> 文档状态：v9.0（2026-03-15），Company 域专用管线已上线，其他 5 域待实现
