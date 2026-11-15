from __future__ import annotations

import logging
import sys

import uvicorn

from agente_salas.a2a import criar_app
from agente_salas.config import Config, carregar_config
from agente_salas.mcp_client import McpClient
from agente_salas.ponte import BridgeService
from agente_salas.tasks import TaskStore


def montar(cfg: Config):
    store = TaskStore()
    return criar_app(BridgeService(McpClient(cfg.mcp_url), store), store, cfg.public_url)


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    cfg = carregar_config()
    uvicorn.run(montar(cfg), host="127.0.0.1", port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
