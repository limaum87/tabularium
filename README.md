# Tabularium

Sistema de inventário de máquinas Windows em domínio corporativo. Coleta dados via **WinRM** (sem agente), armazena em **MySQL** e apresenta em uma interface web com dashboards e gráficos.

---

## Stack

| Componente | Tecnologia |
|---|---|
| **Backend** | Python 3.12 + FastAPI |
| **Banco** | MySQL 8.0 |
| **Frontend** | HTML + JS + CSS (design system próprio) |
| **Collector** | Python (LDAP + WinRM + PowerShell) |
| **Infra** | Docker Compose + Nginx |

---

## Quick Start

### 1. Clonar e configurar

```bash
git clone git@github.com:limaum87/tabularium.git
cd tabularium

# Ajuste o .env com suas senhas e DNS corporativo
```

### 2. Configurar DNS (necessário para resolução de nomes internos)

No `.env`, preencha o DNS da sua rede (IP do controlador de domínio AD):

```env
DNS_SERVERS=10.0.0.1
DNS_SEARCH=empresa.local
```

Isso permite que os containers resolvam nomes como `WIR-ADM-01`. Também é possível configurar pela UI em **Configurações → DNS**.

### 3. Subir os containers

```bash
docker compose up -d --build
```

Isso sobe:
- **MySQL** → `localhost:3307`
- **Backend API** → `localhost:8091`
- **Frontend** → `localhost:8090`

### 4. Acessar

| URL | Descrição |
|---|---|
| http://localhost:8090/login.html | Login (admin) |
| http://localhost:8090/dashboard.html | Dashboard com gráficos |
| http://localhost:8090/settings.html | Configurações (AD, WinRM, DNS) |

**Credenciais padrão:**
- E-mail: `admin@tabularium.local`
- Senha: `admin123`

### 5. Configurar

Na tela de **Configurações**, preencha:

1. **Active Directory** — servidor LDAP, bind DN, senha, base DN
2. **WinRM** — usuário e senha (formato `DOMINIO\usuario`)
3. **DNS** — IP do DNS corporativo + domínio de busca (ex: `empresa.local`)
4. Clique em **Descobrir Máquinas** para importar hosts do AD

### 6. Popular com dados de demonstração (opcional)

```bash
pip install requests
python backend/scripts/seed_demo.py
```

---

## Arquitetura

```
┌──────────────────────┐
│   Active Directory   │
└──────────┬───────────┘
           │ LDAP (lista computadores)
           ▼
┌──────────────────────┐
│  Python Collector    │──── WinRM ───▶ Hosts Windows
└──────────┬───────────┘               (sem agente)
           │ POST /api/hosts/checkin
           ▼
┌──────────────────────┐       ┌──────────────────┐
│   FastAPI Backend    │──────▶│     MySQL 8.0    │
│  + Coleta Manual     │       └──────────────────┘
│  + Ping Automático   │
│  + Ativação WinRM    │
└──────────┬───────────┘
           │ REST API + JWT
           ▼
┌──────────────────────┐
│  Frontend (Nginx)    │
│  Charts + DataTables │
└──────────────────────┘
```

---

## Funcionalidades

### Backend (API)
- Autenticação JWT com roles (admin, operator, viewer)
- CRUD de usuários
- Checkin de hosts (recebe dados do collector)
- Endpoints de leitura: hosts, software, licenças, scans, compliance
- Configurações dinâmicas salvas no banco (tabela `settings`)
- Teste de conexão LDAP e WinRM via API
- Seed automático do usuário admin
- **Coleta manual** de dados via WinRM (por host)
- **Ping automático** a cada 5 minutos em todos os hosts
- **Ativação remota de WinRM** via impacket-psexec
- **DNS dinâmico** — configura DNS do container pela UI, preservando DNS interno do Docker
- Migration automática de novas colunas

### Frontend
- **Login** — autenticação JWT
- **Dashboard** — 4 cards + 4 gráficos Chart.js + tabela de coletas
- **Hosts** — lista com busca, dois status (📡 Ping + 🔌 WinRM), menu de ações por host
- **Host Detail** — ficha completa (hardware, discos, rede, licenças, software) + barra de ações
- **Softwares** — inventário agrupado com busca
- **Licenças** — status com filtros por produto e status
- **Usuários** — CRUD completo (admin)
- **Configurações** — AD/LDAP, WinRM, DNS, agendamento (admin)
- **Discovery** — modal para descobrir e importar máquinas do AD
- Design system industrial (dark mode, amber/gold)

