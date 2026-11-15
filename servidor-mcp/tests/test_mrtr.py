import json
import os
import time
from types import SimpleNamespace

import pytest
from mcp.server import request_state
from starlette.testclient import TestClient

from central_salas.app import criar_app
from central_salas.config import ConfigInvalida, carregar_config
from conftest import SECRET, call, texto_de

H = lambda hora: f"2026-11-03T{hora}:00-03:00"  # noqa: E731
GARAGEM = {"sala": "sala-garagem", "inicio": H("14:00"), "fim": H("15:00"), "responsavel": "Marty"}
ACEITA_FUSCA = {"action": "accept", "content": {"sala": "sala-fusca"}}


def pausar(client, args=GARAGEM):
    _, b = call(client, "reservar_sala", args)
    r = b["result"]
    chave = next(iter(r["inputRequests"]))
    return chave, r["requestState"], r


def retomar(client, chave, estado, resposta, args=GARAGEM, id_="2"):
    return call(client, "reservar_sala", args, id_=id_, extra={"inputResponses": {chave: resposta}, "requestState": estado})


def disponibilidade(client, sala, ini, fim):
    _, b = call(client, "consultar_disponibilidade", {"sala": sala, "inicio": H(ini), "fim": H(fim)})
    return b["result"]["structuredContent"]


def adiantar_relogio(monkeypatch, segundos):
    agora = time.time()
    monkeypatch.setattr(request_state, "time", SimpleNamespace(time=lambda: agora + segundos))


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

    _, b = retomar(client, chave, estado, ACEITA_FUSCA)

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
    assert disponibilidade(client, "sala-fusca", "14:00", "15:00")["livre"] is True


def test_state_adulterado_32602(client):
    chave, estado, _ = pausar(client)
    ruim = estado[:-6] + ("AAAAAA" if not estado.endswith("AAAAAA") else "BBBBBB")

    _, b = retomar(client, chave, ruim, ACEITA_FUSCA)

    assert b["error"]["code"] == -32602


def test_args_adulterados_no_retry_nao_tem_efeito(client):
    chave, estado, _ = pausar(client)
    adulterados = {"sala": "sala-mirante", "inicio": H("13:00"), "fim": H("14:00"), "responsavel": "Biff"}

    _, b = retomar(client, chave, estado, ACEITA_FUSCA, args=adulterados)

    assert b["error"]["code"] == -32602  # o SDK amarra o state aos argumentos e rejeita a divergencia
    assert disponibilidade(client, "sala-mirante", "13:00", "14:00")["livre"] is True


def test_escolha_fora_do_enum_nao_reserva(client):
    chave, estado, _ = pausar(client)

    _, b = retomar(client, chave, estado, {"action": "accept", "content": {"sala": "sala-aquario"}})

    assert b["result"]["isError"] is True and "Sem alternativas disponiveis no intervalo" in texto_de(b["result"])
    assert disponibilidade(client, "sala-aquario", "14:00", "15:00")["livre"] is True


def test_alternativa_tomada_entre_a_pausa_e_o_retry_nao_reserva_de_novo(client):
    chave, estado, _ = pausar(client)
    call(client, "reservar_sala", {**GARAGEM, "sala": "sala-fusca", "responsavel": "Doc"})

    _, b = retomar(client, chave, estado, ACEITA_FUSCA)

    assert b["result"]["isError"] is True and "Sem alternativas disponiveis no intervalo" in texto_de(b["result"])
    conflitos = disponibilidade(client, "sala-fusca", "14:00", "15:00")["conflitos"]
    assert [r["responsavel"] for r in conflitos] == ["Doc"]


def test_retry_sem_a_chave_da_pausa_32602(client):
    _, estado, _ = pausar(client)

    status, b = retomar(client, "outra_chave", estado, ACEITA_FUSCA)

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


def test_state_vale_ate_10_minutos(client, monkeypatch):
    chave, estado, _ = pausar(client)
    adiantar_relogio(monkeypatch, 599)

    _, b = retomar(client, chave, estado, ACEITA_FUSCA)

    assert b["result"]["structuredContent"]["reservado"] is True


def test_state_expira_depois_de_10_minutos(client, monkeypatch):
    chave, estado, _ = pausar(client)
    adiantar_relogio(monkeypatch, 601)

    _, b = retomar(client, chave, estado, ACEITA_FUSCA)

    assert b["error"]["code"] == -32602


def test_state_de_outro_secret_e_rejeitado(client):
    chave, estado, _ = pausar(client)
    outro = carregar_config({"REQUEST_STATE_SECRET": "b" * 64})

    with TestClient(criar_app(outro), base_url="http://127.0.0.1:7301") as c:
        _, resp = retomar(c, chave, estado, ACEITA_FUSCA)

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


def test_secret_ausente_do_ambiente_e_config_invalida():
    with pytest.raises(ConfigInvalida):
        carregar_config({})


def test_config_sem_argumento_le_os_valores_do_os_environ(monkeypatch):
    monkeypatch.setenv("REQUEST_STATE_SECRET", SECRET)
    monkeypatch.setenv("MCP_PORT", "7777")

    cfg = carregar_config()

    assert (cfg.port, cfg.secret) == (7777, SECRET)


