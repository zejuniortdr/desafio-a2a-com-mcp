# SPEC_COVERAGE

Fontes: `enunciado.md` (fora do repositorio, em `../docs/enunciado.md`) e `README.md`.
Legenda: `S:` = `servidor-mcp/tests/`, `A:` = `agente/tests/`, `Vnn` = verificacao `nn` do `validador/validar.py`
(roda contra os dois processos reais, nao pelo pytest).

## 1. Clausulas e testes

### Servidor MCP

| # | Clausula | Testes |
|---|----------|--------|
| 1 | Streamable HTTP, endpoint unico `/mcp`, porta padrao 7301 | V01; S:test_tools_http (todo o arquivo fala `/mcp`); S:test_mrtr::test_secret_valido (porta padrao); S:test_log_e_app::test_main_configura_log_no_stderr_e_sobe_uvicorn_em_loopback_na_porta_da_config |
| 2 | Request sem `protocolVersion` ou `clientCapabilities` no `_meta`: `-32602` + HTTP 400 | V04, V05; S:test_tools_http::test_sem_meta_obrigatorio |
| 3 | Declara capabilities `tools` e `resources` | S:test_log_e_app::test_server_discover_declara_nome_e_capabilities_de_tools_e_resources |
| 4 | Tres tools, todas com `inputSchema` objeto | V01, V02; S:test_tools_http::test_tools_list |
| 5 | `listar_salas`: `structuredContent` conforme `outputSchema` e o mesmo JSON em texto | V03; S:test_tools_http::test_listar_salas_structured_igual_texto |
| 6 | `consultar_disponibilidade`: livre/conflitos, mesmas validacoes e erros da reserva | S:test_tools_http::test_consultar_conflito, ::test_erros_de_execucao[consultar_disponibilidade], ::test_reserva_livre |
| 7 | `reservar_sala` cria a reserva com intervalo livre e dentro da politica | V24; S:test_tools_http::test_reserva_livre |
| 8 | `structuredContent` da reserva: `reserva, reservado, sala, inicio, fim, responsavel, politica` | S:test_tools_http::test_reserva_livre; V25 |
| 9 | Resource `politica://uso`, conteudo de `dados/politica-de-uso.md`, `text/markdown` | V07; S:test_tools_http::test_resource_politica, ::test_resources_list |
| 10 | `resources/read` de URI inexistente: `-32602` | V08; S:test_tools_http::test_resource_inexistente |
| 11 | stderr registra metodo, id e `traceparent` de cada request | S:test_log_e_app::test_log_traz_metodo_id_e_traceparent, ::test_log_sem_traceparent_registra_hifen, ::test_log_traceparent_filtra_caracteres_e_corta_em_55, ::test_log_corta_method_e_id_em_64, ::test_log_id_numerico_vira_texto; S:test_tools_http::test_log_nao_aceita_quebra_de_linha_nem_id_gigante; workflow `validador` (grep do trace-id no stderr) |
| 12 | Tool inexistente e recusada (`-32602` ou `isError`) | V06; S:test_tools_http::test_tool_inexistente_recusada |
| 13 | `Sala inexistente: <id>` | V09; S:test_dominio::test_validar_erros; S:test_tools_http::test_erros_de_execucao |
| 14 | Inicio ou fim fora de 08:00-20:00 (SP): `Fora da janela de uso: ...` (bordas, segundos, fuso) | V10; S:test_dominio::test_validar_erros, ::test_validar_bordas_ok, ::test_validar_20h01_fora, ::test_janela_e_sempre_avaliada_no_fuso_de_sao_paulo |
| 15 | Mais de 2 h: `Duracao acima do limite: ...` (2 h exatas passam) | V11; S:test_dominio::test_validar_erros, ::test_validar_bordas_ok |
| 16 | `fim <= inicio`: `Intervalo invalido: fim deve ser posterior a inicio` | V12; S:test_dominio::test_validar_erros (inclui precedencia) |
| 17 | Conflito sem nenhuma alternativa: `Sem alternativas disponiveis no intervalo` (`isError`, sem elicitation) | V20; S:test_mrtr::test_sem_alternativa_e_erro_de_execucao |
| 18 | Data fora de ISO 8601 ou sem fuso: erro de execucao (escolha do README, nao do enunciado) | S:test_dominio::test_validar_erros (2 casos) |
| 19 | Nenhuma regra depende da data de hoje; reservar no passado e permitido | S:test_mrtr::test_reservar_no_passado_e_permitido |
| 20 | Alternativas: salas livres, capacidade >= a pedida, maximo 3, capacidade crescente e `id` em empate | V14; S:test_dominio::test_alternativas_garagem_ordem_e_limite, ::test_alternativas_limite_tres_e_desempate_por_id, ::test_alternativas_vazia_para_mirante, ::test_alternativa_ocupada_no_intervalo_fica_de_fora |
| 21 | Reservas em memoria, visiveis nas consultas seguintes do mesmo processo | S:test_dominio::test_add_fica_visivel_em_conflitos, ::test_add_gera_ids_sequenciais, ::test_repo_vazio_comeca_em_res_0001; S:test_tools_http::test_reserva_livre |

