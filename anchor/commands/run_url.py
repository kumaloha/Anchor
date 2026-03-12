"""
anchor.commands.run_url — 单条 URL / 本地文件全链路分析
=====================================================
从根目录 run_url.py 迁入，移除 sys.path hack。
"""
from __future__ import annotations

import asyncio
import hashlib
import json as _json
import re
import sys
from datetime import datetime
from pathlib import Path

_PAYWALL_RE = re.compile(
    r"subscribe\s+to\s+(?:\w+\s+to\s+)?(read|continue|access|unlock|the full)"
    r"|subscriber[s']?\s+(only|to\s+(read|access|continue))"
    r"|members?\s+only"
    r"|sign\s+in\s+to\s+(read|access|continue|view)"
    r"|log\s+in\s+to\s+(read|access|continue|view)"
    r"|this\s+(content|article|story)\s+is\s+(only\s+)?for\s+(subscribers?|members?|premium)"
    r"|you.ve\s+(reached|used)\s+\d+\s+(of\s+(your\s+)?\d+\s+)?(free\s+)?(article|story)"
    r"|you\s+have\s+\d+\s+(free\s+)?(article|story)"
    r"|register\s+to\s+(read|access|continue)"
    r"|本文[为是]付费(内容|文章|阅读)"
    r"|订阅后[查看阅读]全文"
    r"|会员专属(内容|文章|阅读)"
    r"|付费(阅读|查看)全文",
    re.IGNORECASE,
)


# ── 本地文件读取 ──────────────────────────────────────────────────────────────

def _read_file(path: Path) -> str:
    """读取本地文件内容（支持 txt / md / pdf）。"""
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".pdf":
        try:
            import pypdf  # type: ignore
            import re as _re
            reader = pypdf.PdfReader(str(path))
            raw = "\n".join(page.extract_text() or "" for page in reader.pages)
            _img_exts = _re.compile(r"https?://\S+\.(?:jpg|jpeg|png|gif|webp|svg|bmp)\S*", _re.IGNORECASE)
            lines = [l for l in raw.splitlines() if not _img_exts.fullmatch(l.strip())]
            return "\n".join(lines)
        except ImportError:
            print(f"  WARNING: pypdf 未安装，跳过 PDF：{path.name}")
            return ""
    return ""


async def _save_local_file(path: Path) -> int | None:
    """将本地文件写入 DB，返回 RawPost.id（已存在则返回现有记录）。"""
    from anchor.database.session import AsyncSessionLocal
    from anchor.models import Author, MonitoredSource, RawPost, SourceType
    from sqlmodel import select

    content = _read_file(path)
    if not content.strip():
        return None

    abs_path = str(path.resolve())
    ext_id = hashlib.md5(abs_path.encode()).hexdigest()[:16]
    file_url = f"file://{abs_path}"
    author_name = path.stem

    async with AsyncSessionLocal() as s:
        existing = (await s.exec(
            select(RawPost).where(RawPost.source == "local", RawPost.external_id == ext_id)
        )).first()
        if existing:
            return existing.id

        author = (await s.exec(
            select(Author).where(Author.platform == "local", Author.platform_id == ext_id)
        )).first()
        if not author:
            author = Author(
                name=author_name,
                platform="local",
                platform_id=ext_id,
                profile_url=file_url,
            )
            s.add(author)
            await s.flush()

        src = (await s.exec(
            select(MonitoredSource).where(
                MonitoredSource.platform == "local",
                MonitoredSource.platform_id == ext_id,
            )
        )).first()
        if not src:
            src = MonitoredSource(
                url=file_url,
                source_type=SourceType.POST,
                platform="local",
                platform_id=ext_id,
                author_id=author.id,
                is_active=True,
            )
            s.add(src)
            await s.flush()

        rp = RawPost(
            source="local",
            external_id=ext_id,
            content=content,
            author_name=author_name,
            author_platform_id=ext_id,
            url=file_url,
            posted_at=datetime.utcfromtimestamp(path.stat().st_mtime),
            raw_metadata=_json.dumps({"title": path.name, "local_path": abs_path}, ensure_ascii=False),
            monitored_source_id=src.id,
        )
        s.add(rp)
        await s.commit()
        await s.refresh(rp)
        return rp.id


