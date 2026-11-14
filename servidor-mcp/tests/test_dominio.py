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


def intervalo(sala: str, ini: str, fim: str):
    _, i, f = validar(sala, d("03", ini), d("03", fim), SALAS)
    return i, f


@pytest.mark.parametrize(
    "sala,ini,fim,msg",
    [
        ("sala-x", d("03", "09:00"), d("03", "10:00"), "Sala inexistente: sala-x"),
        ("sala-aquario", d("03", "10:00"), d("03", "09:00"), "Intervalo invalido: fim deve ser posterior a inicio"),
        ("sala-aquario", d("03", "10:00"), d("03", "10:00"), "Intervalo invalido: fim deve ser posterior a inicio"),
        ("sala-aquario", d("03", "07:00"), d("03", "08:00"), "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"),
        ("sala-aquario", d("03", "19:30"), d("03", "20:30"), "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"),
        # regressao: segundos/microssegundos eram ignorados e 20:00:59 passava
        ("sala-aquario", d("03", "19:00"), "2026-11-03T20:00:59-03:00", "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"),
        ("sala-aquario", d("03", "19:00"), "2026-11-03T20:00:00.500000-03:00", "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"),
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
        ("2026-11-03T11:00:00Z", "2026-11-03T12:00:00Z"),  # sufixo Z: igual no 3.10 e no 3.11+
    ],
)
def test_validar_bordas_ok(ini, fim):
    sala, i, f = validar("sala-aquario", ini, fim, SALAS)

    assert sala.id == "sala-aquario" and isinstance(i, datetime) and f > i


def test_validar_20h01_fora():
    with pytest.raises(ErroDeNegocio):
        validar("sala-aquario", d("03", "19:00"), d("03", "20:01"), SALAS)


@pytest.mark.parametrize(
    "ini,fim,esperado",
    [
        ("14:00", "15:00", ["res-0001"]),
        ("15:00", "16:00", []),  # adjacente depois: livre
        ("13:00", "14:00", []),  # adjacente antes: livre
    ],
)
def test_conflitos_e_adjacencia(ini, fim, esperado):
    r = repo()
    i, f = intervalo("sala-garagem", ini, fim)

    achados = conflitos(r, "sala-garagem", i, f)

    assert [x.id for x in achados] == esperado


def test_add_gera_ids_sequenciais():
    r = repo()
    i, f = intervalo("sala-aquario", "09:00", "10:00")
    primeira = r.add("sala-aquario", i, f, "Doc")

    segunda = r.add("sala-porao", i, f, "Doc")

    assert (primeira.id, segunda.id) == ("res-0003", "res-0004")


def test_add_fica_visivel_em_conflitos():
    r = repo()
    i, f = intervalo("sala-aquario", "09:00", "10:00")

    r.add("sala-aquario", i, f, "Doc")

    assert [x.id for x in conflitos(r, "sala-aquario", i, f)] == ["res-0003"]


def test_repo_vazio_comeca_em_res_0001():
    i, f = intervalo("sala-aquario", "09:00", "10:00")

    reserva = ReservaRepo([]).add("sala-aquario", i, f, "Doc")

    assert reserva.id == "res-0001"


def test_reservar_se_livre_reserva_quando_livre():
    r = repo()
    i, f = intervalo("sala-aquario", "09:00", "10:00")

    reserva = r.reservar_se_livre("sala-aquario", i, f, "Doc")

    assert reserva is not None and reserva.id == "res-0003"


def test_reservar_se_livre_recusa_o_segundo():
    r = repo()
    i, f = intervalo("sala-aquario", "09:00", "10:00")
    r.reservar_se_livre("sala-aquario", i, f, "Doc")

    segunda = r.reservar_se_livre("sala-aquario", i, f, "Marty")

    assert segunda is None
    assert len(conflitos(r, "sala-aquario", i, f)) == 1


def test_reservar_se_livre_concorrente_gera_uma_reserva(monkeypatch):
    # Regressao do double-booking: as tools sincronas rodam em thread pool. O sleep alarga a janela
    # entre a checagem e a escrita; sem o lock em volta das duas, varias threads reservam.
    import time
    from concurrent.futures import ThreadPoolExecutor

    from central_salas import dominio

    original = dominio.conflitos

    def conflitos_lento(*a):
        achados = original(*a)
        time.sleep(0.01)
        return achados

    monkeypatch.setattr(dominio, "conflitos", conflitos_lento)
    r = repo()
    i, f = intervalo("sala-aquario", "09:00", "10:00")

    with ThreadPoolExecutor(8) as pool:
        feitas = list(pool.map(lambda n: r.reservar_se_livre("sala-aquario", i, f, f"p{n}"), range(8)))

    assert sum(x is not None for x in feitas) == 1


def test_alternativa_ocupada_no_intervalo_fica_de_fora():
    r = repo()
    i, f = intervalo("sala-garagem", "14:00", "15:00")
    r.add("sala-fusca", i, f, "Doc")

    alts = alternativas(r, SALAS, "sala-garagem", i, f)

    assert [s.id for s in alts] == ["sala-mirante"]


def test_alternativas_garagem_ordem_e_limite():
    r = repo()
    i, f = intervalo("sala-garagem", "14:00", "15:00")

    alts = alternativas(r, SALAS, "sala-garagem", i, f)

    assert [s.id for s in alts] == ["sala-fusca", "sala-mirante"]


def test_alternativas_limite_tres_e_desempate_por_id():
    r = repo()
    i, f = intervalo("sala-aquario", "09:00", "10:00")

    alts = alternativas(r, SALAS, "sala-aquario", i, f)

    assert [s.id for s in alts] == ["sala-porao", "sala-fusca", "sala-garagem"]  # cap 6, 12(fusca<garagem); mirante cortado


def test_alternativas_vazia_para_mirante():
    r = repo()
    i, f = intervalo("sala-mirante", "11:00", "12:00")

    alts = alternativas(r, SALAS, "sala-mirante", i, f)

    assert alts == []


def test_versao_politica():
    versao = versao_politica((DADOS / "politica-de-uso.md").read_text(encoding="utf-8"))

    assert versao == "2026-11-01"