### MRTR

| # | Clausula | Testes |
|---|----------|--------|
| 22 | Conflito: `resultType=input_required`, uma entrada em `inputRequests`, form mode, `requestState` presente | V13; S:test_mrtr::test_conflito_pede_input |
| 23 | `requestedSchema` plano, `sala` string restrita as alternativas (enum, ou const se so uma), na ordem | V14; S:test_mrtr::test_conflito_pede_input, ::test_uma_unica_alternativa_continua_sendo_oferecida_e_aceita_no_retry |
| 24 | `requestState` com integridade (AEAD): adulterado ou de outra chave e rejeitado | V17; S:test_mrtr::test_state_adulterado_32602, ::test_state_de_outro_secret_e_rejeitado |
| 25 | Expiracao entre 5 e 30 min (600 s): 599 s vale, 601 s expira, `-32602`; TTL passado explicitamente ao SDK | S:test_mrtr::test_state_vale_ate_10_minutos, ::test_state_expira_depois_de_10_minutos; S:test_log_e_app::test_ttl_do_request_state_e_passado_explicitamente_ao_sdk |
| 26 | State autocontido: retry apos restart do processo conclui | S:test_mrtr::test_retry_sobrevive_a_restart; Fluxo do avaliador, passo 12 |
| 27 | Chave vem de `REQUEST_STATE_SECRET`, >= 32 bytes hex; sem segredo no codigo | S:test_mrtr::test_secret_invalido_na_config, ::test_secret_ausente_do_ambiente_e_config_invalida, ::test_mensagens_de_config_invalida_orientam_o_operador, ::test_config_sem_argumento_le_os_valores_do_os_environ, ::test_nenhum_segredo_hardcoded_no_codigo_do_servidor |
| 28 | Argumentos divergentes no retry nao tomam efeito (usa os selados) | V18; S:test_mrtr::test_args_adulterados_no_retry_nao_tem_efeito |
| 29 | Conflito sem elicitation form: `-32021` + `data.requiredCapabilities` + HTTP 400 | V15; S:test_mrtr::test_sem_capability_32021, ::test_sem_capability_32021_tem_mensagem_e_nao_depende_de_caps_nulas, ::test_elicitation_vazia_equivale_a_form_mode, ::test_elicitation_so_url_nao_serve_32021 |
| 30 | Sem conflito, a falta da capability nao e erro | S:test_mrtr::test_sala_livre_nao_exige_capability_de_elicitation |
| 31 | Retry le `inputResponses`/`requestState` de `params` e conclui (`resultType=complete`) | V16; S:test_mrtr::test_retry_accept_conclui |
| 32 | `decline`/`cancel` concluem sem reservar e sem `isError`, com `reservado=false` e `motivo` | V19; S:test_mrtr::test_recusa_conclui_sem_reservar[decline, cancel] |
| 33 | So vale alternativa oferecida e ainda livre | S:test_mrtr::test_escolha_fora_do_enum_nao_reserva, ::test_alternativa_tomada_entre_a_pausa_e_o_retry_nao_reserva_de_novo |
| 34 | `inputResponses` sem a chave da pausa: `-32602` | S:test_mrtr::test_retry_sem_a_chave_da_pausa_32602 |
| 35 | State selado com conteudo invalido: `-32602 requestState invalido` | S:test_mrtr::test_state_selado_com_conteudo_invalido_vira_32602 |
| 36 | (adversarial) sem double-booking sob concorrencia: N reservas simultaneas do mesmo intervalo criam uma; ids unicos; retries concorrentes do mesmo state reservam uma vez | S:test_concorrencia (4 testes) ; S:test_dominio::test_reservar_se_livre_concorrente_gera_uma_reserva |

