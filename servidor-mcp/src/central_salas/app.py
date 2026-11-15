from __future__ import annotations

import json
import logging
import sys

import uvicorn
from mcp.server.mcpserver import MCPServer, RequestStateSecurity
from starlette.applications import Starlette
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from central_salas.config import Config, carregar_config
from central_salas.dominio import ReservaRepo, carregar_reservas, carregar_salas, versao_politica
from central_salas.tools import registrar_tools

TTL_REQUEST_STATE_S = 600.0  # 10 min, dentro da faixa 5-30 do enunciado
POLITICA_URI = "politica://uso"

log = logging.getLogger("central_salas")


def _log_corpo(corpo: bytes) -> None:
    try:
        msg = json.loads(corpo)
    except ValueError:
        msg = None
    if not isinstance(msg, dict):
        msg = {}
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    # traceparent vem do cliente: so [alnum-] e no maximo 55 caracteres (tamanho do formato W3C) vao ao log.
    traceparent = "".join(c for c in str(meta.get("traceparent", "-"))[:55] if c.isalnum() or c == "-")
    # id e method tambem vem do cliente: repr escapa \n e o corte em 64 limita o tamanho.
    log.info("method=%r id=%r traceparent=%s", str(msg.get("method", "-"))[:64], str(msg.get("id", "-"))[:64], traceparent)


class _LogMiddleware:
    """Loga cada POST antes do SDK, inclusive os que ele rejeita na validacao do envelope _meta."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        lidas: list[Message] = []
        while True:
            m = await receive()
            lidas.append(m)
            if m["type"] != "http.request" or not m.get("more_body"):
                break
        _log_corpo(b"".join(m.get("body", b"") for m in lidas))

        async def reenviar() -> Message:
            return lidas.pop(0) if lidas else await receive()

        await self.app(scope, reenviar, send)


def criar_servidor(cfg: Config) -> MCPServer:
    salas = carregar_salas(cfg.dados_dir / "salas.json")
    repo = ReservaRepo(carregar_reservas(cfg.dados_dir / "reservas.json"))
    politica_md = (cfg.dados_dir / "politica-de-uso.md").read_text(encoding="utf-8")
    srv = MCPServer(
        "central-de-salas",
        request_state_security=RequestStateSecurity(keys=[cfg.secret], ttl=TTL_REQUEST_STATE_S),
    )
    registrar_tools(srv, salas, repo, versao_politica(politica_md))

    @srv.resource(POLITICA_URI, name="politica-de-uso", mime_type="text/markdown")
    def politica() -> str:
        return politica_md

    return srv


def criar_app(cfg: Config) -> Starlette:
    app = criar_servidor(cfg).streamable_http_app()
    app.add_middleware(_LogMiddleware)
    return app


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(message)s")
    cfg = carregar_config()
    uvicorn.run(criar_app(cfg), host="127.0.0.1", port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
