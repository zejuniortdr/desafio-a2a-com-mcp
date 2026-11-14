import pytest
from starlette.testclient import TestClient

from central_salas.app import criar_app
from central_salas.config import ConfigInvalida, carregar_config
from conftest import SECRET, call, texto_de

H = lambda hora: f"2026-11-03T{hora}:00-03:00"  # noqa: E731
GARAGEM = {"sala": "sala-garagem", "inicio": H("14:00"), "fim": H("15:00"), "responsavel": "Marty"}


def pausar(client, args=GARAGEM):
    _, b = call(client, "reservar_sala", args)
    r = b["result"]
    chave = next(iter(r["inputRequests"]))
    return chave, r["requestState"], r


def retomar(client, chave, estado, resposta, args=GARAGEM, id_="2"):
    return call(client, "reservar_sala", args, id_=id_, extra={"inputResponses": {chave: resposta}, "requestState": estado})


def test_conflito_pede_input(client):
    chave, estado, r = pausar(client)
    assert r["resultType"] == "input_required" and estado and len(r["inputRequests"]) == 1
    p = r["inputRequests"][chave]
    assert p["method"] == "elicitation/create" and p["params"]["mode"] == "form"
    assert p["params"]["requestedSchema"]["properties"]["sala"]["enum"] == ["sala-fusca", "sala-mirante"]


def test_sem_capability_32021(client):
    status, b = call(client, "reservar_sala", GARAGEM, caps={})
    assert status == 400 and b["error"]["code"] == -32021
    assert b["error"]["data"]["requiredCapabilities"] == {"elicitation": {"form": {}}}


def test_retry_accept_conclui(client):
    chave, estado, _ = pausar(client)
    _, b = retomar(client, chave, estado, {"action": "accept", "content": {"sala": "sala-fusca"}})
    r = b["result"]
    assert r["resultType"] == "complete" and not r.get("isError")
    assert r["structuredContent"]["sala"] == "sala-fusca" and r["structuredContent"]["reservado"] is True


@pytest.mark.parametrize("acao", ["decline", "cancel"])
def test_recusa_conclui_sem_reservar(client, acao):
    chave, estado, _ = pausar(client)
    _, b = retomar(client, chave, estado, {"action": acao})
    r = b["result"]
    assert r["resultType"] == "complete" and not r.get("isError")
    assert r["structuredContent"]["reservado"] is False and r["structuredContent"]["motivo"]
    _, b = call(client, "consultar_disponibilidade", {"sala": "sala-fusca", "inicio": H("14:00"), "fim": H("15:00")})
    assert b["result"]["structuredContent"]["livre"] is True


def test_state_adulterado_32602(client):
    chave, estado, _ = pausar(client)
    ruim = estado[:-6] + ("AAAAAA" if not estado.endswith("AAAAAA") else "BBBBBB")
    _, b = retomar(client, chave, ruim, {"action": "accept", "content": {"sala": "sala-fusca"}})
    assert b["error"]["code"] == -32602


def test_args_adulterados_no_retry_nao_tem_efeito(client):
    chave, estado, _ = pausar(client)
    adulterados = {"sala": "sala-mirante", "inicio": H("13:00"), "fim": H("14:00"), "responsavel": "Biff"}
    _, b = retomar(client, chave, estado, {"action": "accept", "content": {"sala": "sala-fusca"}}, args=adulterados)
    assert b["error"]["code"] == -32602  # o SDK amarra o state aos argumentos e rejeita a divergencia
    _, c = call(client, "consultar_disponibilidade", {"sala": "sala-mirante", "inicio": H("13:00"), "fim": H("14:00")})
    assert c["result"]["structuredContent"]["livre"] is True


def test_escolha_fora_do_enum_nao_reserva(client):
    chave, estado, _ = pausar(client)
    _, b = retomar(client, chave, estado, {"action": "accept", "content": {"sala": "sala-aquario"}})
    assert b["result"]["isError"] is True


