from __future__ import annotations

import logging
import sys
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer, RequestStateSecurity
from starlette.applications import Starlette

from central_salas.config import Config, carregar_config
from central_salas.dominio import ReservaRepo, carregar_reservas, carregar_salas, versao_politica
from central_salas.tools import registrar_tools

TTL_REQUEST_STATE_S = 600.0  # 10 min, dentro da faixa 5-30 do enunciado
POLITICA_URI = "politica://uso"

log = logging.getLogger("central_salas")


async def _log_request(ctx: Any, call_next: Any) -> Any:
    meta = (ctx.params or {}).get("_meta") or {}
    log.info("method=%s id=%s traceparent=%s", ctx.method, ctx.request_id, meta.get("traceparent", "-"))
    return await call_next(ctx)


def criar_servidor(cfg: Config) -> MCPServer:
    salas = carregar_salas(cfg.dados_dir / "salas.json")
    repo = ReservaRepo(carregar_reservas(cfg.dados_dir / "reservas.json"))
    politica_md = (cfg.dados_dir / "politica-de-uso.md").read_text(encoding="utf-8")
    srv = MCPServer(
        "central-de-salas",
        request_state_security=RequestStateSecurity(keys=[cfg.secret], ttl=TTL_REQUEST_STATE_S),
        middleware=[_log_request],
    )
    registrar_tools(srv, salas, repo, versao_politica(cfg.dados_dir / "politica-de-uso.md"))

    @srv.resource(POLITICA_URI, name="politica-de-uso", mime_type="text/markdown")
    def politica() -> str:
        return politica_md

    return srv


def criar_app(cfg: Config) -> Starlette:
    return criar_servidor(cfg).streamable_http_app()


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(message)s")
    cfg = carregar_config()
    uvicorn.run(criar_app(cfg), host="127.0.0.1", port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
