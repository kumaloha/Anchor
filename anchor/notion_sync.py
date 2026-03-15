"""
Notion 同步模块 — 将 Anchor 提取结果写入对应 Notion 数据库
===========================================================
触发时机：事实验证完成后（standard 模式）或内容提取完成后（policy 模式）
当前启用：市场动向、市场分析
"""

import io
import logging
import os
import re
from collections import defaultdict
from typing import List, Optional

import httpx
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.config import settings
from anchor.models import (
    Author,
    AuthorStanceProfile,
    MonitoredSource,
    PostQualityAssessment,
    RawPost,
)

logger = logging.getLogger(__name__)

NOTION_API_KEY = settings.notion_api_key or os.environ.get("NOTION_API_KEY", "")
NOTION_VERSION = "2022-06-28"

# content_type → Notion Database ID（财经分析页面已改名为「财经分析」）
_DEFAULT_NOTION_DB = "31ca7586-d273-80c9-98eb-dea5cec01133"

NOTION_DB_MAP: dict[str, str] = {
    "财经分析":   _DEFAULT_NOTION_DB,
    "市场动向":   _DEFAULT_NOTION_DB,
    "产业链研究": _DEFAULT_NOTION_DB,
    "公司调研":   _DEFAULT_NOTION_DB,
    "政策解读":   _DEFAULT_NOTION_DB,
}

# 实体类型前缀
_ETYPE_PREFIX = {
    "fact": "F",
    "assumption": "A",
    "implicit_condition": "H",
    "conclusion": "C",
    "prediction": "P",
    "solution": "S",
    "theory": "T",
}

# verdict 值 → 显示符号（各实体类型独立映射）
_VERDICT_SYM: dict[str, dict[str, str]] = {
    "fact": {
        "credible": "✓", "vague": "≈", "unreliable": "✗",
    },
    "assumption": {
        "high_probability": "✓", "medium_probability": "≈", "low_probability": "✗",
    },
    "implicit_condition": {
        "consensus": "✓", "contested": "≈", "false": "✗",
    },
    "conclusion": {
        "confirmed": "✓", "partial": "≈", "refuted": "✗",
    },
    "prediction": {
        "accurate": "✓", "directional": "≈", "off_target": "≈", "wrong": "✗",
    },
    "solution": {},
    "theory": {},
}

# 各实体类型对应的 verdict 字段名
_VERDICT_FIELD: dict[str, Optional[str]] = {
    "fact":               "fact_verdict",
    "assumption":         "assumption_verdict",
    "implicit_condition": "implicit_verdict",
    "conclusion":         "conclusion_verdict",
    "prediction":         "prediction_verdict",
    "solution":           None,
    "theory":             None,
}


def _label(etype: str, idx: int) -> str:
    return f"{_ETYPE_PREFIX.get(etype, etype[0].upper())}{idx}"


def _rt(text: str, max_len: int = 2000) -> list[dict]:
    """Notion rich_text 数组，单块最多 2000 字符。"""
    content = (text or "")[:max_len]
    return [{"type": "text", "text": {"content": content}}]


# 匹配行中任意位置出现的结论标号（C1, C2...），用于红色高亮
_LOGIC_LINE_RE = re.compile(r"C\d+")


def _rt_logic(text: str) -> list[dict]:
    """逻辑列专用 rich_text：含结论标号（C\d+）的行用红色，其余行保持默认颜色。

    Notion rich_text 是扁平数组，通过拼接相同颜色的连续行减少分段数量。
    单段内容超过 1800 字符时自动切断，以满足 Notion 2000 字符/段的上限。
    """
    if not text:
        return [{"type": "text", "text": {"content": ""}}]

    def _flush(content: str, is_red: bool) -> dict:
        block: dict = {"type": "text", "text": {"content": content[:2000]}}
        if is_red:
            block["annotations"] = {"color": "red"}
        return block

    result: list[dict] = []
    lines = text.split("\n")
    cur_content = ""
    cur_red = False

    for i, line in enumerate(lines):
        is_red = bool(_LOGIC_LINE_RE.search(line))
        segment = line + ("\n" if i < len(lines) - 1 else "")

        if is_red != cur_red and cur_content:
            result.append(_flush(cur_content, cur_red))
            cur_content = ""

        cur_red = is_red
        cur_content += segment

        if len(cur_content) >= 1800:
            result.append(_flush(cur_content, cur_red))
            cur_content = ""

    if cur_content:
        result.append(_flush(cur_content, cur_red))

    return result or [{"type": "text", "text": {"content": ""}}]