# ── 单条处理（URL 或 RawPost.id）────────────────────────────────────────────

async def _run_pipeline(raw_post_id: int, label: str) -> None:
    from anchor.database.session import AsyncSessionLocal
    from anchor.chains.general_assessment import run_assessment
    from anchor.extract.extractor import Extractor
    from anchor.models import RawPost
    from sqlmodel import select

    extractor = Extractor()

    async with AsyncSessionLocal() as s:
        rp = (await s.exec(select(RawPost).where(RawPost.id == raw_post_id))).first()
        rp.is_processed = False
        rp.assessed = False
        rp.assessed_at = None
        s.add(rp)
        await s.commit()

    print(f"      post_id={raw_post_id}  author={rp.author_name!r}")

    _meta: dict = {}
    try:
        _meta = _json.loads(rp.raw_metadata or "{}")
    except Exception:
        pass

    yt_redirect = _meta.get("youtube_redirect")
    if yt_redirect:
        print(f"  检测到 YouTube 嵌入，改抓: {yt_redirect}")
        await _main_url(yt_redirect)
        return

    _duration_s = _meta.get("duration_s") or 0
    if _duration_s and _duration_s < 180:
        print(f"  跳过：视频过短（{_duration_s}s < 180s）")
        return

    _content_chars = len((rp.content or "").strip())
    if _content_chars < 200:
        print(f"  跳过：文章内容过短（{_content_chars} 字 < 200 字）")
        return

    if _PAYWALL_RE.search(rp.content or ""):
        print(f"  跳过：检测到付费墙")
        return

    print(f"\n[2/4] 通用判断  内容分类 + 作者分析")
    from anchor.chains.general_assessment import resolve_content_mode
    async with AsyncSessionLocal() as s:
        pre = await run_assessment(raw_post_id, s)
    ct = pre.get("content_type", "")
    content_mode = resolve_content_mode(
        pre.get("content_domain"), pre.get("content_nature"), ct,
    )
    print(f"      domain={pre.get('content_domain')!r}  nature={pre.get('content_nature')!r}  type={ct!r}")
    print(f"      summary={pre.get('assessment_summary')!r}")
    print(f"      mode={content_mode}")

    print(f"\n[3/4] 内容提取（{content_mode} 模式）")
    async with AsyncSessionLocal() as s:
        rp3 = (await s.exec(select(RawPost).where(RawPost.id == raw_post_id))).first()
        result3 = await extractor.extract(
            rp3, s,
            content_mode=content_mode,
            author_intent=pre.get("author_intent"),
            force=True,
        )
    if result3 and result3.get("is_relevant_content"):
        nodes = result3.get("nodes", [])
        edges = result3.get("edges", 0)
        n_count = len(nodes) if isinstance(nodes, list) else nodes
        print(f"      {n_count} nodes  {edges} edges  domain={content_mode}")
        one_liner = result3.get("one_liner")
        if one_liner:
            print(f"      一句话: {one_liner}")
        summary = result3.get("summary")
        if summary:
            print(f"      摘要: {summary}")
        if isinstance(nodes, list):
            for node in nodes:
                node_type = node.node_type if hasattr(node, "node_type") else "?"
                abstract = node.abstract if hasattr(node, "abstract") and node.abstract else None
                claim = node.claim if hasattr(node, "claim") else str(node)
                label = abstract or claim[:80]
                print(f"        [{node_type}] {label}")
    elif result3:
        print(f"      内容不相关: {result3.get('skip_reason')}")
    else:
        print(f"      内容提取返回空（LLM 调用失败）")

    print(f"\n[4/4] 写入 Notion")
    try:
        from anchor.notion_sync import sync_post_to_notion
        async with AsyncSessionLocal() as s:
            notion_url = await sync_post_to_notion(raw_post_id, s)
            await s.commit()
        if notion_url:
            print(f"      {notion_url}")
        else:
            print(f"      跳过（content_type 未映射，或 Notion API 错误）")
    except Exception as e:
        import traceback
        print(f"      ERROR: {e}")
        traceback.print_exc()


