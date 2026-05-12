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
- [ ] **0.6** Testar `docker compose up` — MySQL acessível e healthy

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
- [ ] **1.11** Testes unitários dos endpoints com `pytest`

---

## Fase 2 — Interface Web

- [ ] **2.1** Criar estrutura base do frontend (pasta `static/` ou `templates/`)
- [ ] **2.2** Configurar Bootstrap 5 + DataTables + Chart.js via CDN
- [ ] **2.3** Página de **Login** (`/login.html`)
  - [ ] Formulário email + senha
  - [ ] Armazenar JWT no `localStorage`
  - [ ] Redirecionar para dashboard após login
  - [ ] Interceptar 401 para redirecionar ao login
- [ ] **2.4** **Layout base** (navbar, sidebar, logout)
  - [ ] Navbar com nome do usuário e botão sair
  - [ ] Sidebar com links para cada página
  - [ ] Proteção de rota (redirecionar se não logado)
- [ ] **2.5** Página **Dashboard** (`/dashboard.html`)
  - [ ] Cards: total hosts, online, offline, alertas de licença
  - [ ] Gráfico: hosts online/offline ao longo do tempo
  - [ ] Gráfico: top softwares instalados
  - [ ] Últimas coletas (tabela resumo)
- [ ] **2.6** Página **Hosts** (`/hosts.html`)
  - [ ] DataTable com colunas: hostname, IP, OS, status, última coleta
  - [ ] Filtros: online/offline, domínio, busca textual
  - [ ] Link para detalhe do host
- [ ] **2.7** Página **Detalhe do Host** (`/host-detail.html`)
  - [ ] Abas: Hardware / Rede / Discos / Software / Licenças
  - [ ] Cabeçalho com hostname, status, última coleta
  - [ ] Cada aba com tabela ou cards dos dados
- [ ] **2.8** Página **Licenças** (`/licenses.html`)
  - [ ] Tabela: hosts × status de licença Windows/Office
  - [ ] Filtro por status (Licensed, Unlicensed, Notification)
  - [ ] Indicadores visuais (verde/amarelo/vermelho)
- [ ] **2.9** Página **Softwares** (`/software.html`)
  - [ ] DataTable: nome, versão, publisher, qtd instalações
  - [ ] Agrupamento por software (expandir para ver em quais hosts)
  - [ ] Filtro e busca
- [ ] **2.10** Página **Usuários** (`/users.html`) — admin apenas
  - [ ] Lista de usuários com role e status
  - [ ] Criar novo usuário
  - [ ] Editar role / ativar-desativar
  - [ ] Oculta para roles não-admin no menu

---

## Fase 3 — Collector Python

- [ ] **3.1** Inicializar projeto Python (venv, requirements.txt)
- [ ] **3.2** Configurar conexão LDAP (`ldap3`)
  - [ ] Ler configuração do AD (server, bind DN, password, base DN)
  - [ ] Consultar computadores em OUs específicas
  - [ ] Retornar lista de hostnames
- [ ] **3.3** Implementar teste de conectividade
  - [ ] ICMP ping antes de tentar WinRM
  - [ ] Timeout configurável (ex.: 3s)
  - [ ] Marcar hosts offline no log
- [ ] **3.4** Implementar módulo WinRM
  - [ ] Conexão via `pywinrm` (HTTP ou HTTPS)
  - [ ] Script PowerShell para coletar **hardware**
  - [ ] Script PowerShell para coletar **discos**
  - [ ] Script PowerShell para coletar **rede**
  - [ ] Script PowerShell para coletar **Windows / licença**
  - [ ] Script PowerShell para coletar **Office / licença**
  - [ ] Script PowerShell para coletar **softwares instalados**
  - [ ] Consolidar tudo em um JSON por host
- [ ] **3.5** Implementar envio à API
  - [ ] `POST /api/hosts/checkin` com autenticação (token do collector)
  - [ ] Retry em caso de falha (ex.: 3 tentativas)
  - [ ] Log de sucesso/erro por host
- [ ] **3.6** Logging estruturado
  - [ ] Log por host (coletado, offline, erro WinRM, erro API)
  - [ ] Resumo ao final da execução (X coletados, Y offline, Z erros)
- [ ] **3.7** Agendamento
  - [ ] Execução via `schedule` ou cron do Linux
  - [ ] Intervalo configurável (ex.: a cada 6h)
- [ ] **3.8** Arquivo de configuração
  - [ ] `config.yaml` ou `.env` com: AD, WinRM, API URL, intervalos
  - [ ] Validação ao iniciar

---

## Fase 4 — Integração e Testes

- [ ] **4.1** Teste integrado: collector → API → banco → frontend
- [ ] **4.2** Testar com pelo menos 5 máquinas reais do domínio
- [ ] **4.3** Validar dados: conferir se o que o PowerShell retorna bate com o que aparece no frontend
- [ ] **4.4** Ajustar timeouts e tratamento de erros do WinRM
- [ ] **4.5** Docker Compose para subir tudo (MySQL + FastAPI + Frontend)
- [ ] **4.6** Documentação de instalação e uso (`README.md`)

---

## Pós-MVP

- [ ] Agendamento de coletas pelo frontend (admin)
- [ ] Alertas por e-mail (host offline, licença expirada)
- [ ] Exportação de relatórios (PDF / Excel)
- [ ] Suporte a hosts Linux (via SSH)
- [ ] API de integração com GLPI / ServiceDesk
- [ ] Dashboard com gráficos de tendência (histórico de RAM, disco)
- [ ] Notificações in-app (bell icon com alertas recentes)