### Agente: host MCP

| # | Clausula | Testes |
|---|----------|--------|
| 37 | `tools/list` em runtime antes da primeira chamada, sem lista fixa | A:test_ponte::test_descoberta_antes_da_primeira_chamada_e_trace, ::test_tool_ausente_na_descoberta_vira_failed_sem_chamar |
| 38 | Le `politica://uso` e extrai a versao da primeira linha (ida e volta) | A:test_ponte::test_sala_livre_conclui_com_artifact, ::test_politica_vazia_vira_failed; A:test_contratos::test_versao_corta_so_no_primeiro_dois_pontos, ::test_a_politica_e_lida_pela_uri_certa_na_ida_e_na_volta |
| 39 | Todo request leva `_meta` obrigatorio e `{"elicitation":{"form":{}}}` | A:test_mcp_client::test_tools_list_manda_headers_e_meta |
| 40 | Headers espelhados: `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name` (so em `tools/call` e `resources/read`) | A:test_mcp_client::test_tools_list_manda_headers_e_meta, ::test_read_resource_manda_mcp_name; A:test_contratos::test_cabecalhos_e_corpo_exatos, ::test_tools_list_nao_manda_mcp_name |
| 41 | Mesmo trace-id do header A2A em todos os requests da Task, span novo; sem header herda da Task | A:test_a2a::test_send_pausa_propaga_trace_e_nao_vaza_request_state; A:test_ponte::test_continuacao_com_traceparent_novo_usa_o_trace_novo, ::test_traceparent_ausente_ou_invalido_gera_trace_novo; A:test_mcp_client::test_cada_chamada_tem_id_e_traceparent_proprios; A:test_contratos::test_novo_traceparent_tem_formato_w3c, ::test_call_tool_propaga_trace, ::test_continuacao_leva_traceparent_e_message_id_do_cliente, ::test_trace_id_de |
| 42 | `isError` da tool: Task `FAILED` com a mensagem exata no historico | V35; A:test_ponte::test_erro_da_tool_vira_failed_com_mensagem_exata |
| 43 | Cliente cru: enxerga o `input_required` (sem callback de elicitation) | A:test_mcp_client::test_input_required_cru, ::test_enum_const, ::test_pausa_sem_enum_nem_const_tem_lista_vazia |
| 44 | Erro MCP, de rede, resposta malformada ou excecao inesperada: Task sai de `WORKING` (`FAILED`) | A:test_ponte::test_erro_do_mcp_vira_failed, ::test_falha_na_continuacao_nunca_deixa_a_task_em_working, ::test_resultado_sem_campos_da_reserva_vira_failed; A:test_mcp_client (erros); A:test_concorrencia::test_falha_do_mcp_no_pedido_inicial_nunca_deixa_task_em_working (21 casos), ::test_falha_do_mcp_na_continuacao_termina_failed_e_libera_o_state, ::test_excecao_inesperada_* |
| 45 | Timeout/cancelamento do chamador nao deixa a Task em `WORKING` | A:test_concorrencia::test_timeout_externo_no_pedido_inicial_nao_deixa_a_task_em_working, ::test_timeout_externo_na_continuacao_nao_deixa_a_task_em_working |

### Agente: servidor A2A

