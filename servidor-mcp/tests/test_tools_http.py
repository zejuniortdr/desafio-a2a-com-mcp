import json

from conftest import call, rpc

H = lambda hora: f"2026-11-03T{hora}:00-03:00"  # noqa: E731


def texto(res):
    return " ".join(p["text"] for p in res["content"])


def test_tools_list(client):
    _, b = rpc(client, "tools/list", {})
    tools = {t["name"]: t for t in b["result"]["tools"]}
    assert set(tools) == {"listar_salas", "consultar_disponibilidade", "reservar_sala"}
    assert all(t["inputSchema"]["type"] == "object" for t in tools.values())
    assert "outputSchema" in tools["listar_salas"]


def test_listar_salas_structured_igual_texto(client):
    _, b = call(client, "listar_salas", {})
    r = b["result"]
    assert len(r["structuredContent"]["salas"]) == 5
    assert json.loads(texto(r)) == r["structuredContent"]


def test_resources_list(client):
    _, b = rpc(client, "resources/list", {})
    assert b["result"]["resources"][0]["uri"] == "politica://uso"


def test_sem_meta_obrigatorio(client):
    for campo in ("protocolVersion", "clientCapabilities"):
        status, b = call(client, "listar_salas", {}, omitir=campo)
        assert status == 400 and b["error"]["code"] == -32602


def test_tool_inexistente_recusada(client):
    _, b = call(client, "voar_delorean", {})
    assert b.get("error", {}).get("code") == -32602 or b["result"]["isError"] is True


def test_resource_politica_e_inexistente(client):
    _, b = rpc(client, "resources/read", {"uri": "politica://uso"}, "politica://uso")
    c = b["result"]["contents"][0]
    assert "versao: 2026-11-01" in c["text"] and c["mimeType"] == "text/markdown"
    _, b = rpc(client, "resources/read", {"uri": "politica://nada"}, "politica://nada")
    assert b["error"]["code"] == -32602


import pytest  # noqa: E402


@pytest.mark.parametrize(
    "args,msg",
    [
        ({"sala": "sala-x", "inicio": H("09:00"), "fim": H("10:00")}, "Sala inexistente: sala-x"),
        ({"sala": "sala-aquario", "inicio": H("07:00"), "fim": H("08:00")}, "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"),
        ({"sala": "sala-aquario", "inicio": H("09:00"), "fim": H("12:00")}, "Duracao acima do limite: a politica permite no maximo 2 horas"),
        ({"sala": "sala-aquario", "inicio": H("10:00"), "fim": H("09:00")}, "Intervalo invalido: fim deve ser posterior a inicio"),
    ],
)
@pytest.mark.parametrize("tool", ["consultar_disponibilidade", "reservar_sala"])
def test_erros_de_execucao(client, tool, args, msg):
    args = {**args, "responsavel": "Doc"} if tool == "reservar_sala" else args
    _, b = call(client, tool, args)
    assert b["result"]["isError"] is True and msg in texto(b["result"])


def test_consultar_conflito(client):
    _, b = call(client, "consultar_disponibilidade", {"sala": "sala-garagem", "inicio": H("14:00"), "fim": H("15:00")})
    s = b["result"]["structuredContent"]
    assert s["livre"] is False and s["conflitos"][0]["id"] == "res-0001"


def test_reserva_livre(client):
    _, b = call(client, "reservar_sala", {"sala": "sala-porao", "inicio": H("09:00"), "fim": H("10:00"), "responsavel": "Doc"})
    r = b["result"]
    assert r["resultType"] == "complete" and not r.get("isError")
    s = r["structuredContent"]
    assert s["reservado"] is True and s["politica"] == "2026-11-01" and s["reserva"].startswith("res-")
    _, b = call(client, "consultar_disponibilidade", {"sala": "sala-porao", "inicio": H("09:00"), "fim": H("10:00")})
    assert b["result"]["structuredContent"]["livre"] is False
