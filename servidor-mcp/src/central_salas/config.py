from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MIN_SECRET_BYTES = 32


class ConfigInvalida(Exception):
    pass


@dataclass(frozen=True)
class Config:
    port: int
    secret: str
    dados_dir: Path


def carregar_config(env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ) if env is None else env
    secret = env.get("REQUEST_STATE_SECRET", "")
    if len(secret.encode()) < MIN_SECRET_BYTES:
        raise ConfigInvalida(
            "REQUEST_STATE_SECRET ausente ou com menos de 32 bytes. "
            'Gere com: python3 -c "import secrets; print(secrets.token_hex(32))"'
        )
    padrao = Path(__file__).resolve().parents[3] / "dados"
    return Config(
        port=int(env.get("MCP_PORT", "7301")),
        secret=secret,
        dados_dir=Path(env.get("DADOS_DIR", str(padrao))),
    )