def test_mensagens_de_config_invalida_orientam_o_operador():
    with pytest.raises(ConfigInvalida) as segredo:
        carregar_config({})
    with pytest.raises(ConfigInvalida) as porta:
        carregar_config({"REQUEST_STATE_SECRET": SECRET, "MCP_PORT": "abc"})

    assert str(segredo.value) == (
        "REQUEST_STATE_SECRET ausente, fora do formato hex ou com menos de 32 bytes (64 caracteres hex). "
        'Gere com: python3 -c "import secrets; print(secrets.token_hex(32))"'
    )
    assert str(porta.value) == "MCP_PORT deve ser um numero inteiro"


def test_dados_dir_padrao_fica_tres_niveis_acima_do_modulo_config():
    from pathlib import Path

    from central_salas import config

    cfg = carregar_config({"REQUEST_STATE_SECRET": SECRET})

    assert cfg.dados_dir == Path(config.__file__).resolve().parents[3] / "dados"


@pytest.mark.skipif("MUTANT_UNDER_TEST" in os.environ, reason="o mutmut relocaliza src/ e quebra o layout do repo")
def test_dados_dir_padrao_e_a_pasta_dados_da_raiz_do_repo():
    cfg = carregar_config({"REQUEST_STATE_SECRET": SECRET})

    assert (cfg.dados_dir / "salas.json").is_file()


def test_secret_valido():
    cfg = carregar_config({"REQUEST_STATE_SECRET": SECRET})

    assert cfg.port == 7301


def test_config_le_porta_e_dados_e_nao_expoe_o_secret(tmp_path):
    env = {"REQUEST_STATE_SECRET": SECRET, "MCP_PORT": "7999", "DADOS_DIR": str(tmp_path)}

    cfg = carregar_config(env)

    assert (cfg.port, cfg.dados_dir) == (7999, tmp_path)
    assert SECRET not in repr(cfg)


def test_porta_invalida_na_config():
    with pytest.raises(ConfigInvalida):
        carregar_config({"REQUEST_STATE_SECRET": SECRET, "MCP_PORT": "abc"})


@pytest.mark.parametrize("conteudo", ["isto nao e json", '{"sala": "sala-garagem"}', "[1, 2]"])
def test_state_selado_com_conteudo_invalido_vira_32602(client, monkeypatch, conteudo):
    # Arrange: o SDK sela qualquer texto; o servidor precisa rejeitar conteudo que ele mesmo nao produziria.
    from central_salas import tools

    with monkeypatch.context() as m:
        m.setattr(tools, "json", SimpleNamespace(dumps=lambda *_a, **_k: conteudo, loads=json.loads))
        chave, estado, _ = pausar(client)

    # Act
    status, b = retomar(client, chave, estado, ACEITA_FUSCA)

    # Assert
    assert b["error"]["code"] == -32602 and b["error"]["message"] == "requestState invalido"
    assert disponibilidade(client, "sala-fusca", "14:00", "15:00")["livre"] is True


def test_sem_capability_32021_tem_mensagem_e_nao_depende_de_caps_nulas(client):
    # Arrange
    from central_salas.tools import _exigir_elicitation_form
    from mcp.shared.exceptions import MCPError

    # Act
    status, b = call(client, "reservar_sala", GARAGEM, caps={})
    with pytest.raises(MCPError) as nulo:
        _exigir_elicitation_form(SimpleNamespace(client_capabilities=None))

    # Assert
    assert b["error"]["message"] == "Client did not declare the form elicitation capability required by reservar_sala"
    assert nulo.value.code == -32021


def test_reservar_no_passado_e_permitido(client):
    # Arrange: o enunciado diz que nenhuma regra depende da data de hoje
    args = {"sala": "sala-porao", "inicio": "2001-01-02T10:00:00-03:00", "fim": "2001-01-02T11:00:00-03:00", "responsavel": "Doc"}

    # Act
    _, b = call(client, "reservar_sala", args)

    # Assert
    assert b["result"]["structuredContent"]["reservado"] is True


def test_sala_livre_nao_exige_capability_de_elicitation(client):
    # Arrange: so o caminho de conflito precisa de elicitation; sem conflito o -32021 nao se aplica
    args = {"sala": "sala-porao", "inicio": H("09:00"), "fim": H("10:00"), "responsavel": "Doc"}

    # Act
    status, b = call(client, "reservar_sala", args, caps={})

    # Assert
    assert status == 200 and b["result"]["structuredContent"]["reservado"] is True


def test_uma_unica_alternativa_continua_sendo_oferecida_e_aceita_no_retry(client):
    # Arrange: fusca conflita com res-0002 (16-17h); o mirante e tomado e sobra so a garagem
    fusca = {"sala": "sala-fusca", "inicio": H("16:00"), "fim": H("17:00"), "responsavel": "Doc"}
    call(client, "reservar_sala", {**fusca, "sala": "sala-mirante", "responsavel": "Biff"})
    chave, estado, r = pausar(client, fusca)

    # Act
    campo = r["inputRequests"][chave]["params"]["requestedSchema"]["properties"]["sala"]
    _, b = retomar(client, chave, estado, {"action": "accept", "content": {"sala": "sala-garagem"}}, args=fusca)

    # Assert
    assert campo.get("enum") == ["sala-garagem"] or campo.get("const") == "sala-garagem"
    assert b["result"]["structuredContent"]["sala"] == "sala-garagem"


def test_nenhum_segredo_hardcoded_no_codigo_do_servidor():
    import re
    from pathlib import Path

    # Arrange
    fontes = list((Path(__file__).resolve().parents[1] / "src").rglob("*.py"))

    # Act
    achados = [f.name for f in fontes if re.search(r"\b[0-9a-fA-F]{32,}\b", f.read_text(encoding="utf-8"))]

    # Assert
    assert fontes and achados == []
