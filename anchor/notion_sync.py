"""
Notion 同步模块 — 将 Anchor 提取结果写入对应 Notion 数据库
===========================================================
触发时机：Chain 3 完成后（standard 模式）或 Chain 1 完成后（policy 模式）
当前启用：市场动向、市场分析
"""

import io
import logging
import os
import re
from typing import Optional

import httpx
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.config import settings
from anchor.models import (
    Assumption,
    Author,
    AuthorStanceProfile,
    Conclusion,
    EntityRelationship,
    Fact,
    ImplicitCondition,
    MonitoredSource,
    PostQualityAssessment,
    Prediction,
    RawPost,
    Solution,
)

logger = logging.getLogger(__name__)

NOTION_API_KEY = settings.notion_api_key or os.environ.get("NOTION_API_KEY", "")
NOTION_VERSION = "2022-06-28"

# content_type → Notion Database ID（财经分析页面已改名为「财经分析」）
NOTION_DB_MAP: dict[str, str] = {
    "财经分析": "31ca7586-d273-80c9-98eb-dea5cec01133",
    "市场动向":  "31ca7586-d273-80c9-98eb-dea5cec01133",
    # 以下暂未启用（待补充 Database ID）
    # "产业链研究": "",
    # "公司调研": "",
    # "技术论文": "",
    # "政策解读": "",
}

# 实体类型前缀
_ETYPE_PREFIX = {
    "fact": "F",
    "assumption": "A",
    "implicit_condition": "H",
    "conclusion": "C",
    "prediction": "P",
    "solution": "S",
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
}

# 各实体类型对应的 verdict 字段名
_VERDICT_FIELD: dict[str, Optional[str]] = {
    "fact":               "fact_verdict",
    "assumption":         "assumption_verdict",
    "implicit_condition": "implicit_verdict",
    "conclusion":         "conclusion_verdict",
    "prediction":         "prediction_verdict",
    "solution":           None,
}


def _label(etype: str, idx: int) -> str:
    return f"{_ETYPE_PREFIX.get(etype, etype[0].upper())}{idx}"


def _rt(text: str, max_len: int = 2000) -> list[dict]:
    """Notion rich_text 数组，单块最多 2000 字符。"""
    content = (text or "")[:max_len]
    return [{"type": "text", "text": {"content": content}}]


_CONCLUSION_LINE_RE = re.compile(r"^C\d+")


