[![testes](https://github.com/zejuniortdr/desafio-a2a-com-mcp/actions/workflows/testes.yml/badge.svg)](https://github.com/zejuniortdr/desafio-a2a-com-mcp/actions/workflows/testes.yml)

# A Ponte: agente A2A com MCP por dentro

Hill Valley Tech: cinco salas de reunião expostas como **servidor MCP** (`servidor-mcp/`, porta 7301) e um
**agente A2A** (`agente/`, porta 7300) que consome esse servidor como host MCP e se oferece ao mundo como servidor A2A.
Sem LLM no caminho de execução: o mesmo pedido produz sempre o mesmo resultado.

## Como rodar

Requisitos: Python >= 3.10 e `make`. A partir de um clone limpo, na raiz do repositório:

```bash
make install    # um venv por processo (servidor-mcp/.venv e agente/.venv), dependências diretas travadas com == nos pyproject.toml

# terminal 1: servidor MCP na 7301 (o stderr mostra método, id e traceparent de cada request)
export REQUEST_STATE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
make servidor   # para reiniciar o servidor, rode de novo só esta linha, no mesmo terminal (mesmo segredo)

make agente     # terminal 2: agente A2A na 7300
make validar    # terminal 3: validador do starter contra os dois processos
```

| Alvo | O que faz |
|------|-----------|
| `make install` | cria os dois venvs e instala os pacotes (com extras de teste) |
| `make servidor` | sobe o servidor MCP (comando `central-salas`; no protocolo ele se identifica como `central-de-salas`) |
| `make agente` | sobe o agente A2A (comando `agente-salas`) |
| `make validar` | roda `python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301` |
| `make test` | roda o pytest dos dois pacotes (servidor-mcp e agente) |
| `make clean` | remove os dois venvs |

**Segredo do `requestState`:** o servidor MCP recusa subir se `REQUEST_STATE_SECRET` estiver vazio, não for hexadecimal ou
tiver menos de 32 bytes depois de decodificado (64 caracteres hex). Gere sempre com o comando acima (`token_hex(32)`: 32 bytes
aleatórios) e nunca versione o valor. Só o alvo `servidor` recebe a variável. Sem o `export`, `make servidor`
gera um segredo novo a cada subida: o validador passa, mas um `requestState` emitido antes de um restart deixa de valer. Por isso o
`export` vem antes, e o restart do passo 12 do fluxo do avaliador precisa acontecer no mesmo terminal.

<details>
<summary>Comandos equivalentes sem <code>make</code></summary>

```bash
python3 -m venv servidor-mcp/.venv && servidor-mcp/.venv/bin/pip install -e servidor-mcp
python3 -m venv agente/.venv       && agente/.venv/bin/pip install -e agente
export REQUEST_STATE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
servidor-mcp/.venv/bin/central-salas      # terminal 1
agente/.venv/bin/agente-salas             # terminal 2
python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301   # terminal 3
```

</details>

Variáveis opcionais, com os valores padrão do enunciado: `MCP_PORT=7301`, `A2A_PORT=7300`, `MCP_URL=http://127.0.0.1:7301/mcp`,
`PUBLIC_URL=http://127.0.0.1:7300`. Há ainda `DADOS_DIR`, cujo padrão é a pasta `dados/` na raiz do repositório.

## Onde a ponte acontece

Tudo vive em [`agente/src/agente_salas/ponte.py`](agente/src/agente_salas/ponte.py) (`BridgeService`), o único módulo que conhece os dois protocolos.
Na **ida**, `_aplicar` recebe do `McpClient` (cliente httpx cru, sem callback de elicitation, então o `input_required` chega intacto) o resultado
`input_required` e o transforma em `TASK_STATE_INPUT_REQUIRED`: guarda o `requestState`, a chave de `inputRequests` e o `enum` na própria `Task`
e devolve ao cliente A2A, como mensagem da Task, somente a linha `alternativas: a, b`. Na **volta**, `continuar` recebe `escolha=<id>` num
`SendMessage` com `taskId`, confere a escolha contra o `enum` guardado (fora dele a Task segue pausada e repete a lista) e repete o `tools/call`
com **id JSON-RPC novo**, `inputResponses` sob a mesma chave e o `requestState` ecoado sem abrir. O DTO de saída (`task_dto` em `a2a.py`) nunca
serializa o estado da pausa.

## Decisões técnicas

- **Proteção do `requestState`:** `RequestStateSecurity` do SDK `mcp` 2.3.0 (AES-256-GCM, chave derivada por HKDF de `REQUEST_STATE_SECRET`;
  cifrado, não só assinado). O SDK também amarra o state ao request: adulterar `arguments` no retry é rejeitado com `-32602`.
  O pedido original (sala, início, fim, responsável) viaja dentro do state selado; o servidor não guarda nada entre o `input_required` e o retry,
  então um retry após restart do processo funciona (desde que a chave seja a mesma).
- **Validade:** 600 s (10 min), dentro da faixa de 5 a 30 min (`TTL_REQUEST_STATE_S` em `servidor-mcp/src/central_salas/app.py`).
  Expirado ou adulterado: `-32602` (coberto em `servidor-mcp/tests/test_mrtr.py`: 599 s conclui, 601 s rejeita).
- **Capability de elicitation:** em conflito com alternativas, se o `_meta` do request não declara elicitation em form mode, o servidor responde
  `-32021` com `data.requiredCapabilities` em vez de devolver o `input_required`.
- **MRTR manual em vez de `Resolve/Elicit`:** a ferramenta devolve `InputRequiredResult` diretamente. O enunciado exige que o `requestState`
  carregue tudo de que o servidor precisa para reconstruir o pedido; com `Resolve`, o SDK sela só as respostas já dadas, não o pedido
  (`mcp/server/mcpserver/resolve.py`, docstring do módulo):

  ```text
  Only *asked* outcomes ride `request_state`, so each question is asked once. Resolver
  bodies may re-run on every round; a recorded outcome is consulted only when the
  body asks its question again
  ```

  A forma manual sela `sala`, `inicio`, `fim` e `responsavel` e reconstrói o pedido só a partir do state (`_retomar` em `tools.py`).
  O SDK não deixa misturar as duas formas: uma tool com parâmetro `Resolve(...)` não pode devolver `InputRequiredResult` (`tools/base.py`).
- **Estado das Tasks:** no agente, dicionário em memória protegido por `Lock` (`TaskStore`), um registro por Task com o próprio `requestState`.
  As reservas ficam em memória no servidor MCP. Ambos somem no restart, o que o enunciado permite; o `requestState` é a exceção, porque é autocontido.
- **A2A à mão:** JSON-RPC 2.0 mínimo (`SendMessage`, `GetTask`) sobre Starlette. `SendMessage` é síncrono e devolve a Task já pausada ou terminal
  (`SUBMITTED` e `WORKING` são transições reais, validadas por máquina de estados; estado terminal é definitivo).
- **Erros A2A:** Task terminal `-32004` (`UnsupportedOperationError`); Task inexistente `-32001` (`TaskNotFoundError`); pedido fora do formato,
  ou mensagem para Task que não está aguardando resposta, `-32602`; método desconhecido `-32601`; requisição JSON-RPC inválida `-32600`;
  JSON inválido `-32700`.
- **Validação de entrada de data:** `inicio`/`fim` sem fuso ou fora do ISO 8601 devolvem `Intervalo invalido: informe inicio e fim em ISO 8601 com fuso`
  (o enunciado não define essa mensagem; escolha mínima para não quebrar o servidor).
- **Descoberta:** no pedido inicial de cada Task, o agente faz `tools/list` e `resources/read politica://uso` (extrai a versão da primeira linha)
  antes do `tools/call`. Na continuação, relê só o resource antes do retry. Sem sessão, todo request leva `_meta` completo e os headers espelhados
  (`MCP-Protocol-Version`, `Mcp-Method` e, em `tools/call` e `resources/read`, `Mcp-Name`).
- **traceparent:** o trace-id do header A2A (ou um novo, se ausente ou inválido) vai em todos os requests MCP da Task, com span-id novo a cada request;
  continuações sem header herdam o trace da Task.

## Saída do validador

```text
trace-id desta execucao: a65b1efb1dda409ee3ff46f8d4b0bf69
procure esse valor no stderr do servidor MCP para conferir a propagacao do traceparent.

PASS 01 tools/list traz as tres tools
PASS 02 toda tool tem inputSchema de objeto
PASS 03 listar_salas devolve structuredContent e o mesmo JSON em texto
PASS 04 _meta sem protocolVersion devolve -32602 e HTTP 400
PASS 05 _meta sem clientCapabilities devolve -32602 e HTTP 400
PASS 06 tool inexistente e recusada, por -32602 ou por isError
PASS 07 resources/read de politica://uso devolve a politica
PASS 08 resources/read de URI inexistente devolve -32602
PASS 09 sala inexistente devolve isError com a mensagem exata
PASS 10 fora da janela devolve isError com a mensagem exata
PASS 11 duracao acima de 2h devolve isError com a mensagem exata
PASS 12 intervalo invertido devolve isError com a mensagem exata
PASS 13 conflito devolve input_required com inputRequests e requestState
PASS 14 a elicitation e form mode e oferece as alternativas na ordem certa
PASS 15 conflito sem a capability elicitation devolve -32021 e HTTP 400
PASS 16 retry com inputResponses e requestState conclui a reserva
PASS 17 requestState adulterado e rejeitado com -32602
PASS 18 argumentos adulterados no retry nao tomam efeito
PASS 19 recusa conclui sem reservar e sem isError
PASS 20 conflito sem alternativa possivel devolve isError com a mensagem exata

PASS 21 agent card responde 200 no well-known com JSON
PASS 22 o card declara a interface JSON-RPC com url e versao 1.0
PASS 23 o card declara a skill reservar-sala
PASS 24 SendMessage com sala livre conclui a Task
PASS 25 o artifact chama reserva e traz a versao da politica
PASS 26 GetTask devolve id, contextId e estado corrente
PASS 27 SendMessage com sala ocupada pausa a Task
PASS 28 a Task pausada lista as alternativas na ordem certa
PASS 29 escolha fora do enum mantem a Task pausada
PASS 30 a continuacao conclui a Task na sala escolhida
PASS 31 SendMessage em Task terminal e recusado
PASS 32 a recusa termina a Task em CANCELED
PASS 33 duas Tasks pausadas ao mesmo tempo concluem cada uma com a sua reserva
PASS 34 nenhuma resposta A2A carrega o requestState
PASS 35 sala inexistente termina a Task em FAILED com a mensagem da tool
PASS 36 o agente e deterministico: o mesmo pedido produz a mesma pausa

resumo: 36 passaram, 0 falharam, de 36 verificacoes
```
