# Anchor — 经济观点准确性追踪系统

> 锚定经济预测，验证观点价值

Anchor 是一个三层架构的经济观点数据库，系统性地收集社交媒体上的经济相关观点，对其进行分类打标，并长期追踪验证其准确性。

---

## 系统架构

```
┌──────────────────────────────────────────────────────┐
│  Layer 1  信息采集层  (Collector)                      │
│  从 Twitter/X、微博、财经 RSS 等来源抓取原始内容          │
└─────────────────────────┬────────────────────────────┘
                          │ 原始帖子
┌─────────────────────────▼────────────────────────────┐
│  Layer 2  观点分类层  (Classifier)                     │
│  用 Claude API 提取可验证的经济观点，识别实体与类别        │
└─────────────────────────┬────────────────────────────┘
                          │ 结构化观点
┌─────────────────────────▼────────────────────────────┐
│  Layer 3  追踪验证层  (Tracker)                        │
│  对接金融数据源，到期自动验证观点准确性并打分               │
└──────────────────────────────────────────────────────┘
```

### Layer 1 — 信息采集

| 采集器 | 数据源 | 说明 |
|--------|--------|------|
| `TwitterCollector` | Twitter/X API v2 | 关键词搜索 + 指定账号时间线 |
| `WeiboCollector` | 微博 API / 爬虫 | 热门财经话题、大 V 博文 |
| `RSSCollector` | 财经 RSS 源 | 财联社、36氪、彭博、路透等 |

### Layer 2 — 观点分类

使用 Claude API 对原始文本做结构化提取：

- **观点类型**：预测型 / 分析型 / 评论型
- **资产类别**：股票 / 债券 / 大宗商品 / 汇率 / 宏观经济
- **方向性**：看多 / 看空 / 中性
- **时间跨度**：短期（<1个月）/ 中期（1–12个月）/ 长期（>1年）
- **可验证性评分**：0–1，越高代表越容易客观核实

### Layer 3 — 追踪验证

- 到期后自动拉取金融数据（Yahoo Finance / AKShare 等）
- 对照观点预测方向判定结果（正确 / 错误 / 部分正确 / 无法判定）
- 统计每位作者的历史准确率，构建可信度档案

---

## 技术栈

| 模块 | 技术 |
|------|------|
| 后端框架 | Python 3.12 + FastAPI |
| 数据库 | PostgreSQL + SQLModel |
| 任务调度 | APScheduler |
| AI 分类 | Anthropic Claude API |
| 金融数据 | AKShare / yfinance |
| 采集工具 | Tweepy / feedparser / httpx |

---

## 目录结构

```
Anchor/
├── anchor/
│   ├── collector/          # Layer 1: 数据采集
│   │   ├── base.py         # 抽象基类
│   │   ├── twitter.py      # Twitter/X 采集器
│   │   ├── weibo.py        # 微博采集器
│   │   ├── rss.py          # RSS 采集器
│   │   └── manager.py      # 采集调度管理器
│   ├── classifier/         # Layer 2: 观点分类
│   │   ├── extractor.py    # Claude API 观点提取
│   │   └── pipeline.py     # 分类流水线
│   ├── tracker/            # Layer 3: 追踪验证
│   │   ├── verifier.py     # 验证逻辑
│   │   ├── data_sources.py # 金融数据接口
│   │   └── scorer.py       # 准确率评分
│   ├── database/
│   │   ├── models.py       # SQLModel 数据模型
│   │   └── session.py      # 数据库连接
│   ├── api/
│   │   ├── routers/        # FastAPI 路由
│   │   └── main.py         # 应用入口
│   └── config.py           # 配置管理
├── tests/
├── .env.example
├── requirements.txt
└── README.md
```

---

## 快速开始

### 1. 环境准备

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入各平台 API Key
```

### 3. 初始化数据库

```bash
python -m anchor.database.session --create
```

### 4. 启动采集任务

```bash
# 单次采集
python -m anchor.collector.manager --run-once

# 持续调度（默认每小时采集一次）
python -m anchor.collector.manager
```

### 5. 启动 API 服务

```bash
uvicorn anchor.api.main:app --reload
```

访问 `http://localhost:8000/docs` 查看 API 文档。

---

## 数据模型概览

```
RawPost          原始帖子（来自各平台的未处理文本）
  └─► Opinion    提取后的结构化观点
        └─► Verification  到期验证结果
```

---

## 路线图

- [x] Layer 1: 多平台采集器框架
- [ ] Layer 1: Twitter/X、微博、RSS 采集器实现
- [ ] Layer 2: Claude API 观点提取 Pipeline
- [ ] Layer 3: 金融数据对接与自动验证
- [ ] Web Dashboard: 观点浏览与作者准确率排行

---

## License

MIT