| # | Clausula | Testes |
|---|----------|--------|
| 46 | `GET /.well-known/agent-card.json`: card v1.0 com interface JSON-RPC (url, versao 1.0) e skill `reservar-sala` | V21, V22, V23; A:test_a2a::test_card; A:test_contratos::test_card_exato, ::test_metodos_http_errados_dao_405, ::test_montar_liga_card_a_public_url_da_config |
| 47 | `/a2a` aceita `SendMessage` e `GetTask` (JSON-RPC 2.0) | A:test_a2a (todo); A:test_contratos::test_erros_a2a_tem_codigo_e_mensagem_exatos |
| 48 | `SendMessage` sem `taskId` abre Task com `id` e `contextId` proprios | A:test_tasks::test_create_gera_ids_proprios_em_submitted; A:test_contratos::test_ids_tem_prefixo_e_12_hex |
| 49 | `SUBMITTED` -> `WORKING` antes de terminar; `GetTask` reflete o estado corrente a qualquer momento | A:test_tasks::test_transicao_invalida; A:test_concorrencia::test_get_task_reflete_working_enquanto_o_mcp_trabalha; V26; A:test_a2a::test_get_task_devolve_a_task_sem_request_state |
| 50 | Conclusao: artifact `reserva` com o JSON da reserva e `politica` lida do resource | V24, V25; A:test_ponte::test_sala_livre_conclui_com_artifact |
| 51 | Estado terminal e definitivo; `SendMessage` em Task terminal e recusado (`-32004`) | V31; A:test_tasks::test_terminal_e_definitivo; A:test_ponte::test_continuar_task_terminal_e_recusado; A:test_a2a::test_send_em_task_terminal_e_erro; A:test_contratos::test_task_terminal_devolve_32004_com_o_id_do_pedido_e_mensagem |
| 52 | Codigos de erro do README: `-32001`, `-32004`, `-32602`, `-32601`, `-32600`, `-32700`, com o `id` do pedido | A:test_a2a::test_erros_jsonrpc_devolvem_o_id_do_pedido, ::test_sem_id_legivel_o_erro_vai_com_id_nulo; A:test_contratos (erros, JSON invalido, params, jsonrpc invalido) |
| 53 | Pedido em formato fixo `reservar sala=... inicio=... fim=... responsavel=...`; resposta `escolha=<id>` ou `escolha=recusar` | A:test_pedido (3 testes); A:test_contratos::test_pedido_invalido_tem_mensagem_exata |

### A ponte

| # | Clausula | Testes |
|---|----------|--------|
| 54 | `input_required` vira `INPUT_REQUIRED` com a mensagem exata `alternativas: a, b` na ordem do enum | V27, V28, V36; A:test_ponte::test_pausa_mensagem_exata_e_state_guardado |
| 55 | `requestState` preso a Task e nunca exposto (card, artifact, mensagens, `GetTask`) | V34; A:test_a2a::test_send_pausa_propaga_trace_e_nao_vaza_request_state, ::test_get_task_devolve_a_task_sem_request_state, ::test_continuacao_conclui_sem_vazar_request_state |
| 56 | Escolha fora do enum mantem a Task em `INPUT_REQUIRED` e repete a lista (sem chamar o MCP; limite de tentativas) | V29; A:test_ponte::test_escolha_fora_do_enum_mantem_pausa_sem_chamar_mcp, ::test_tentativas_fora_do_enum_tem_limite, ::test_escolha_valida_depois_de_uma_fora_do_enum_conclui |
| 57 | Retry com id JSON-RPC novo, `inputResponses` na mesma chave, `requestState` ecoado | A:test_ponte::test_continuacao_aceite_usa_id_novo_state_ecoado; A:test_mcp_client::test_retry_ecoa_state_e_respostas; Fluxo do avaliador, passo 6 |
| 58 | Continuacao conclui em `COMPLETED` na sala escolhida | V30; A:test_ponte::test_continuacao_aceite_usa_id_novo_state_ecoado; A:test_a2a::test_continuacao_conclui_sem_vazar_request_state |
| 59 | `escolha=recusar` vira `decline` e a Task termina em `CANCELED` | V32; A:test_ponte::test_recusa_cancela |
| 60 | Estado pausado por Task: duas pausas simultaneas concluem cada uma com o seu `requestState` | V33; A:test_ponte::test_duas_pausas_recebem_states_distintos, ::test_duas_tasks_pausadas_nao_trocam_state; A:test_concorrencia::test_n_pausas_simultaneas_cada_uma_conclui_com_o_seu_state |
| 61 | (adversarial) continuacoes duplicadas da mesma Task: so uma chega ao MCP | A:test_concorrencia::test_continuacoes_duplicadas_da_mesma_task_so_uma_chega_ao_mcp |
| 62 | Conexao recusada na continuacao devolve a pausa (state continua valido) e permite novo envio | A:test_ponte::test_servidor_fora_do_ar_na_continuacao_mantem_a_pausa; A:test_concorrencia::test_conexao_recusada_na_continuacao_devolve_a_pausa_e_permite_novo_envio |
| 63 | Mensagem de usuario no historico (com e sem ids) | A:test_ponte::test_historico_guarda_papeis_e_ids; A:test_contratos::test_pedido_inicial_vira_mensagem_de_usuario_sem_ids, ::test_resposta_do_usuario_entra_no_historico_com_ids |