### Collector
- Descoberta de hosts via LDAP (Active Directory)
- Teste de conectividade ICMP ping
- Coleta via WinRM + PowerShell (sem agente nas máquinas)
- 6 scripts PS: hardware, discos, rede, Windows license, Office license, software
- Envio à API com retry (3 tentativas)
- Logging estruturado com arquivo rotativo
- Modo agendado (a cada X horas)
- Lê configurações da API (centralizado pelo frontend)

---

## Ações por Host

Cada host possui um menu de ações acessível pela lista ou pela página de detalhes:

| Ação | Descrição | Tempo estimado |
|---|---|---|
| 📡 **Testar Ping** | Verifica conectividade ICMP, mostra latência e IP resolvido | ~5s |
| 🔌 **Testar WinRM** | Testa conexão WinRM com credenciais das Settings | ~10s |
| ⚡ **Ativar WinRM** | Habilita WinRM remotamente via impacket-psexec (precisa de porta 445 aberta) | ~30s |
| 🔄 **Coletar Dados** | Coleta completa: hardware, discos, rede, licenças e software | ~1-2 min |

### Status duplo

Cada host tem dois status independentes:

| Status | Significado | Atualizado por |
|---|---|---|
| 📡 **Ping** | Responde a ICMP ping | Automático a cada 5 min + ping sweep manual |
| 🔌 **WinRM** | Conexão WinRM funcionando | Coleta manual, teste manual, collector |

### Ping Sweep

- **Automático**: background task faz ping em todos os hosts a cada 5 minutos
- **Manual**: botão "📡 Ping Sweep" na lista de hosts força verificação imediata

---

## DNS e Resolução de Nomes

Containers Docker não resolvem nomes internos da rede corporativa por padrão. O Tabularium resolve isso de duas formas:

### Via variável de ambiente (.env)
```env
DNS_SERVERS=192.168.0.152
DNS_SEARCH=wiretec.local
```

### Via interface web
Em **Configurações → DNS**, preencha o IP do DNS corporativo e o domínio de busca. O sistema aplica no `/etc/resolv.conf` do container em runtime, preservando o DNS interno do Docker (`127.0.0.11`).

O endpoint de teste de DNS (`POST /api/settings/test-dns`) permite verificar se um hostname resolve corretamente.

---

## Preparação das Máquinas Windows

Cada máquina precisa ter **WinRM habilitado**. Veja instruções completas em:

📖 **[docs/PREPARACAO-WINDOWS.md](docs/PREPARACAO-WINDOWS.md)**

Resumo rápido (rodar como Admin no PowerShell da máquina):

```powershell
Enable-PSRemoting -Force -SkipNetworkProfileCheck
Set-Item WSMan:\localhost\Service\Auth\Negotiate -Value $true
Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value $true
Enable-NetFirewallRule -Name "WINRM-HTTP-In-TCP-PUBLIC"
Restart-Service WinRM -Force
```

Ou use a ação **⚡ Ativar WinRM** na interface do Tabularium (requer porta SMB 445 aberta e credenciais de admin).

---

## Subir o Collector

O collector roda separadamente (precisa de acesso à rede das máquinas Windows):

```bash
# Modo agendado (via Docker)
docker compose --profile collector up -d collector

# Ou manual (sem Docker)
cd collector
pip install -r requirements.txt
python -m collector.main --schedule
```

O collector lê as configurações da API (AD, WinRM, agendamento) — configuradas pelo admin na tela de **Configurações** do frontend.

---

## Estrutura do Projeto

