# Atalhos para o avaliador. Uso: make install; make servidor (terminal 1); make agente (terminal 2); make validar (terminal 3)
SERVIDOR_PY := servidor-mcp/.venv/bin
AGENTE_PY   := agente/.venv/bin

.PHONY: install servidor agente validar test clean

# Se REQUEST_STATE_SECRET nao estiver exportado, gera um novo a cada `make servidor` (so o servidor recebe o segredo).
# Para um retry sobreviver a restart, exporte o mesmo valor antes: export REQUEST_STATE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
servidor: export REQUEST_STATE_SECRET ?= $(shell python3 -c "import secrets; print(secrets.token_hex(32))")

install:
	python3 -m venv servidor-mcp/.venv && $(SERVIDOR_PY)/pip install -q -e 'servidor-mcp[dev]'
	python3 -m venv agente/.venv && $(AGENTE_PY)/pip install -q -e 'agente[dev]'

servidor:
	$(SERVIDOR_PY)/central-salas

agente:
	$(AGENTE_PY)/agente-salas

validar:
	python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301

test:
	cd servidor-mcp && .venv/bin/pytest -q
	cd agente && .venv/bin/pytest -q

clean:
	rm -rf servidor-mcp/.venv agente/.venv
