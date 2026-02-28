"""
媒体描述器 — Layer 1 Step 1.5
==============================
对帖子中的图片调用视觉 LLM，生成文字描述，以便 Layer2 提取器将图片内容纳入分析。

设计原则：
  - 只处理图片（photo / gif），视频暂不支持（无法传递给视觉模型）
  - 每张图片单独调用一次视觉模型，结果按序合并
  - 若视觉模型未配置或调用失败，静默返回 None（不阻断流程）
"""

from __future__ import annotations

import json

from loguru import logger

from anchor.llm_client import chat_completion_multimodal
from anchor.models import RawPost


_SYSTEM = """\
你是一名内容分析助手，专门解读图片中的信息。
请详细描述图片内容，重点关注：
- 文字信息（标题、说明、注解、数字）
- 图表数据（折线图、柱状图、饼图、表格中的数值和趋势）
- 截图内容（新闻截图、公告、财报页面的关键数字）
- 任何与经济、金融、政策相关的可见信息

用中文输出纯文本描述，不加任何前缀或格式标记，不说"这张图片显示"等套话，直接陈述内容。
"""

_PROMPT = "请提取并描述这张图片中的所有关键信息。"


async def describe_media(post: RawPost) -> str | None:
    """对帖子中的图片生成文字描述。

    返回合并后的描述字符串（如 "[图1] ... \n\n[图2] ..."），
    无图片或全部失败时返回 None。
    """
    if not post.media_json:
        return None

    try:
        items: list[dict] = json.loads(post.media_json)
    except Exception:
        return None

    # 只处理图片（视频/gif 无法传给视觉模型）
    photo_urls = [item["url"] for item in items if item.get("type") in ("photo", "gif")]
    if not photo_urls:
        return None

    descriptions: list[str] = []
    for i, url in enumerate(photo_urls, 1):
        logger.info(f"[MediaDescriber] 描述图片 {i}/{len(photo_urls)}: {url[:80]}")
        resp = await chat_completion_multimodal(
            system=_SYSTEM,
            user=_PROMPT,
            image_url=url,
            max_tokens=600,
        )
        if resp and resp.content.strip():
            descriptions.append(resp.content.strip())
            logger.debug(
                f"[MediaDescriber] 图片 {i} 描述完成 "
                f"(in={resp.input_tokens} out={resp.output_tokens})"
            )
        else:
            logger.warning(f"[MediaDescriber] 图片 {i} 描述失败: {url[:80]}")

    if not descriptions:
        return None

    if len(descriptions) == 1:
        return descriptions[0]

    return "\n\n".join(f"[图{i}] {desc}" for i, desc in enumerate(descriptions, 1))
