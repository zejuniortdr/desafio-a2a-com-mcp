"""Estresse de reservas: o SDK roda tools sincronas em thread pool, entao a checagem de conflito e a escrita
precisam ser atomicas tambem pela borda HTTP, nao so no repositorio."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import call
from test_mrtr import ACEITA_FUSCA, GARAGEM, H, disponibilidade, pausar, retomar

N = 24


def em_paralelo(funcoes):
    """Dispara todas as funcoes ao mesmo tempo (barreira) e devolve os resultados na mesma ordem."""
    barreira = threading.Barrier(len(funcoes))

    def rodar(f):
        barreira.wait(timeout=10)
        return f()

    with ThreadPoolExecutor(max_workers=len(funcoes)) as pool:
        return [fut.result(timeout=60) for fut in [pool.submit(rodar, f) for f in funcoes]]


@pytest.mark.parametrize("rodada", range(3))
def test_n_reservas_simultaneas_do_mesmo_intervalo_criam_exatamente_uma(client, rodada):
    # Arrange: sala-porao livre; cada cliente quer o mesmo horario
    base = {"sala": "sala-porao", "inicio": H("09:00"), "fim": H("10:00")}
    chamadas = [
        lambda i=i: call(client, "reservar_sala", {**base, "responsavel": f"cli-{i}"}, id_=str(i)) for i in range(N)
    ]

    # Act
    respostas = em_paralelo(chamadas)

    # Assert
    resultados = [b["result"] for _, b in respostas]
    criadas = [r for r in resultados if r.get("structuredContent", {}).get("reservado") is True]
    pausadas = [r for r in resultados if r["resultType"] == "input_required"]
    assert len(criadas) == 1 and len(pausadas) == N - 1
    conflitos = disponibilidade(client, "sala-porao", "09:00", "10:00")["conflitos"]
    assert [c["id"] for c in conflitos] == [criadas[0]["structuredContent"]["reserva"]]


def test_ids_de_reserva_nunca_se_repetem_sob_concorrencia(client):
    # Arrange: N salas/horarios diferentes, todas livres
    horas = [
        (f"{8 + i // 2:02d}:{(i % 2) * 30:02d}", f"{8 + (i + 1) // 2:02d}:{((i + 1) % 2) * 30:02d}") for i in range(12)
    ]
    chamadas = [
        lambda ini=ini, fim=fim: call(
            client, "reservar_sala", {"sala": "sala-mirante", "inicio": H(ini), "fim": H(fim), "responsavel": "x"}
        )
        for ini, fim in horas
    ]

    # Act
    respostas = em_paralelo(chamadas)

    # Assert
    ids = [b["result"]["structuredContent"]["reserva"] for _, b in respostas]
    assert len(set(ids)) == len(ids) == 12


def test_duas_pausas_aceitando_a_mesma_alternativa_ao_mesmo_tempo_reservam_uma_so(client):
    # Arrange: dois clientes pausados no mesmo conflito, ambos escolhem sala-fusca
    a = pausar(client)
    b = pausar(client, {**GARAGEM, "responsavel": "Biff"})

    # Act
    respostas = em_paralelo(
        [
            lambda: retomar(client, a[0], a[1], ACEITA_FUSCA, id_="a"),
            lambda: retomar(client, b[0], b[1], ACEITA_FUSCA, args={**GARAGEM, "responsavel": "Biff"}, id_="b"),
        ]
    )

    # Assert
    resultados = [r["result"] for _, r in respostas]
    ok = [r for r in resultados if r.get("structuredContent", {}).get("reservado") is True]
    erro = [r for r in resultados if r.get("isError")]
    assert len(ok) == 1 and len(erro) == 1
    assert len(disponibilidade(client, "sala-fusca", "14:00", "15:00")["conflitos"]) == 1


def test_n_retries_com_o_mesmo_state_so_o_primeiro_a_chegar_reserva_cada_alternativa(client):
    # Arrange: um unico state reenviado em paralelo (replay); mirante e fusca livres
    chave, estado, _ = pausar(client)
    chamadas = [lambda i=i: retomar(client, chave, estado, ACEITA_FUSCA, id_=str(i)) for i in range(N)]

    # Act
    respostas = em_paralelo(chamadas)

    # Assert: sem estado no servidor nao ha como impedir o replay, mas a reserva continua atomica:
    # a sala escolhida acaba com exatamente uma reserva e as demais tentativas viram erro de execucao
    resultados = [b["result"] for _, b in respostas]
    assert sum(1 for r in resultados if r.get("structuredContent", {}).get("reservado") is True) == 1
    assert sum(1 for r in resultados if r.get("isError")) == N - 1
    assert len(disponibilidade(client, "sala-fusca", "14:00", "15:00")["conflitos"]) == 1
