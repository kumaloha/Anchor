"""
anchor.commands.serve — Web UI 启动
"""
from __future__ import annotations


def serve_command(host: str = "0.0.0.0", port: int = 8765) -> None:
    """CLI 入口，由 anchor.cli 调用。"""
    import uvicorn

    uvicorn.run(
        "anchor.web.app:app",
        host=host,
        port=port,
        reload=False,
    )