def _fmt_entity(e, etype: str, lbl: str) -> str:
    """单条实体：'F1 ✓ 摘要'"""
    vfield = _VERDICT_FIELD.get(etype)
    sym_map = _VERDICT_SYM.get(etype, {})
    sym = ""
    if vfield:
        v = getattr(e, vfield, None)
        if v in sym_map:
            sym = " " + sym_map[v]
    summary = getattr(e, "summary", None)
    if not summary:
        raw = getattr(e, "claim", None) or getattr(e, "condition_text", "") or ""
        summary = raw[:40] + ("…" if len(raw) > 40 else "")
    return f"{lbl}{sym} {summary}"


def _entity_text(entities: list, etype: str, incoming: dict[int, list[str]]) -> str:
    """方案列用，每行一条。"""
    lines = []
    for i, e in enumerate(entities, 1):
        lbl = _label(etype, i)
        src_str = ""
        sources = incoming.get(e.id, [])
        if sources:
            src_str = f" ({','.join(sources)})"
        lines.append(_fmt_entity(e, etype, lbl) + src_str)
    return "\n".join(lines)


def _build_dag_column(
    conclusions: list,
    facts: list,
    assumptions: list,
    implicits: list,
    label_map: dict[str, dict[int, str]],
    rels: list,
    theories: Optional[List] = None,
    predictions: Optional[List] = None,
    solutions: Optional[List] = None,
) -> str:
    """
    将所有实体 + 关系渲染为 ASCII DAG，从输出端（预测/方案/核心结论）向下追溯支撑链。

    格式示例：
        ▶ P1 ≈ 美联储将在Q3降息
          └─ C1 ✓ 通胀受控、就业稳定
             ├─ F1 ✓ CPI连续三月低于3%
             └─ F2 ✓ 失业率维持4.1%

        ▶ C2 ✓ 科技股估值偏高
          ├─ C1 (见上)
          └─ A1 ≈ 市场无系统性风险

        [孤立] F3 ✓ 未引用的事实
    """
    theories   = theories   or []
    predictions = predictions or []
    solutions  = solutions  or []

    # 构建实体查找表 key=(etype, id) → entity
    entity_by_key: dict[tuple, object] = {}
    for e in facts:       entity_by_key[("fact", e.id)] = e
    for e in assumptions: entity_by_key[("assumption", e.id)] = e
    for e in implicits:   entity_by_key[("implicit_condition", e.id)] = e
    for e in conclusions: entity_by_key[("conclusion", e.id)] = e
    for e in theories:    entity_by_key[("theory", e.id)] = e
    for e in predictions: entity_by_key[("prediction", e.id)] = e
    for e in solutions:   entity_by_key[("solution", e.id)] = e

    # 构建入边/出边索引（source → target 方向，表示"source 支撑 target"）
    in_edges: dict[tuple, list[tuple]]  = defaultdict(list)  # tgt → [src]
    out_edges: dict[tuple, list[tuple]] = defaultdict(list)  # src → [tgt]
    for rel in rels:
        src = (rel.source_type, rel.source_id)
        tgt = (rel.target_type, rel.target_id)
        if src in entity_by_key and tgt in entity_by_key:
            out_edges[src].append(tgt)
            in_edges[tgt].append(src)

    def _lbl(key: tuple) -> str:
        return label_map.get(key[0], {}).get(key[1], f"{key[0][0].upper()}?")

    def _summary_line(key: tuple) -> str:
        return _fmt_entity(entity_by_key[key], key[0], _lbl(key))

    # 输出端节点（在结论/理论/预测/方案中，没有出边的节点）
    output_types = {"conclusion", "theory", "prediction", "solution"}
    top_nodes = [k for k in entity_by_key if k[0] in output_types and not out_edges.get(k)]
    # 排序：预测 > 方案 > 理论 > 结论；同类型按 id 升序
    _top_priority = {"prediction": 0, "solution": 1, "theory": 2, "conclusion": 3}
    top_nodes.sort(key=lambda k: (_top_priority.get(k[0], 9), k[1]))

    visited: set[tuple] = set()
    lines: list[str] = []

    # 支撑实体渲染顺序：理论 > 结论 > 事实 > 假设 > 隐含
    _supp_priority = {"theory": 0, "conclusion": 1, "fact": 2, "assumption": 3, "implicit_condition": 4}

    def _render(key: tuple, prefix: str, is_last: bool, depth: int) -> None:
        connector = "└─ " if is_last else "├─ "
        indent = (prefix + connector) if depth > 0 else ""

        if key in visited:
            # 已渲染过 → 只引用标号，不展开
            ref = f"{indent}{_lbl(key)} (见上)" if depth > 0 else f"▶ {_lbl(key)} (见上)"
            lines.append(ref)
            return

        visited.add(key)

        if depth == 0:
            lines.append(f"▶ {_summary_line(key)}")
        else:
            lines.append(f"{indent}{_summary_line(key)}")

        supporters = sorted(
            in_edges.get(key, []),
            key=lambda k: (_supp_priority.get(k[0], 5), k[1]),
        )
        child_prefix = ("  " if depth == 0 else prefix + ("   " if is_last else "│  "))
        for i, supp in enumerate(supporters):
            _render(supp, child_prefix, i == len(supporters) - 1, depth + 1)

    for i, top in enumerate(top_nodes):
        if i > 0:
            lines.append("")
        _render(top, "", True, 0)

    # 有出边但未被 DFS 访问到的结论/理论（极罕见，如孤立中间节点）
    unvisited_abstract = [k for k in entity_by_key if k[0] in output_types and k not in visited]
    if unvisited_abstract:
        if lines:
            lines.append("")
        lines.append("[其他]")
        for key in sorted(unvisited_abstract, key=lambda k: k[1]):
            lines.append(f"  {_summary_line(key)}")
            visited.add(key)

    # 孤立叶节点（事实/假设/隐含，未被任何上层实体引用）
    leaf_types = {"fact", "assumption", "implicit_condition"}
    _leaf_priority = {"fact": 0, "assumption": 1, "implicit_condition": 2}
    orphan_leaves = [k for k in entity_by_key if k[0] in leaf_types and k not in visited]
    if orphan_leaves:
        if lines:
            lines.append("")
        lines.append("[孤立]")
        for key in sorted(orphan_leaves, key=lambda k: (_leaf_priority.get(k[0], 3), k[1])):
            lines.append(f"  {_summary_line(key)}")

    return "\n".join(lines)


