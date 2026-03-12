"""
anchor.cli — 统一 CLI 入口
===========================
安装后：  anchor run-url <url>
开发模式：python -m anchor run-url <url>
"""
from __future__ import annotations

import click

from anchor import __version__


def _load_env():
    """Load .env before any business import."""
    import os
    from pathlib import Path

    from dotenv import load_dotenv

    # 优先加载项目根目录 .env
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./anchor.db")


@click.group()
@click.version_option(version=__version__, prog_name="anchor")
def main():
    """Anchor — 多模式信息提取与事实验证引擎"""
    _load_env()


@main.command("run-url")
@click.argument("target")
@click.option("--force", is_flag=True, help="强制重新抓取并覆盖已有记录")
def run_url(target: str, force: bool):
    """分析单条 URL 或本地文件/目录"""
    from anchor.commands.run_url import run_url_command

    run_url_command(target, force=force)


@main.command()
@click.option("--dry-run", is_flag=True, help="仅预览新 URL，不执行分析")
@click.option("--source", default=None, metavar="NAME", help="仅处理名称含该字符串的来源")
@click.option("--limit", default=0, type=int, metavar="N", help="每个来源最多处理条数（0=不限）")
@click.option("--concurrency", default=5, type=int, metavar="N", help="并行提取 worker 数量")
@click.option("--since", default=None, metavar="YYYY-MM-DD", help="只抓此日期之后的文章")
@click.option("--force", "-f", is_flag=True, help="强制重新处理所有 URL")
def monitor(dry_run: bool, source: str | None, limit: int, concurrency: int, since: str | None, force: bool):
    """从 watchlist.yaml 批量拉取新文章并分析"""
    from anchor.commands.monitor import monitor_command

    monitor_command(
        dry_run=dry_run,
        source=source,
        limit=limit,
        concurrency=concurrency,
        since=since,
        force=force,
    )


@main.command()
@click.option("--host", default="0.0.0.0", help="绑定地址")
@click.option("--port", default=8765, type=int, help="监听端口")
def serve(host: str, port: int):
    """启动 Web UI 服务"""
    from anchor.commands.serve import serve_command

    serve_command(host=host, port=port)
