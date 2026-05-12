# Tarefas — Tabularium

> **Referência:** [`docs/PLANO SISTEMA.md`](./PLANO%20SISTEMA.md)
> **Última atualização:** 2026-05-12

---

## Fase 0 — Infraestrutura

- [x] **0.1** Criar arquivo `.env` com credenciais do banco e variáveis do sistema
- [x] **0.2** Criar `docker-compose.yml` (MySQL + Backend + Frontend + Nginx)
- [x] **0.3** Criar `nginx.conf` como proxy reverso para a API
- [x] **0.4** Criar `Dockerfile` do backend FastAPI
- [x] **0.5** Criar estrutura de pastas `backend/` e `frontend/`
- [x] **0.6** Testar `docker compose up` — MySQL acessível e healthy

---

## Fase 1 — Fundação (Backend + Banco)

- [x] **1.1** Inicializar projeto FastAPI (estrutura de pastas, requirements.txt)
- [x] **1.2** Configurar conexão com MySQL (SQLAlchemy / pymysql)
- [x] **1.3** Criar migration inicial com todas as tabelas
- [x] **1.4** Implementar sistema de usuários
  - [x] Modelo `users` com bcrypt
  - [x] `POST /api/auth/login` → retorna JWT
  - [x] `POST /api/auth/register` (admin apenas)
  - [x] `GET /api/auth/me`
  - [x] `PUT /api/auth/password`
  - [x] Middleware de validação JWT
- [x] **1.5** CRUD de usuários (`GET /api/users`, `PUT /api/users/{id}`, `DELETE /api/users/{id}`)
- [x] **1.6** Seed do banco com usuário `admin` padrão
- [x] **1.7** Criar tabelas de inventário (`hosts`, `host_hardware`, `host_network`, etc.)
- [x] **1.8** Implementar `POST /api/hosts/checkin` (receber payload do collector)
- [x] **1.9** Implementar `GET /api/hosts` e `GET /api/hosts/{id}`
- [x] **1.10** Implementar endpoints de leitura (`/software`, `/licenses`, `/scans`, `/compliance`)
- [ ] **1.11** Testes unitários dos endpoints com `pytest` _(postergado)_

---

## Fase 2 — Interface Web

- [x] **2.1** Criar estrutura base do frontend
- [x] **2.2** Design system próprio (sem Bootstrap) + Chart.js via CDN
- [x] **2.3** Página de **Login** (`/login.html`)
  - [x] Formulário email + senha
  - [x] Armazenar JWT no `localStorage`
  - [x] Redirecionar para dashboard após login
  - [x] Interceptar 401 para redirecionar ao login
- [x] **2.4** **Layout base** (sidebar, header, logout)
  - [x] Sidebar com navegação por seções
  - [x] Nome do usuário e avatar com iniciais
  - [x] Proteção de rota (redirecionar se não logado)
- [x] **2.5** Página **Dashboard** (`/dashboard.html`)
  - [x] Cards: total hosts, online, offline, alertas de licença
  - [x] Gráfico doughnut: hosts online/offline
  - [x] Gráfico bar: compliance de licenças
  - [x] Gráfico bar: top 8 softwares
  - [x] Gráfico line: coletas por dia
  - [x] Últimas coletas (tabela)
- [x] **2.6** Página **Hosts** (`/hosts.html`)
  - [x] Tabela com hostname, domínio, status, último contato
  - [x] Busca textual em tempo real
  - [x] Link para detalhe do host
- [x] **2.7** Página **Detalhe do Host** (`/host-detail.html`)
  - [x] Hardware, Rede, Discos, Licenças, Software
  - [x] Barra de uso de disco com cores dinâmicas
  - [x] Cabeçalho com hostname, status, último contato
- [x] **2.8** Página **Licenças** (`/licenses.html`)
  - [x] Tabela: hosts × status de licença
  - [x] Filtro por status e produto
  - [x] Badges visuais (verde/vermelho/amarelo)
- [x] **2.9** Página **Softwares** (`/software.html`)
  - [x] Tabela: nome, versão, publisher, qtd instalações
  - [x] Busca textual
- [x] **2.10** Página **Usuários** (`/users.html`) — admin apenas
  - [x] Lista de usuários com role e status
  - [x] Modal para criar novo usuário
  - [x] Ativar/desativar
  - [x] Oculta para roles não-admin no menu

---

## Fase 3 — Collector Python

- [x] **3.1** Inicializar projeto Python (venv, requirements.txt)
- [x] **3.2** Configurar conexão LDAP (`ldap3`)
  - [x] Ler configuração do AD (server, bind DN, password, base DN)
  - [x] Consultar computadores em OUs específicas
  - [x] Retornar lista de hostnames
- [x] **3.3** Implementar teste de conectividade
  - [x] ICMP ping antes de tentar WinRM
  - [x] Timeout configurável (ex.: 3s)
  - [x] Marcar hosts offline no log
- [x] **3.4** Implementar módulo WinRM
  - [x] Conexão via `pywinrm` (HTTP ou HTTPS)
  - [x] Script PowerShell para coletar **hardware**
  - [x] Script PowerShell para coletar **discos**
  - [x] Script PowerShell para coletar **rede**
  - [x] Script PowerShell para coletar **Windows / licença**
  - [x] Script PowerShell para coletar **Office / licença**
  - [x] Script PowerShell para coletar **softwares instalados**
  - [x] Consolidar tudo em um JSON por host
- [x] **3.5** Implementar envio à API
  - [x] `POST /api/hosts/checkin` com autenticação (token do collector)
  - [x] Retry em caso de falha (ex.: 3 tentativas)
  - [x] Log de sucesso/erro por host
- [x] **3.6** Logging estruturado
  - [x] Log por host (coletado, offline, erro WinRM, erro API)
  - [x] Resumo ao final da execução (X coletados, Y offline, Z erros)
- [x] **3.7** Agendamento
  - [x] Execução via `schedule` (loop infinito)
  - [x] Intervalo configurável (ex.: a cada 6h)
- [x] **3.8** Arquivo de configuração
  - [x] `config.yaml` com: AD, WinRM, API URL, intervalos
  - [x] Suporte a env COLLECTOR_CONFIG e --config

---

## Fase 4 — Integração e Testes

- [x] **4.1** Teste integrado: collector → API → banco → frontend (seed demo 20 hosts OK)
- [ ] **4.2** Testar com pelo menos 5 máquinas reais do domínio
- [ ] **4.3** Validar dados: conferir se o que o PowerShell retorna bate com o que aparece no frontend
- [ ] **4.4** Ajustar timeouts e tratamento de erros do WinRM
- [x] **4.5** Docker Compose para subir tudo (MySQL + FastAPI + Frontend + Collector)
- [x] **4.6** Documentação de instalação e uso (`README.md`)

---

## Pós-MVP

- [ ] Agendamento de coletas pelo frontend (admin)
- [ ] Alertas por e-mail (host offline, licença expirada)
- [ ] Exportação de relatórios (PDF / Excel)
- [ ] Suporte a hosts Linux (via SSH)
- [ ] API de integração com GLPI / ServiceDesk
- [ ] Dashboard com gráficos de tendência (histórico de RAM, disco)
- [ ] Notificações in-app (bell icon com alertas recentes)