def _fmt_stance(dominant_stance: str) -> str:
    """将四维立场文本格式化为简洁展示。

    dominant_stance 格式（每行 "维度｜值"）：
      意识形态｜自由市场主义
      地缘立场｜亲美
      利益代表｜独立分析师
      客观性｜相对客观

    规则：
    - 客观性包含"客观" → 只返回"相对客观"
    - 否则 → 将非客观性维度的值拼为一句（" · " 分隔）
    """
    dims: dict[str, str] = {}
    for line in dominant_stance.strip().splitlines():
        if "｜" in line:
            k, _, v = line.partition("｜")
            dims[k.strip()] = v.strip()

    objectivity = dims.get("客观性", "")
    if "客观" in objectivity:
        return "相对客观"

    parts = [v for k, v in dims.items() if k != "客观性" and v and v != "无法判断"]
    return " · ".join(parts) if parts else dominant_stance


# ── 封面图生成 ────────────────────────────────────────────────────────────────

_FONT_PATHS = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/Library/Fonts/Arial Unicode MS.ttf",
]
_COVER_W, _COVER_H = 1500, 630


def _load_font(size: int):
    try:
        from PIL import ImageFont
        for path in _FONT_PATHS:
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()
    except Exception:
        return None


def _wrap_text(text: str, max_chars: int) -> list[str]:
    is_cjk = len(re.findall(r"[\u4e00-\u9fff]", text)) / max(len(text), 1) > 0.3
    lines: list[str] = []
    if is_cjk:
        current, count = "", 0.0
        for ch in text:
            w = 1.0 if "\u4e00" <= ch <= "\u9fff" else 0.5
            if count + w > max_chars and current:
                lines.append(current)
                current, count = ch, w
            else:
                current += ch
                count += w
        if current:
            lines.append(current)
    else:
        import textwrap
        lines = textwrap.wrap(text, width=max_chars * 2)
    return lines or [text]


