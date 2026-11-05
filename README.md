# A Ponte: agente A2A com MCP por dentro

Hill Valley Tech: cinco salas de reuniao expostas como **servidor MCP** (`servidor-mcp/`, porta 7301) e um
**agente A2A** (`agente/`, porta 7300) que consome esse servidor como host MCP e se oferece ao mundo como servidor A2A.
Sem LLM no caminho de execucao: o mesmo pedido produz sempre o mesmo resultado.

## Como rodar

Requisitos: Python >= 3.10 e `make`. A partir de um clone limpo, na raiz do repositorio:

```bash
make install    # um venv por processo (servidor-mcp/.venv e agente/.venv), dependencias diretas travadas com == nos pyproject.toml

# terminal 1: servidor MCP na 7301 (stderr mostra metodo, id e traceparent de cada request)
export REQUEST_STATE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
make servidor   # para reiniciar o servidor, rode de novo so esta linha, no mesmo terminal (mesmo segredo)

make agente     # terminal 2: agente A2A na 7300
make validar    # terminal 3: validador do starter contra os dois processos
```

| Alvo | O que faz |
|------|-----------|
| `make install` | cria os dois venvs e instala os pacotes (com extras de teste) |
| `make servidor` | sobe o servidor MCP (`central-salas`) |
| `make agente` | sobe o agente A2A (`agente-salas`) |
| `make validar` | roda `python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301` |
| `make test` | roda o pytest dos dois pacotes (servidor-mcp e agente) |
| `make clean` | remove os dois venvs |

**Segredo do `requestState`:** o servidor MCP recusa subir com `REQUEST_STATE_SECRET` vazio ou com menos de 32 bytes; gere sempre com o
comando acima (32 bytes aleatorios) e nunca versione o valor. So o alvo `servidor` recebe a variavel. Sem o `export`, `make servidor` gera um
segredo novo a cada subida: o validador passa, mas um `requestState` emitido antes de um restart deixa de valer. Por isso o `export` vem
antes, e o restart do passo 12 do avaliador precisa acontecer no mesmo terminal.

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

Variaveis opcionais (defaults do enunciado): `MCP_PORT=7301`, `A2A_PORT=7300`, `MCP_URL=http://127.0.0.1:7301/mcp`,
`PUBLIC_URL=http://127.0.0.1:7300`, `DADOS_DIR`.

## Onde a ponte acontece

Tudo vive em [`agente/src/agente_salas/ponte.py`](agente/src/agente_salas/ponte.py) (`BridgeService`), o unico modulo que conhece os dois protocolos.
Na **ida**, `_aplicar` recebe do `McpClient` (cliente httpx cru, sem callback de elicitation, entao o `input_required` chega intacto) o resultado
`input_required` e o transforma em `TASK_STATE_INPUT_REQUIRED`: guarda `requestState`, a chave de `inputRequests` e o `enum` na propria `Task`
e devolve ao cliente A2A somente a linha `alternativas: a, b`. Na **volta**, `continuar` recebe `escolha=<id>` num `SendMessage` com `taskId`, valida
a escolha contra o `enum` guardado (fora dele a Task segue pausada e repete a lista) e repete o `tools/call` com **id JSON-RPC novo**,
`inputResponses` sob a mesma chave e o `requestState` ecoado sem abrir. O DTO de saida (`task_dto` em `a2a.py`) nunca serializa o estado da pausa.

## Decisoes tecnicas

- **Protecao do `requestState`:** `RequestStateSecurity` do SDK `mcp` 2.3.0 (AES-256-GCM, chave derivada por HKDF de `REQUEST_STATE_SECRET`;
  cifrado, nao so assinado). O SDK tambem amarra o state ao request (adulterar `arguments` no retry e rejeitado com `-32602`).
  O pedido original (sala, inicio, fim, responsavel) viaja dentro do state selado; o servidor nao guarda nada entre o `input_required` e o retry,
  entao um retry apos restart do processo funciona (mesma chave).
- **Validade:** 600 s (10 min), dentro da faixa de 5 a 30 min (`TTL_REQUEST_STATE_S` em `servidor-mcp/src/central_salas/app.py`).
  Expirado ou adulterado: `-32602` (coberto em `servidor-mcp/tests/test_mrtr.py`: 599 s conclui, 601 s rejeita).
- **MRTR manual em vez de `Resolve/Elicit`:** a ferramenta devolve `InputRequiredResult` diretamente. O enunciado exige que o `requestState`
  carregue tudo que o servidor precisa para reconstruir o pedido; com `Resolve`, o SDK sela so as respostas ja dadas, nao o pedido
  (`mcp/server/mcpserver/resolve.py`, docstring do modulo):

  ```text
  Only *asked* outcomes ride `request_state`, so each question is asked once. Resolver
  bodies may re-run on every round; a recorded outcome is consulted only when the
  body asks its question again
  ```

  A forma manual sela `sala`, `inicio`, `fim` e `responsavel` e reconstroi o pedido so a partir do state (`_retomar` em `tools.py`).
  O SDK nao deixa misturar as duas formas: uma tool com parametro `Resolve(...)` nao pode devolver `InputRequiredResult` (`tools/base.py`).
- **Estado das Tasks:** dicionario em memoria protegido por `Lock` (`TaskStore`), um registro por Task com o proprio `requestState`.
  Reservas tambem em memoria; ambos somem no restart (permitido pelo enunciado, exceto o `requestState`, que e autocontido).
- **A2A a mao:** JSON-RPC 2.0 minimo (`SendMessage`, `GetTask`) sobre Starlette; `SendMessage` e sincrono e devolve a Task ja pausada ou terminal
  (`SUBMITTED` e `WORKING` sao transicoes reais, validadas por maquina de estados; estado terminal e definitivo).
- **Erros A2A:** Task terminal `-32004` (`UnsupportedOperationError`), Task inexistente `-32001` (`TaskNotFoundError`), pedido fora do formato `-32602`, metodo desconhecido `-32601`, JSON invalido `-32700`.
- **Validacao de entrada de data:** `inicio`/`fim` sem fuso ou fora do ISO 8601 devolvem `Intervalo invalido: informe inicio e fim em ISO 8601 com fuso`
  (o enunciado nao define essa mensagem; escolha minima para nao quebrar o servidor).
- **Descoberta:** a cada Task o agente faz `tools/list` e `resources/read politica://uso` (extrai a versao da primeira linha) antes do `tools/call`;
  sem sessao, todo request leva `_meta` completo e headers espelhados (`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`).
- **traceparent:** o trace-id do header A2A (ou um novo, se ausente/invalido) vai em todos os requests MCP da Task, com span-id novo a cada request;
  continuacoes sem header herdam o trace da Task.

## Saida do validador

```text
trace-id desta execucao: e2cdf20e1c902762fbc206d6b4150f9d
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