### Restricoes e entrega

| # | Clausula | Testes / verificacao |
|---|----------|----------------------|
| 64 | Dois processos; o agente nao importa o servidor nem implementa regra de sala | A:test_contratos::test_agente_nao_importa_o_servidor_nem_sdk_de_llm_e_nao_tem_regra_de_sala |
| 65 | Sem LLM no caminho nem nas dependencias; mesmo pedido, mesmo resultado | V36; A:test_contratos::test_agente_nao_importa_..., ::test_mesmo_pedido_duas_vezes_produz_o_mesmo_resultado |
| 66 | Versoes travadas (`==`) nos `pyproject.toml` | inspecao dos dois `pyproject.toml` (sem teste automatizado) |
| 67 | `dados/`, `validador/`, `exemplos/` intactos | `git log -- dados validador exemplos` mostra so o commit inicial (verificado manualmente) |
| 68 | README com as 4 secoes; comandos funcionam em clone limpo | workflow `validador` (CI) e a execucao em clone limpo descrita na secao 3 |
| 69 | Validador 36/36 em processos recem-iniciados, saida 0 | workflow `validador`; execucao em clone limpo (secao 3) |
| 70 | `traceparent` visivel no stderr do servidor | workflow `validador` (`grep "traceparent=00-$trace-"`) |

## 2. Mutation testing (mutmut 3.8.1)

Comando, em cada pacote: `.venv/bin/mutmut run` (config em `[tool.mutmut]` de cada `pyproject.toml`). No servidor, rode antes `cp -r dados servidor-mcp/dados` (ignorado no git): os testes rodam em `servidor-mcp/mutants/tests` e resolvem `dados/` dois niveis acima.

| Pacote | Mutantes | Mortos | Sobreviventes (equivalentes) | Score |
|--------|----------|--------|------------------------------|-------|
| servidor-mcp (`central_salas`) | 452 | 433 | 19 | 95,8 % |
| agente (`agente_salas`) | 1012 | 1004 | 8 | 99,2 % |
| **Total** | **1464** | **1437** | **27** | **98,2 %** |

Antes: servidor 77 % (82 sobreviventes + 21 sem teste em `main`), agente 74 % (193 sobreviventes + 66 sem teste + 1 timeout).

Os 27 restantes foram analisados um a um e sao equivalentes:

| Mutantes | Por que sao equivalentes |
|----------|--------------------------|
| `app.criar_servidor_13/18`, `dominio.carregar_salas_2/4`, `dominio.carregar_reservas_24/26` (`encoding=None` ou `"UTF-8"`) | Os JSON/MD de `dados/` sao ASCII e o locale e UTF-8; `"UTF-8"` e alias de `"utf-8"`. Nenhum teste legitimo distingue sem alterar `dados/` (proibido). |
| `app.criar_servidor_31` (`ttl` removido) | O default do SDK (`600.0`) coincide com `TTL_REQUEST_STATE_S`. O comportamento e identico; o teste `test_ttl_do_request_state_e_passado_explicitamente_ao_sdk` guarda o argumento contra uma mudanca futura do default. |
| `config.carregar_config_13` (default `"XXXX"` para o segredo) e `_16` (`aleatorios = 1` no `ValueError`) | Ausente ou nao-hex continua dando < 32 bytes e `ConfigInvalida`; o resultado observavel e o mesmo. |
| `tools._exigir_elicitation_form_26/27/29/30/32/33/34` (`by_alias`, `mode`) | `{"elicitation": {"form": {}}}` serializa igual com qualquer combinacao dessas opcoes (nomes ja em minusculo, sem aliases, sem tipos nao-JSON). |
| `tools.registrar_tools_20` (`request_state or ""` -> `"XXXX"`) | O ramo so e alcancado com `request_state is not None`; o fallback nunca e usado. |
| `dominio._dt_3/10/11` (tratamento do sufixo `Z`) | Equivalentes no Python >= 3.11, onde `fromisoformat` ja aceita `Z`. Sao mortos no 3.10 por `test_validar_bordas_ok[...Z]` (ver secao 3). |
| `pedido.parse_pedido_3` (`texto or ""` -> `"XXXX"`) e `ponte.trace_id_de_5` (idem) | Texto vazio ou `"XXXX"` falham no mesmo regex; mesma excecao ou mesmo `None`. |
| `ponte.iniciar_16` (`com_ids=None` em vez de `False`) | `None` e falsy; mesmo comportamento. |
| `a2a.criar_app_128` (`headers.get("TRACEPARENT")`) | Headers HTTP sao case-insensitive no Starlette. |
| `a2a.criar_app_172/175` (`methods=None` / omitido) | Para endpoint-funcao, o Starlette usa `["GET"]` por padrao. |
| `a2a.criar_app_179/189` (`"get"`, `"post"`) | O Starlette poe os metodos em maiusculas. |

Notas de execucao: o mutmut copia `src/` para `mutants/` (ignorado no git), o que muda `parents[3]` do `config.py`; por isso o teste que depende do layout real do repositorio (`test_dados_dir_padrao_e_a_pasta_dados_da_raiz_do_repo`) e pulado quando `MUTANT_UNDER_TEST` esta definido, e ha um teste equivalente que compara contra `Path(config.__file__).parents[3]`.
Depois de acrescentar testes, apague `mutants/mutmut-stats.json` antes de rodar de novo: o mutmut so executa, para cada mutante, os testes que cobriam a funcao na coleta de estatisticas anterior.

## 3. Bugs encontrados

1. **Task presa em `WORKING` quando o chamador cancela ou expira o prazo** (`agente/src/agente_salas/ponte.py`, `iniciar` e `continuar`). `asyncio.CancelledError` nao esta em `_FALHAS_MCP`; com `asyncio.wait_for(...)` ou desconexao durante o `tools/call`, a Task ficava para sempre em `TASK_STATE_WORKING`, sem saida (nem `SendMessage`, que exige `INPUT_REQUIRED`). Correcao: a Task vai para `FAILED` e o cancelamento e repropagado. Na continuacao ela nao volta para a pausa porque o servidor pode ja ter reservado (um novo retry duplicaria a reserva). Testes de regressao (falham no codigo antigo com `Task ficou em TASK_STATE_WORKING`): `test_timeout_externo_no_pedido_inicial_nao_deixa_a_task_em_working`, `test_timeout_externo_na_continuacao_nao_deixa_a_task_em_working`.
2. **Task presa em `WORKING` por excecao fora de `_FALHAS_MCP`** (mesmos pontos, p. ex. `ZeroDivisionError`, `OSError`). A excecao escapava e a Task ficava em `WORKING`. Correcao: a Task vai para `FAILED` ("Erro interno do agente: <Tipo>") e a excecao sobe para a borda. Regressao: `test_excecao_inesperada_falha_a_task_e_propaga_sem_deixar_working` (3 x 2 casos) e `test_excecao_inesperada_na_continuacao_falha_a_task_e_libera_o_state` (falharam no codigo antigo).

Alem disso, o working tree tinha uma linha corrompida em `servidor-mcp/tests/test_mrtr.py` (`claudeimport time`), que impedia a coleta do pytest do servidor; foi corrigida.

Sem defeito encontrado (o teste passou no codigo atual e falha se o codigo for degradado): double-booking pela borda HTTP (confirmado removendo o lock de `reservar_se_livre`: 5 dos 6 testes de concorrencia do servidor falham), continuacoes duplicadas e mistura de `requestState` entre Tasks.
