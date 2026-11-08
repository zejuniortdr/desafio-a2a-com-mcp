import pytest

from agente_salas.pedido import Escolha, PedidoInvalido, PedidoReserva, parse_pedido


def test_reserva():
    p = parse_pedido("reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty McFly")
    assert p == PedidoReserva("sala-garagem", "2026-11-03T14:00:00-03:00", "2026-11-03T15:00:00-03:00", "Marty McFly")


@pytest.mark.parametrize("t,v", [("escolha=sala-fusca", "sala-fusca"), ("  escolha=recusar \n", "recusar")])
def test_escolha(t, v):
    assert parse_pedido(t) == Escolha(v)


@pytest.mark.parametrize("t", ["", "oi", "reservar sala=a", "escolha=", "escolha=a b", None, "reservar sala=a inicio=b fim=c"])
def test_invalidos(t):
    with pytest.raises(PedidoInvalido):
        parse_pedido(t)
