from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    port: int
    mcp_url: str
    public_url: str


def carregar_config(env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ) if env is None else env
    port = int(env.get("A2A_PORT", "7300"))
    return Config(
        port=port,
        mcp_url=env.get("MCP_URL", "http://127.0.0.1:7301/mcp"),
        public_url=env.get("PUBLIC_URL", f"http://127.0.0.1:{port}").rstrip("/"),
    )