def _rt_conclusion(text: str) -> list[dict]:
    """结论列专用 rich_text：以 C 开头的结论行用红色，其余行保持默认颜色。

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
        is_red = bool(_CONCLUSION_LINE_RE.match(line))
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


def _build_conclusion_column(
    conclusions: list,
    facts: list,
    assumptions: list,
    implicits: list,
    label_map: dict[str, dict[int, str]],
    rels: list,
) -> str:
    """
    每个结论独占一段，格式：
      C1 ✓ 结论摘要
      [事实] F1 ✓ 摘要 | F2 ✗ 摘要
      [假设] A1 ≈ 摘要
      [隐含] H1 ✓ 摘要

    多个结论之间空两行。
    不被任何结论引用的孤立实体附在最后（[孤立事实] 等）。
    """
    # 反查：entity_id → entity 对象（按类型）
    fact_by_id  = {e.id: e for e in facts}
    assm_by_id  = {e.id: e for e in assumptions}
    impl_by_id  = {e.id: e for e in implicits}

    # 按结论 id 收集支撑实体（入边）
    conc_by_id = {c.id: c for c in conclusions}
    supports: dict[int, dict[str, list]] = {
        c.id: {"fact": [], "assumption": [], "implicit_condition": [], "conclusion": []}
        for c in conclusions
    }
    used_facts: set[int] = set()
    used_assms: set[int] = set()
    used_impls: set[int] = set()
    used_concs: set[int] = set()

    for rel in rels:
        if rel.target_type == "conclusion" and rel.target_id in supports:
            if rel.source_type == "fact" and rel.source_id in fact_by_id:
                supports[rel.target_id]["fact"].append(rel.source_id)
                used_facts.add(rel.source_id)
            elif rel.source_type == "assumption" and rel.source_id in assm_by_id:
                supports[rel.target_id]["assumption"].append(rel.source_id)
                used_assms.add(rel.source_id)
            elif rel.source_type == "implicit_condition" and rel.source_id in impl_by_id:
                supports[rel.target_id]["implicit_condition"].append(rel.source_id)
                used_impls.add(rel.source_id)
            elif rel.source_type == "conclusion" and rel.source_id in conc_by_id:
                supports[rel.target_id]["conclusion"].append(rel.source_id)
                used_concs.add(rel.source_id)

    blocks: list[str] = []

    for c in conclusions:
        clbl = label_map["conclusion"].get(c.id, "C?")
        lines = [_fmt_entity(c, "conclusion", clbl)]

        # [事实]
        f_parts = [
            _fmt_entity(fact_by_id[fid], "fact", label_map["fact"].get(fid, "F?"))
            for fid in supports[c.id]["fact"]
        ]
        if f_parts:
            lines.append("[事实] " + " | ".join(f_parts))

        # [假设]
        a_parts = [
            _fmt_entity(assm_by_id[aid], "assumption", label_map["assumption"].get(aid, "A?"))
            for aid in supports[c.id]["assumption"]
        ]
        if a_parts:
            lines.append("[假设] " + " | ".join(a_parts))

        # [隐含]
        h_parts = [
            _fmt_entity(impl_by_id[hid], "implicit_condition", label_map["implicit_condition"].get(hid, "H?"))
            for hid in supports[c.id]["implicit_condition"]
        ]
        if h_parts:
            lines.append("[隐含] " + " | ".join(h_parts))

        # [依据结论]
        c_parts = [
            _fmt_entity(conc_by_id[cid], "conclusion", label_map["conclusion"].get(cid, "C?"))
            for cid in supports[c.id]["conclusion"]
        ]
        if c_parts:
            lines.append("[依据] " + " | ".join(c_parts))

        blocks.append("\n".join(lines))

    result = "\n\n\n".join(blocks)  # 两个空行分隔

    # 孤立实体（未被任何结论引用）
    orphan_lines: list[str] = []
    orphan_facts = [e for e in facts if e.id not in used_facts]
    if orphan_facts:
        parts = [_fmt_entity(e, "fact", label_map["fact"].get(e.id, "F?")) for e in orphan_facts]
        orphan_lines.append("[孤立事实] " + " | ".join(parts))
    orphan_assms = [e for e in assumptions if e.id not in used_assms]
    if orphan_assms:
        parts = [_fmt_entity(e, "assumption", label_map["assumption"].get(e.id, "A?")) for e in orphan_assms]
        orphan_lines.append("[孤立假设] " + " | ".join(parts))
    orphan_impls = [e for e in implicits if e.id not in used_impls]
    if orphan_impls:
        parts = [_fmt_entity(e, "implicit_condition", label_map["implicit_condition"].get(e.id, "H?")) for e in orphan_impls]
        orphan_lines.append("[孤立隐含] " + " | ".join(parts))

    if orphan_lines:
        result = result + "\n\n\n" + "\n".join(orphan_lines) if result else "\n".join(orphan_lines)

    return result


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

    # ── 3. 加载六实体 ─────────────────────────────────────────────────────────
    facts       = list((await session.exec(select(Fact).where(Fact.raw_post_id == post_id))).all())
    assumptions = list((await session.exec(select(Assumption).where(Assumption.raw_post_id == post_id))).all())
    implicits   = list((await session.exec(select(ImplicitCondition).where(ImplicitCondition.raw_post_id == post_id))).all())
    conclusions = list((await session.exec(select(Conclusion).where(Conclusion.raw_post_id == post_id))).all())
    predictions = list((await session.exec(select(Prediction).where(Prediction.raw_post_id == post_id))).all())
    solutions   = list((await session.exec(select(Solution).where(Solution.raw_post_id == post_id))).all())

    # ── 4. 构建标号映射 + 入边索引 ───────────────────────────────────────────
    label_map: dict[str, dict[int, str]] = {
        "fact":               {e.id: _label("fact", i)               for i, e in enumerate(facts, 1)},
        "assumption":         {e.id: _label("assumption", i)         for i, e in enumerate(assumptions, 1)},
        "implicit_condition": {e.id: _label("implicit_condition", i) for i, e in enumerate(implicits, 1)},
        "conclusion":         {e.id: _label("conclusion", i)         for i, e in enumerate(conclusions, 1)},
        "prediction":         {e.id: _label("prediction", i)         for i, e in enumerate(predictions, 1)},
        "solution":           {e.id: _label("solution", i)           for i, e in enumerate(solutions, 1)},
    }

    # incoming[target_type][target_id] = [source_label, ...]
    incoming: dict[str, dict[int, list[str]]] = {et: {} for et in label_map}
    rels = list((await session.exec(
        select(EntityRelationship).where(EntityRelationship.raw_post_id == post_id)
    )).all())
    for rel in rels:
        src_lbl = label_map.get(rel.source_type, {}).get(rel.source_id)
        if src_lbl and rel.target_type in incoming:
            incoming[rel.target_type].setdefault(rel.target_id, []).append(src_lbl)

    # ── 5. 构建 Notion 页面属性 ───────────────────────────────────────────────
    # 优先 Chain 2 主题，其次原始文章标题（存在 raw_metadata.title），最后作者名
    _raw_title = ""
    if post.raw_metadata:
        import json as _json
        try:
            _raw_title = _json.loads(post.raw_metadata).get("title", "")
        except Exception:
            pass
    title = post.content_topic or _raw_title or post.author_name or "（无标题）"

    # 结论 + 预测 + 方案 合并为一列，预测和方案各空两行接在结论之后
    _conc_text = _build_conclusion_column(
        conclusions, facts, assumptions, implicits, label_map, rels
    )
    _pred_text = _entity_text(predictions, "prediction", incoming["prediction"])
    _soln_text = _entity_text(solutions,   "solution",   incoming["solution"])
    _combined_parts = [_conc_text] if _conc_text else []
    if _pred_text.strip():
        _combined_parts.append(_pred_text)
    if _soln_text.strip():
        _combined_parts.append(_soln_text)
    _conclusion_combined = "\n\n\n".join(_combined_parts)

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
        "结论":    {"rich_text": _rt_conclusion(_conclusion_combined)},
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

    # ── 7. 发送到 Notion API ──────────────────────────────────────────────────

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.notion.com/v1/pages",
            headers={
                "Authorization": f"Bearer {NOTION_API_KEY}",
                "Content-Type": "application/json",
                "Notion-Version": NOTION_VERSION,
            },
            json=payload,
        )

    if resp.status_code not in (200, 201):
        logger.error("notion_sync: API error %s: %s", resp.status_code, resp.text[:400])
        return None

    resp_data = resp.json()
    page_url = resp_data.get("url", "")
    page_id  = resp_data.get("id", "")
    logger.info("notion_sync: created page %s for post %s (type=%s)", page_url, post_id, ct)

    # 将 Notion 页面 ID 写回 DB，便于后续更新
    if page_id:
        post.notion_page_id = page_id
        session.add(post)
        await session.flush()

    return page_url