# ── URL 入口 ──────────────────────────────────────────────────────────────────

async def _refetch_and_update(raw_post_id: int, url: str) -> None:
    """重新抓取 URL 内容，更新 RawPost 并清除旧实体数据。"""
    from anchor.database.session import AsyncSessionLocal
    from anchor.collect.input_handler import parse_url, _get_fetcher
    from anchor.models import Edge, Node, RawPost, PostQualityAssessment
    from sqlmodel import select, delete

    parsed = parse_url(url)
    fetcher = _get_fetcher(parsed.platform)
    if hasattr(fetcher, "set_url"):
        fetcher.set_url(parsed.canonical_url)
    raw_posts_data = await fetcher.fetch_post(parsed.platform_id)
    if not raw_posts_data:
        print("  WARNING: 重新抓取失败，使用已有内容")
        return

    new_data = raw_posts_data[0]
    async with AsyncSessionLocal() as s:
        rp = (await s.exec(select(RawPost).where(RawPost.id == raw_post_id))).first()

        rp.content = new_data.content
        rp.posted_at = new_data.posted_at
        rp.author_name = new_data.author_name
        rp.raw_metadata = _json.dumps(new_data.metadata, ensure_ascii=False) if new_data.metadata else rp.raw_metadata
        rp.media_json = _json.dumps(new_data.media_items, ensure_ascii=False) if new_data.media_items else None
        rp.collected_at = datetime.utcnow()
        s.add(rp)

        # 清除旧数据（Node/Edge + PostQualityAssessment）
        await s.exec(delete(Edge).where(Edge.added_by_post_id == raw_post_id))
        await s.exec(delete(Node).where(Node.raw_post_id == raw_post_id))
        await s.exec(delete(PostQualityAssessment).where(PostQualityAssessment.raw_post_id == raw_post_id))

        await s.commit()
        print(f"  已更新内容 + 清除旧数据  posted_at={rp.posted_at}")


async def _main_url(url: str) -> None:
    from anchor.database.session import AsyncSessionLocal
    from anchor.collect.input_handler import process_url

    print(f"[1/4] 采集  {url}")
    async with AsyncSessionLocal() as s:
        result = await process_url(url, s)
    if not result or not result.raw_posts:
        print("  ERROR: 采集失败")
        sys.exit(1)
    rp = result.raw_posts[0]

    if not result.is_new_source:
        print(f"  已有记录 (id={rp.id})，重新抓取并覆盖")
        await _refetch_and_update(rp.id, url)

    await _run_pipeline(rp.id, url)


# ── 本地文件 / 目录入口 ───────────────────────────────────────────────────────

_SUPPORTED_EXTS = {".txt", ".md", ".pdf"}


async def _main_local(path: Path) -> None:
    if path.is_file():
        files = [path]
    elif path.is_dir():
        files = sorted(f for f in path.rglob("*") if f.suffix.lower() in _SUPPORTED_EXTS)
        if not files:
            print(f"  目录下无可处理文件（{', '.join(_SUPPORTED_EXTS)}）：{path}")
            sys.exit(1)
        print(f"  发现 {len(files)} 个文件")
    else:
        print(f"  ERROR: 路径不存在：{path}")
        sys.exit(1)

    for i, f in enumerate(files, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(files)}] {f.name}")
        print(f"[1/4] 读取本地文件")
        rp_id = await _save_local_file(f)
        if rp_id is None:
            print(f"  跳过：文件为空或格式不支持")
            continue
        await _run_pipeline(rp_id, str(f))


# ── CLI 入口 ────────────────────────────────────────────────────────────────

def run_url_command(target: str, force: bool = False) -> None:
    """CLI 入口，由 anchor.cli 调用。"""
    arg = target
    if arg.startswith("file://"):
        arg = arg[len("file://"):]
    p = Path(arg)
    if p.exists():
        asyncio.run(_main_local(p))
    else:
        asyncio.run(_main_url(arg))
