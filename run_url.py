"""
run_url.py — 单条 URL / 本地文件 全链路分析并写入 Notion
==========================================================
Chain 2（内容分类 + 作者分析）→ Chain 1（实体提取）→ Notion 同步

用法：
    python run_url.py <url>
    python run_url.py 'https://robinjbrooks.substack.com/p/...'
    python run_url.py /path/to/article.txt
    python run_url.py /path/to/reports/          # 批量处理目录下所有文件
"""
from __future__ import annotations

import asyncio
import hashlib
import json as _json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./anchor_ui.db")


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
            # 过滤裸图片 URL 行（防止 Qwen VL 自动尝试下载图片）
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
    author_name = path.stem  # 文件名（不含扩展名）作为作者/来源

    async with AsyncSessionLocal() as s:
        # 已存在则直接返回
        existing = (await s.exec(
            select(RawPost).where(RawPost.source == "local", RawPost.external_id == ext_id)
        )).first()
        if existing:
            return existing.id

        # 创建 Author
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

        # 创建 MonitoredSource
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

        # 创建 RawPost
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
    from anchor.chains.chain2_author import run_chain2
    from anchor.extract.extractor import Extractor
    from anchor.models import RawPost
    from sqlmodel import select

    extractor = Extractor()

    # ── 强制重置 ──────────────────────────────────────────────────────────────
    async with AsyncSessionLocal() as s:
        rp = (await s.exec(select(RawPost).where(RawPost.id == raw_post_id))).first()
        rp.is_processed = False
        rp.chain2_analyzed = False
        rp.chain2_analyzed_at = None
        s.add(rp)
        await s.commit()

    print(f"      post_id={raw_post_id}  author={rp.author_name!r}")

    # ── 内容质量检查 ──────────────────────────────────────────────────────────
    _meta: dict = {}
    try:
        _meta = _json.loads(rp.raw_metadata or "{}")
    except Exception:
        pass

    _duration_s = _meta.get("duration_s") or 0
    if _duration_s and _duration_s < 180:
        print(f"  跳过：视频过短（{_duration_s}s < 180s）")
        return

    _content_chars = len((rp.content or "").strip())
    if _content_chars < 200:
        print(f"  跳过：文章内容过短（{_content_chars} 字 < 200 字）")
        return

    # ── Step 2: Chain 2 ───────────────────────────────────────────────────────
    print(f"\n[2/4] Chain 2  内容分类 + 作者分析")
    async with AsyncSessionLocal() as s:
        pre = await run_chain2(raw_post_id, s)
    ct = pre.get("content_type", "")
    content_mode = "policy" if ct in {"政策宣布", "政策解读"} else "standard"
    print(f"      content_type={ct!r}  intent={pre.get('author_intent')!r}")
    print(f"      mode={content_mode}")

    # ── Step 3: Chain 1 ───────────────────────────────────────────────────────
    print(f"\n[3/4] Chain 1  实体提取（{content_mode} 模式）")
    async with AsyncSessionLocal() as s:
        rp3 = (await s.exec(select(RawPost).where(RawPost.id == raw_post_id))).first()
        result3 = await extractor.extract(
            rp3, s,
            content_mode=content_mode,
            author_intent=pre.get("author_intent"),
            force=True,
        )
    if result3:
        f = len(result3.facts) if result3.facts else 0
        a = len(result3.assumptions) if result3.assumptions else 0
        i = len(result3.implicit_conditions) if result3.implicit_conditions else 0
        c = len(result3.conclusions) if result3.conclusions else 0
        p = len(result3.predictions) if result3.predictions else 0
        s_ = len(result3.solutions) if result3.solutions else 0
        r = len(result3.relationships) if result3.relationships else 0
        print(f"      {f}F  {a}A  {i}I  {c}C  {p}P  {s_}S  {r} edges")
        if result3.article_summary:
            print(f"      摘要: {result3.article_summary}")
        if not result3.is_relevant_content:
            print(f"      (内容较少，实体为空)")
    else:
        print(f"      Chain 1 返回空（LLM 调用失败）")

    # ── Step 4: Notion 同步 ───────────────────────────────────────────────────
    print(f"\n[4/4] 写入 Notion")
    try:
        from anchor.notion_sync import sync_post_to_notion
        async with AsyncSessionLocal() as s:
            notion_url = await sync_post_to_notion(raw_post_id, s)
        if notion_url:
            print(f"      {notion_url}")
        else:
            print(f"      跳过（content_type 未映射）")
    except Exception as e:
        import traceback
        print(f"      ERROR: {e}")
        traceback.print_exc()


# ── URL 入口 ──────────────────────────────────────────────────────────────────

async def main(url: str) -> None:
    from anchor.database.session import AsyncSessionLocal
    from anchor.collect.input_handler import process_url

    print(f"[1/4] 采集  {url}")
    async with AsyncSessionLocal() as s:
        result = await process_url(url, s)
    if not result or not result.raw_posts:
        print("  ERROR: 采集失败")
        sys.exit(1)
    rp = result.raw_posts[0]
    await _run_pipeline(rp.id, url)


# ── 本地文件 / 目录入口 ───────────────────────────────────────────────────────

_SUPPORTED_EXTS = {".txt", ".md", ".pdf"}


async def main_local(path: Path) -> None:
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


# ── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0 if "--help" in sys.argv else 1)

    arg = sys.argv[1]
    # 支持 file:// URI（如从 Finder 拖入终端）
    if arg.startswith("file://"):
        arg = arg[len("file://"):]   # file:///path → /path
    p = Path(arg)
    if p.exists():
        asyncio.run(main_local(p))
    else:
        asyncio.run(main(arg))
