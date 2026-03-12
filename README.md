# Anchor

多模式信息提取与事实验证引擎。从非结构化文本中提取结构化节点（Node）和关系（Edge），并通过联网搜索验证事实可信度。

```
文章/文件 → [Anchor 理解模型] → Node + Edge（结构化知识图谱）
                                       ↓
            市场数据 → [Axion 产业模型] → 价格预测信号
                                       ↓
            当前定价 → [Polaris 量化投资] → 投资决策
```

## 6 个领域 × 节点类型

| 领域 | content_mode | 节点类型 |
|------|-------------|---------|
| 政策 | policy | 主旨·目标·战略·战术·资源·考核·约束·反馈·外溢 (9) |
| 产业 | industry | 格局·驱动·趋势·技术路线·资金流向·机会威胁·标的 (7) |
| 技术 | technology | 问题·方案·效果性能·局限场景·玩家 (5) |
| 期货 | futures | 供给·需求·库存·头寸·冲击·缺口 (6) |
| 公司 | company | 表现·归因·指引·风险·叙事 (5) |
| 专家分析 | expert | 事实·判断·预测·建议 (4) |

## 安装

```bash
# 克隆
git clone https://github.com/kumaloha/Anchor.git
cd Anchor

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -e .

# 如需 Axion 产业模型支持
pip install -e ../Axion
```

## 配置

复制 `.env.example` 为 `.env`，填写必要配置：

```bash
cp .env.example .env
```

必填项：

```env
# 数据库
DATABASE_URL=sqlite+aiosqlite:///./data/anchor.db

# LLM（二选一）
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

# 或 Qwen（推荐，支持 Batch API 50% 折扣）
LLM_PROVIDER=openai
LLM_API_KEY=sk-...
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
ENABLE_BATCH=true
```

可选项：

```env
# 联网事实核查（Serper.dev Google Search API）
SERPER_API_KEY=...

# 社交媒体采集
TWITTER_API_KEY=...
WEIBO_COOKIE=...

# 语音转录（YouTube/Bilibili）
ASR_API_KEY=...
ASR_BASE_URL=https://api.groq.com/openai/v1
ASR_MODEL=whisper-large-v3-turbo
```

## 使用

### 分析单条 URL

```bash
anchor run-url <url>

# 示例
anchor run-url "https://x.com/RayDalio/status/2015822544083759340"
anchor run-url "https://arxiv.org/abs/2501.12948"
anchor run-url "https://www.youtube.com/watch?v=EbjIyoIhtc4"
anchor run-url "https://weibo.com/1182426800/QoLwdfDvQ"

# 强制重新处理
anchor run-url --force <url>
```

### 批量监控

从 `sources.yaml` 批量拉取订阅源的新文章并分析：

```bash
anchor monitor                          # 全部来源
anchor monitor --dry-run                # 预览新 URL，不执行分析
anchor monitor --source "Ray Dalio"     # 只跑指定作者
anchor monitor --limit 5                # 每源最多 5 条
anchor monitor --since 2026-03-01       # 只抓此日期之后的文章
anchor monitor --concurrency 10         # 10 个并行 worker
```

### 开发模式

```bash
# 未安装时可用 python -m
python -m anchor run-url <url>
python -m anchor monitor --dry-run
```

## 处理流程

```
URL
 → 采集（Twitter/微博/YouTube/Bilibili/通用 Web）
 → 通用判断（2D 分类：领域×性质 → content_mode）
 → 内容提取（2-call LLM pipeline → Node + Edge）
 → 事实验证（Serper 搜索 + LLM，原文语言+英文交叉验证）
```

**Batch 模式**：`ENABLE_BATCH=true` 时，LLM 调用走 OpenAI Batch API（DashScope 兼容），异步提交批量请求，成本降低 50%。

## 项目结构

```
anchor/
├── chains/
│   ├── content_extraction.py    # 内容提取编排
│   ├── general_assessment.py    # 通用判断（2D分类+作者档案）
│   └── fact_verification.py     # 事实验证（批量搜索+LLM）
├── collect/
│   ├── input_handler.py         # URL 解析 + 采集入口
│   └── web.py                   # Jina Reader + arXiv 重定向
├── extract/
│   ├── pipelines/
│   │   ├── generic.py           # 统一 2-call 提取管线
│   │   └── _base.py             # LLM 调用封装
│   ├── prompts/domains/         # 6 领域提示词
│   └── schemas/nodes.py         # Node/Edge Pydantic schema
├── verify/
│   ├── web_searcher.py          # Serper.dev 搜索集成
│   └── author_profiler.py       # 作者档案分析
├── monitor/                     # 监控流水线（RSS/YouTube/Bilibili/Weibo）
├── commands/                    # CLI 命令实现
├── models.py                    # SQLModel 数据模型（Node/Edge/RawPost/Author）
├── llm_client.py                # 统一 LLM 客户端（含 Batch API）
├── config.py                    # 配置管理
└── cli.py                       # Click CLI 入口

sources.yaml                     # 信息源订阅列表
docs/
├── PRD.md                       # 产品需求文档
└── content_classification.md    # 内容分类体系
```

## 文档

- [PRD](docs/PRD.md) — 产品需求文档（v8 Node+Edge 架构）
- [内容分类体系](docs/content_classification.md) — 6 领域 × 节点类型详细定义

## License

MIT