def test_alternativa_tomada_entre_a_pausa_e_o_retry_nao_reserva_de_novo(client):
    chave, estado, _ = pausar(client)
    call(client, "reservar_sala", {**GARAGEM, "sala": "sala-fusca", "responsavel": "Doc"})
    _, b = retomar(client, chave, estado, {"action": "accept", "content": {"sala": "sala-fusca"}})
    assert b["result"]["isError"] is True and "Sem alternativas disponiveis no intervalo" in texto_de(b["result"])
    _, c = call(client, "consultar_disponibilidade", {"sala": "sala-fusca", "inicio": H("14:00"), "fim": H("15:00")})
    assert [r["responsavel"] for r in c["result"]["structuredContent"]["conflitos"]] == ["Doc"]


def test_retry_sem_a_chave_da_pausa_32602(client):
    _, estado, _ = pausar(client)
    status, b = retomar(client, "outra_chave", estado, {"action": "accept", "content": {"sala": "sala-fusca"}})
    assert status == 400 and b["error"]["code"] == -32602


def test_elicitation_vazia_equivale_a_form_mode(client):
    _, b = call(client, "reservar_sala", GARAGEM, caps={"elicitation": {}})
    assert b["result"]["resultType"] == "input_required"


def test_elicitation_so_url_nao_serve_32021(client):
    status, b = call(client, "reservar_sala", GARAGEM, caps={"elicitation": {"url": {}}})
    assert status == 400 and b["error"]["code"] == -32021


def test_retry_sobrevive_a_restart(cfg):
    with TestClient(criar_app(cfg), base_url="http://127.0.0.1:7301") as a:
        chave, estado, _ = pausar(a)
    with TestClient(criar_app(cfg), base_url="http://127.0.0.1:7301") as b:
        _, resp = retomar(b, chave, estado, {"action": "accept", "content": {"sala": "sala-mirante"}})
    assert resp["result"]["structuredContent"]["sala"] == "sala-mirante"


@pytest.mark.parametrize("segundos, expira", [(599, False), (601, True)])
def test_state_expira_em_10_minutos(client, monkeypatch, segundos, expira):
    import time
    from types import SimpleNamespace

    from mcp.server import request_state

    chave, estado, _ = pausar(client)
    agora = time.time()
    monkeypatch.setattr(request_state, "time", SimpleNamespace(time=lambda: agora + segundos))
    _, b = retomar(client, chave, estado, {"action": "accept", "content": {"sala": "sala-fusca"}})
    if expira:
        assert b["error"]["code"] == -32602
    else:
        assert b["result"]["structuredContent"]["reservado"] is True


def test_state_de_outro_secret_e_rejeitado(client):
    chave, estado, _ = pausar(client)
    outro = carregar_config({"REQUEST_STATE_SECRET": "b" * 64})
    with TestClient(criar_app(outro), base_url="http://127.0.0.1:7301") as c:
        _, resp = retomar(c, chave, estado, {"action": "accept", "content": {"sala": "sala-fusca"}})
    assert resp["error"]["code"] == -32602


def test_sem_alternativa_e_erro_de_execucao(client):
    mirante = {"sala": "sala-mirante", "inicio": H("11:00"), "fim": H("12:00"), "responsavel": "A"}
    call(client, "reservar_sala", mirante)
    _, b = call(client, "reservar_sala", mirante)
    assert b["result"]["isError"] is True and "Sem alternativas disponiveis no intervalo" in texto_de(b["result"])


@pytest.mark.parametrize("secret", ["", "curto", "x" * 31, "a" * 32, "z" * 64])
def test_secret_invalido_na_config(secret):
    with pytest.raises(ConfigInvalida):
        carregar_config({"REQUEST_STATE_SECRET": secret})


def test_secret_valido():
    assert carregar_config({"REQUEST_STATE_SECRET": SECRET}).port == 7301


def test_config_le_porta_e_dados_e_nao_expoe_o_secret(tmp_path):
    cfg = carregar_config({"REQUEST_STATE_SECRET": SECRET, "MCP_PORT": "7999", "DADOS_DIR": str(tmp_path)})
    assert (cfg.port, cfg.dados_dir) == (7999, tmp_path)
    assert SECRET not in repr(cfg)


def test_porta_invalida_na_config():
    with pytest.raises(ConfigInvalida):
        carregar_config({"REQUEST_STATE_SECRET": SECRET, "MCP_PORT": "abc"})
