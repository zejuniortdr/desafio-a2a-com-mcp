from datetime import datetime
from pathlib import Path

import pytest

from central_salas.dominio import ReservaRepo, alternativas, carregar_reservas, carregar_salas, conflitos, validar, versao_politica
from central_salas.erros import ErroDeNegocio

DADOS = Path(__file__).resolve().parents[2] / "dados"
SALAS = carregar_salas(DADOS / "salas.json")


def d(dia: str, hora: str, tz: str = "-03:00") -> str:
    return f"2026-11-{dia}T{hora}:00{tz}"


def repo() -> ReservaRepo:
    return ReservaRepo(carregar_reservas(DADOS / "reservas.json"))


@pytest.mark.parametrize(
    "sala,ini,fim,msg",
    [
        ("sala-x", d("03", "09:00"), d("03", "10:00"), "Sala inexistente: sala-x"),
        ("sala-aquario", d("03", "10:00"), d("03", "09:00"), "Intervalo invalido: fim deve ser posterior a inicio"),
        ("sala-aquario", d("03", "10:00"), d("03", "10:00"), "Intervalo invalido: fim deve ser posterior a inicio"),
        ("sala-aquario", d("03", "07:00"), d("03", "08:00"), "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"),
        ("sala-aquario", d("03", "19:30"), d("03", "20:30"), "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"),
        ("sala-aquario", d("03", "09:00"), d("03", "12:00"), "Duracao acima do limite: a politica permite no maximo 2 horas"),
        # precedencia: sala > intervalo > janela > duracao
        ("sala-x", d("03", "12:00"), d("03", "06:00"), "Sala inexistente: sala-x"),
        ("sala-aquario", d("03", "22:00"), d("03", "06:00"), "Intervalo invalido: fim deve ser posterior a inicio"),
        ("sala-aquario", d("03", "06:00"), d("03", "12:00"), "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"),
        ("sala-aquario", "2026-11-03T05:00:00-03:00", "2026-11-03T06:00:00-03:00", "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"),
        ("sala-aquario", "nao-e-data", d("03", "10:00"), "Intervalo invalido: informe inicio e fim em ISO 8601 com fuso"),
        ("sala-aquario", "2026-11-03T09:00:00", d("03", "10:00"), "Intervalo invalido: informe inicio e fim em ISO 8601 com fuso"),
    ],
)
def test_validar_erros(sala, ini, fim, msg):
    with pytest.raises(ErroDeNegocio) as e:
        validar(sala, ini, fim, SALAS)
    assert str(e.value) == msg


@pytest.mark.parametrize(
    "ini,fim",
    [
        (d("03", "08:00"), d("03", "10:00")),  # borda inicial e duracao exata de 2h
        (d("03", "18:00"), d("03", "20:00")),  # borda final
        ("2026-11-03T11:00:00+00:00", "2026-11-03T12:00:00+00:00"),  # UTC 11h == 08h SP
    ],
)
def test_validar_bordas_ok(ini, fim):
    sala, i, f = validar("sala-aquario", ini, fim, SALAS)
    assert sala.id == "sala-aquario" and isinstance(i, datetime) and f > i


def test_validar_20h01_fora():
    with pytest.raises(ErroDeNegocio):
        validar("sala-aquario", d("03", "19:00"), d("03", "20:01"), SALAS)


def test_conflitos_e_adjacencia():
    r = repo()
    _, i, f = validar("sala-garagem", d("03", "14:00"), d("03", "15:00"), SALAS)
    assert [x.id for x in conflitos(r, "sala-garagem", i, f)] == ["res-0001"]
    _, i, f = validar("sala-garagem", d("03", "15:00"), d("03", "16:00"), SALAS)  # adjacente: livre
    assert conflitos(r, "sala-garagem", i, f) == []
    _, i, f = validar("sala-garagem", d("03", "13:00"), d("03", "14:00"), SALAS)
    assert conflitos(r, "sala-garagem", i, f) == []


def test_add_gera_ids_sequenciais_e_fica_visivel():
    r = repo()
    _, i, f = validar("sala-aquario", d("03", "09:00"), d("03", "10:00"), SALAS)
    a = r.add("sala-aquario", i, f, "Doc")
    assert a.id == "res-0003"
    assert r.add("sala-porao", i, f, "Doc").id == "res-0004"
    assert [x.id for x in conflitos(r, "sala-aquario", i, f)] == ["res-0003"]


def test_alternativas_garagem_ordem_e_limite():
    r = repo()
    _, i, f = validar("sala-garagem", d("03", "14:00"), d("03", "15:00"), SALAS)
    assert [s.id for s in alternativas(r, SALAS, "sala-garagem", i, f)] == ["sala-fusca", "sala-mirante"]


def test_alternativas_limite_tres_e_desempate_por_id():
    r = repo()
    _, i, f = validar("sala-aquario", d("03", "09:00"), d("03", "10:00"), SALAS)
    ids = [s.id for s in alternativas(r, SALAS, "sala-aquario", i, f)]
    assert ids == ["sala-porao", "sala-fusca", "sala-garagem"]  # cap 6, 12(fusca<garagem); mirante cortado


def test_alternativas_vazia_para_mirante():
    r = repo()
    _, i, f = validar("sala-mirante", d("03", "11:00"), d("03", "12:00"), SALAS)
    assert alternativas(r, SALAS, "sala-mirante", i, f) == []


def test_versao_politica():
    assert versao_politica(DADOS / "politica-de-uso.md") == "2026-11-01"
