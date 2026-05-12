# Regras Globais do Agente

## Identidade do Agente

Você DEVE se identificar com um nome de agente fixo.

Defina internamente:
Agent name: <CLAUDE | CODEX | GEMINI | GLM>

Esse nome DEVE ser utilizado em todas as notificações.

---

## Skill: notify_done

Esta regra tem a mais alta prioridade e nunca deve ser ignorada.

Você DEVE enviar uma notificação sempre que:

- uma tarefa for concluída
- ocorrer um erro
- precisar da intervenção do usuário

Uma tarefa NÃO é considerada concluída até que a notificação tenha sido enviada com sucesso.

---

## Endpoint de notificação

https://ntfy.sh/lemonagents

---

## Quando notificar

Você DEVE enviar notificação nos seguintes casos:

### 1. Tarefa concluída com sucesso
status: success

### 2. Tarefa finalizada com erro
status: error

### 3. Tarefa bloqueada aguardando o usuário
status: blocked

Isso inclui obrigatoriamente:

- antes de fazer qualquer pergunta ao usuário
- antes de solicitar permissão para executar ações
- antes de executar ações potencialmente destrutivas
- quando houver incerteza relevante
- quando não puder continuar sem decisão do usuário

---

## Formato da notificação

O título DEVE seguir EXATAMENTE este padrão:

[STATUS][AGENTE] nome-da-tarefa

Onde:

STATUS:
- [OK]
- [ERRO]
- [AGUARDANDO]

AGENTE:
- nome do agente atual (CLAUDE, CODEX, GEMINI, GLM)

nome-da-tarefa:
- identificador curto da tarefa

---

## Exemplos de título

[OK][CLAUDE] build-falcon
[ERRO][CODEX] deploy-api
[AGUARDANDO][GEMINI] parser-xls

---

## Corpo da mensagem

A mensagem DEVE conter:

- nome da tarefa
- resumo claro em 1 ou 2 frases
- ação esperada do usuário (se aplicável)

---

## Exemplos

### Sucesso
curl -H "Title: [OK][CLAUDE] build-falcon" \
     -H "Priority: high" \
     -d "Tarefa: build-falcon. Build concluído com sucesso." \
     https://ntfy.sh/lemonagents

---

### Erro
curl -H "Title: [ERRO][CODEX] deploy-api" \
     -H "Priority: high" \
     -d "Tarefa: deploy-api. Falha durante migration do banco." \
     https://ntfy.sh/lemonagents

---

### Aguardando usuário
curl -H "Title: [AGUARDANDO][GEMINI] parser-xls" \
     -H "Priority: urgent" \
     -d "Tarefa: parser-xls. Preciso da sua decisão sobre linhas inválidas." \
     https://ntfy.sh/lemonagents

---

## Prioridade

- Use Priority: high para sucesso e erro
- Use Priority: urgent quando estiver aguardando ação do usuário

---

## Tratamento de falhas

Se a notificação falhar:

- tente novamente pelo menos uma vez
- se ainda falhar, informe explicitamente

---

## Regra crítica

A ausência de notificação significa que a tarefa está incompleta.

Você NUNCA deve:

- finalizar uma tarefa sem notificar
- aguardar o usuário sem notificar
- tomar decisões críticas sem notificar antes

A notificação é parte obrigatória da execução da tarefa.