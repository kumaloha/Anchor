"""
为 Notion 数据库中的所有页面生成并设置画廊封面图。
白底黑字，显示核心总结内容。
用法：python generate_covers.py
"""
from __future__ import annotations

import io
import os
import re
import sys
import textwrap

import httpx
from PIL import Image, ImageDraw, ImageFont

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_VERSION = "2022-06-28"
DB_ID = "31ca7586-d273-80c9-98eb-dea5cec01133"

# ── 字体 ──────────────────────────────────────────────────────────────────────
FONT_PATHS = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/Library/Fonts/Arial Unicode MS.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


# ── 图片生成 ──────────────────────────────────────────────────────────────────
W, H = 1500, 630


def _wrap(text: str, max_chars: int) -> list[str]:
    """中英文混排按字符数折行。"""
    lines: list[str] = []
    # 如果文字包含中文，按字符数折行
    is_cjk = len(re.findall(r"[\u4e00-\u9fff\u3000-\u303f]", text)) / max(len(text), 1) > 0.3
    if is_cjk:
        # CJK：每个汉字算一格，英文字母算 0.5 格
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
        lines = textwrap.wrap(text, width=max_chars * 2)
    return lines or [text]


def generate_cover(title: str, author: str, summary: str) -> bytes:
    img = Image.new("RGB", (W, H), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    PAD = 90
    font_author = _load_font(32)
    font_title  = _load_font(46)
    font_summary = _load_font(52)

    # ── 作者（顶部，浅灰）────────────────────────────────────────────────────
    draw.text((PAD, 60), author, font=font_author, fill=(160, 160, 160))

    # ── 分隔线 ──────────────────────────────────────────────────────────────
    draw.rectangle([(PAD, 120), (W - PAD, 123)], fill=(220, 220, 220))

    # ── 核心总结（居中区域，黑色大字）────────────────────────────────────────
    lines = _wrap(summary, max_chars=22)
    line_h = 72  # 行高
    total_h = len(lines) * line_h
    y = (H - total_h) // 2 + 20  # 整体垂直居中，略微下移留给作者行

    for line in lines:
        draw.text((PAD, y), line, font=font_summary, fill=(15, 15, 15))
        y += line_h

    # ── 标题（底部，深灰）───────────────────────────────────────────────────
    title_display = title[:50] + "…" if len(title) > 50 else title
    draw.text((PAD, H - 90), title_display, font=font_title, fill=(100, 100, 100))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── 图片上传（catbox.moe，永久存储，无需 API key）────────────────────────────

def upload_image(img_bytes: bytes, filename: str) -> str:
    """上传到 tmpfiles.org，返回直链 URL。"""
    resp = httpx.post(
        "https://tmpfiles.org/api/v1/upload",
        files={"file": (filename, img_bytes, "image/png")},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"tmpfiles.org error: {data}")
    # 将页面 URL 转为直链：http://tmpfiles.org/ID/file → https://tmpfiles.org/dl/ID/file
    page_url = data["data"]["url"]
    direct_url = page_url.replace("http://tmpfiles.org/", "https://tmpfiles.org/dl/")
    return direct_url


# ── Notion API ────────────────────────────────────────────────────────────────

_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
}


def query_pages() -> list[dict]:
    resp = httpx.post(
        f"https://api.notion.com/v1/databases/{DB_ID}/query",
        headers=_HEADERS,
        json={},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def set_cover(page_id: str, image_url: str) -> None:
    resp = httpx.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers={**_HEADERS, "Content-Type": "application/json"},
        json={"cover": {"type": "external", "external": {"url": image_url}}},
        timeout=30,
    )
    resp.raise_for_status()


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    print("Querying Notion database...")
    pages = query_pages()
    print(f"Found {len(pages)} pages.\n")

    for page in pages:
        page_id = page["id"]
        props = page.get("properties", {})

        title_rt = props.get("名称", {}).get("title", [])
        title = title_rt[0]["text"]["content"] if title_rt else "（无标题）"

        summary_rt = props.get("核心总结", {}).get("rich_text", [])
        summary = summary_rt[0]["text"]["content"] if summary_rt else ""

        author_rt = props.get("作者", {}).get("rich_text", [])
        author = author_rt[0]["text"]["content"].split("\n")[0] if author_rt else ""

        if not summary:
            print(f"  SKIP [{page_id}] {title!r} — 无核心总结")
            continue

        print(f"  Generating cover for: {title!r} ({author})")
        img_bytes = generate_cover(title=title, author=author, summary=summary)

        safe_title = re.sub(r"[^\w\u4e00-\u9fff]", "_", title)[:30]
        filename = f"cover_{safe_title}.png"

        print(f"    Uploading {len(img_bytes)//1024}KB...")
        try:
            img_url = upload_image(img_bytes, filename)
            print(f"    URL: {img_url}")
        except Exception as e:
            print(f"    Upload failed: {e}")
            continue

        try:
            set_cover(page_id, img_url)
            print(f"    Cover set ✓")
        except Exception as e:
            print(f"    Set cover failed: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()
