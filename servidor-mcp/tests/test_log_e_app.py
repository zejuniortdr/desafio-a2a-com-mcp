import asyncio
import logging
from types import SimpleNamespace

import pytest

from central_salas.app import _log_request

TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"  # 55 caracteres


def _logar(caplog, method="tools/call", id_="7", params=None):
    ctx = SimpleNamespace(method=method, request_id=id_, params=params)

    async def proximo(c):
        return "ok"

    caplog.set_level(logging.INFO, logger="central_salas")
    assert asyncio.run(_log_request(ctx, proximo)) == "ok"
    return [r.getMessage() for r in caplog.records if r.name == "central_salas"]


def test_log_traz_metodo_id_e_traceparent(caplog):
    # Arrange
    params = {"_meta": {"traceparent": TP}}

    # Act
    linhas = _logar(caplog, params=params)

    # Assert
    assert linhas == [f"method='tools/call' id='7' traceparent={TP}"]


@pytest.mark.parametrize("params", [None, {}, {"_meta": {}}])
def test_log_sem_traceparent_registra_hifen(caplog, params):
    # Arrange / Act
    linhas = _logar(caplog, params=params)

    # Assert
    assert linhas == ["method='tools/call' id='7' traceparent=-"]


def test_log_traceparent_filtra_caracteres_e_corta_em_55(caplog):
    # Arrange
    sujo = "00-ab\ncd EF!" + "z" * 100

    # Act
    linhas = _logar(caplog, params={"_meta": {"traceparent": sujo}})

    # Assert
    # corta nos 55 primeiros caracteres crus (12 sujos + 43 z), depois filtra os 3 invalidos
    esperado = "00-abcdEF" + "z" * 43
    assert linhas == [f"method='tools/call' id='7' traceparent={esperado}"]


def test_log_corta_method_e_id_em_64(caplog):
    # Arrange / Act
    linhas = _logar(caplog, method="m" * 100, id_="i" * 100)

    # Assert
    assert linhas == [f"method={'m' * 64!r} id={'i' * 64!r} traceparent=-"]


def test_log_id_numerico_vira_texto(caplog):
    # Arrange / Act
    linhas = _logar(caplog, id_=42)

    # Assert
    assert linhas == ["method='tools/call' id='42' traceparent=-"]


def test_server_discover_declara_nome_e_capabilities_de_tools_e_resources(client):
    from conftest import rpc

    # Arrange / Act
    _, b = rpc(client, "server/discover", {})

    # Assert
    r = b["result"]
    assert r["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "central-de-salas"
    assert {"tools", "resources"} <= set(r["capabilities"])


def test_ttl_do_request_state_e_passado_explicitamente_ao_sdk(cfg, monkeypatch):
    from central_salas import app

    # Arrange: o default do SDK (600 s) coincide com o nosso; o enunciado exige 5-30 min, entao nao dependemos dele.
    vistos = []
    real = app.RequestStateSecurity
    monkeypatch.setattr(app, "RequestStateSecurity", lambda **kw: vistos.append(kw) or real(**kw))

    # Act
    app.criar_servidor(cfg)

    # Assert
    assert vistos[0]["ttl"] == app.TTL_REQUEST_STATE_S == 600.0
    assert 300 <= vistos[0]["ttl"] <= 1800


def test_main_configura_log_no_stderr_e_sobe_uvicorn_em_loopback_na_porta_da_config(monkeypatch):
    import sys

    from central_salas import app

    # Arrange
    monkeypatch.setenv("REQUEST_STATE_SECRET", "a" * 64)
    monkeypatch.setenv("MCP_PORT", "7777")
    log_calls, run_args, run_kw = [], [], {}
    monkeypatch.setattr(app.logging, "basicConfig", lambda **kw: log_calls.append(kw))
    monkeypatch.setattr(app.uvicorn, "run", lambda *a, **kw: (run_args.extend(a), run_kw.update(kw)))

    # Act
    app.main()

    # Assert
    assert {"stream": sys.stderr, "level": logging.INFO, "format": "%(asctime)s %(message)s"} in log_calls
    assert run_kw == {"host": "127.0.0.1", "port": 7777, "log_level": "warning"}
    assert len(run_args) == 1 and run_args[0].routes