```
tabularium/
├── docker-compose.yml          # Infra completa
├── .drone.yml                  # CI/CD (Drone)
├── nginx.conf                  # Proxy reverso (timeout 180s)
├── .env                        # Variáveis de ambiente
├── backend/
│   ├── Dockerfile
│   ├── entrypoint.sh           # Aplica DNS no startup
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py             # FastAPI + lifespan + ping sweep + migrations
│   │   ├── core/
│   │   │   ├── config.py       # Settings (pydantic)
│   │   │   ├── database.py     # SQLAlchemy + modelos (Host, Setting, etc)
│   │   │   └── security.py     # JWT + bcrypt
│   │   ├── api/
│   │   │   ├── auth.py         # Login, register, me
│   │   │   ├── users.py        # CRUD usuários
│   │   │   ├── hosts.py        # Checkin + ações (ping, winrm, collect)
│   │   │   ├── reports.py      # Software, licenças, scans
│   │   │   ├── settings.py     # Configs + testes + DNS apply/test
│   │   │   └── discovery.py    # LDAP discovery + import
│   │   ├── collector/
│   │   │   └── winrm_collect.py # Coleta manual via WinRM
│   │   └── schemas/
│   │       └── schemas.py      # Pydantic models
│   └── scripts/
│       └── seed_demo.py        # Dados de demonstração
├── frontend/
│   ├── login.html
│   ├── dashboard.html
│   ├── hosts.html              # Lista + ações + discovery modal + ping sweep
│   ├── host-detail.html        # Ficha completa + barra de ações
│   ├── software.html
│   ├── licenses.html
│   ├── users.html
│   ├── settings.html           # AD, WinRM, DNS, Agendamento
│   ├── css/
│   │   └── theme.css           # Design system
│   └── js/
│       ├── auth.js             # JWT + fetch wrapper
│       └── layout.js           # Sidebar + header
├── collector/
│   ├── Dockerfile
│   ├── entrypoint.sh           # Aplica DNS no startup
│   ├── requirements.txt
│   ├── config.yaml             # Fallback local
│   ├── main.py                 # Orquestrador
│   ├── config_reader.py        # API > YAML
│   ├── logger.py               # Logging rotativo
│   ├── ldap_discovery.py       # AD via ldap3
│   ├── ping_check.py           # ICMP ping
│   ├── winrm_collector.py      # Coleta via WinRM
│   ├── api_client.py           # Envio à API
│   └── scripts/
│       └── powershell_scripts.py  # 6 scripts PS
└── docs/
    ├── PLANO SISTEMA.md        # Referência de arquitetura
    ├── TAREFAS.md              # Checklist de desenvolvimento
    └── PREPARACAO-WINDOWS.md   # Guia de preparação
```

---

## API Reference (Endpoints principais)

### Autenticação
| Método | Rota | Descrição |
|---|---|---|
| POST | `/api/auth/login` | Login (retorna JWT) |
| GET | `/api/auth/me` | Dados do usuário logado |

### Hosts
| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/hosts` | Lista hosts ativos |
| GET | `/api/hosts/{id}` | Detalhe completo do host |
| POST | `/api/hosts/checkin` | Checkin do collector |
| POST | `/api/hosts/ping-sweep` | Ping em todos os hosts |
| POST | `/api/hosts/{id}/action/ping` | Testa ping com diagnóstico |
| POST | `/api/hosts/{id}/action/test-winrm` | Testa conexão WinRM |
| POST | `/api/hosts/{id}/action/enable-winrm` | Ativa WinRM via PSExec |
| POST | `/api/hosts/{id}/action/collect` | Coleta manual completa |

### Configurações
| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/settings` | Lista configs por categoria |
| PUT | `/api/settings` | Salva lote de configs |
| POST | `/api/settings/test-ldap` | Testa conexão LDAP |
| POST | `/api/settings/test-winrm` | Testa conexão WinRM |
| POST | `/api/settings/test-dns` | Testa resolução DNS |
| POST | `/api/settings/apply-dns` | Aplica DNS no container |

### Discovery
| Método | Rota | Descrição |
|---|---|---|
| POST | `/api/discovery/run` | Descobre máquinas no AD |
| POST | `/api/discovery/import` | Importa máquinas selecionadas |

---

## Portas

| Serviço | Porta Host | Porta Container |
|---|---|---|
| MySQL | 3307 | 3306 |
| Backend API | 8091 | 8000 |
| Frontend (Nginx) | 8090 | 80 |

---

## CI/CD

O projeto usa **Drone CI** (`.drone.yml`) para deploy automático:
- Trigger: push na branch `main`
- Runner: node `wir-hub` (192.168.0.159)
- Steps: `git pull` → `docker compose up -d --build` → `image prune`

---

## Licença

Projeto interno — uso restrito.