def _generate_cover_bytes(summary: str, author: str, title: str) -> Optional[bytes]:
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (_COVER_W, _COVER_H), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        PAD = 90
        font_author  = _load_font(32)
        font_summary = _load_font(52)
        font_title   = _load_font(46)

        draw.text((PAD, 60), author, font=font_author, fill=(160, 160, 160))
        draw.rectangle([(PAD, 120), (_COVER_W - PAD, 123)], fill=(220, 220, 220))

        lines = _wrap_text(summary, max_chars=22)
        line_h = 72
        total_h = len(lines) * line_h
        y = (_COVER_H - total_h) // 2 + 20
        for line in lines:
            draw.text((PAD, y), line, font=font_summary, fill=(15, 15, 15))
            y += line_h

        title_display = title[:50] + "…" if len(title) > 50 else title
        draw.text((PAD, _COVER_H - 90), title_display, font=font_title, fill=(100, 100, 100))

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as e:
        logger.warning("cover generation failed: %s", e)
        return None


def _upload_cover(img_bytes: bytes, filename: str) -> Optional[str]:
    try:
        resp = httpx.post(
            "https://tmpfiles.org/api/v1/upload",
            files={"file": (filename, img_bytes, "image/png")},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(data)
        page_url = data["data"]["url"]
        return page_url.replace("http://tmpfiles.org/", "https://tmpfiles.org/dl/")
    except Exception as e:
        logger.warning("cover upload failed: %s", e)
        return None


async def sync_post_to_notion(post_id: int, session: AsyncSession) -> Optional[str]:
    """
    将 post_id 对应的提取结果写入 Notion。
    返回创建的页面 URL；若该 content_type 未启用则返回 None。
    """
    # ── 1. 加载 RawPost ──────────────────────────────────────────────────────
    post = (await session.exec(select(RawPost).where(RawPost.id == post_id))).first()
    if not post:
        logger.warning("notion_sync: post %s not found", post_id)
        return None

    ct = post.content_type or ""
    db_id = NOTION_DB_MAP.get(ct)
    if not db_id:
        logger.info("notion_sync: skipping post %s (content_type=%s)", post_id, ct)
        return None

    # ── 2. 作者信息 ──────────────────────────────────────────────────────────
    author_bg = ""
    author_stance = ""

    if post.monitored_source_id:
        ms = (await session.exec(
            select(MonitoredSource).where(MonitoredSource.id == post.monitored_source_id)
        )).first()
        if ms and ms.author_id:
            author = (await session.exec(
                select(Author).where(Author.id == ms.author_id)
            )).first()
            if author:
                # 背景：有组织/职位则只写 role，否则留空
                author_bg = author.role or ""

                asp = (await session.exec(
                    select(AuthorStanceProfile).where(AuthorStanceProfile.author_id == author.id)
                )).first()
                if asp and asp.dominant_stance:
                    author_stance = _fmt_stance(asp.dominant_stance)

    # PostQualityAssessment 的单篇立场优先级更高
    pqa = (await session.exec(
        select(PostQualityAssessment).where(PostQualityAssessment.raw_post_id == post_id)
    )).first()
    if pqa and pqa.stance_label:
        author_stance = pqa.stance_label

    # ── 3. 构建逻辑列文本（ExtractionNode/Edge 已移除，使用摘要替代）──────
    _logic_text = post.content_summary or ""

    # ── 5. 构建 Notion 页面属性 ───────────────────────────────────────────────
    _raw_title = ""
    if post.raw_metadata:
        import json as _json
        try:
            _raw_title = _json.loads(post.raw_metadata).get("title", "")
        except Exception:
            pass
    title = post.content_topic or _raw_title or post.author_name or "（无标题）"

    properties: dict = {
        "名称":    {"title": _rt(title, 200)},
        "日期":    {"date": {"start": post.posted_at.strftime("%Y-%m-%d")} if post.posted_at else None},
        "链接":    {"url": post.url or None},
        "作者":    {"rich_text": _rt("\n\n".join(filter(None, [
                       post.author_name or "",
                       f"背景｜{author_bg}"      if author_bg      else "",
                       f"立场｜{author_stance}"  if author_stance  else "",
                       f"意图｜{post.author_intent}" if post.author_intent else "",
                   ])))},
        "已读":    {"checkbox": False},
        "核心总结": {"rich_text": _rt(post.content_summary or "")},
        "逻辑":    {"rich_text": _rt_logic(_logic_text)},
    }
    # 财经分析子分类 → "分类" 列（Select 类型）
    if post.content_subtype:
        properties["分类"] = {"select": {"name": post.content_subtype}}

    # ── 6. 生成封面图 ─────────────────────────────────────────────────────────
    payload: dict = {
        "parent": {"database_id": db_id},
        "properties": properties,
    }
    if post.content_summary:
        safe = re.sub(r"[^\w\u4e00-\u9fff]", "_", title)[:30]
        img_bytes = _generate_cover_bytes(
            summary=post.content_summary,
            author=post.author_name or "",
            title=title,
        )
        if img_bytes:
            cover_url = _upload_cover(img_bytes, f"cover_{safe}.png")
            if cover_url:
                payload["cover"] = {"type": "external", "external": {"url": cover_url}}
                logger.info("notion_sync: cover uploaded → %s", cover_url)

    # ── 7. 发送到 Notion API（有则 UPDATE，无则 CREATE）─────────────────────

    _headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }

    existing_page_id = post.notion_page_id
    is_update = bool(existing_page_id)
    async with httpx.AsyncClient(timeout=30) as client:
        if existing_page_id:
            # UPDATE：只更新 properties（不能改 parent）
            resp = await client.patch(
                f"https://api.notion.com/v1/pages/{existing_page_id}",
                headers=_headers,
                json={"properties": properties},
            )
            # 页面已归档 → 先解档再重试；若解档失败则降级为 CREATE
            if resp.status_code == 400 and "archived" in resp.text:
                logger.warning("notion_sync: page %s is archived, attempting unarchive", existing_page_id)
                unarchive = await client.patch(
                    f"https://api.notion.com/v1/pages/{existing_page_id}",
                    headers=_headers,
                    json={"archived": False},
                )
                if unarchive.status_code in (200, 201):
                    resp = await client.patch(
                        f"https://api.notion.com/v1/pages/{existing_page_id}",
                        headers=_headers,
                        json={"properties": properties},
                    )
                else:
                    logger.warning(
                        "notion_sync: unarchive failed (%s), falling back to CREATE",
                        unarchive.status_code,
                    )
                    existing_page_id = None
                    is_update = False
                    resp = await client.post(
                        "https://api.notion.com/v1/pages",
                        headers=_headers,
                        json=payload,
                    )
        else:
            # CREATE
            resp = await client.post(
                "https://api.notion.com/v1/pages",
                headers=_headers,
                json=payload,
            )

    if resp.status_code not in (200, 201):
        logger.error("notion_sync: API error %s: %s", resp.status_code, resp.text[:400])
        return None

    resp_data = resp.json()
    page_url = resp_data.get("url", "")
    page_id  = resp_data.get("id", "")

    if is_update:
        logger.info("notion_sync: updated page %s for post %s", page_url, post_id)
    else:
        logger.info("notion_sync: created page %s for post %s (type=%s)", page_url, post_id, ct)
        if page_id:
            post.notion_page_id = page_id
            session.add(post)
            await session.flush()

    return page_url
