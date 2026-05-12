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

# O .env já vem com valores de desenvolvimento
# Ajuste as senhas para produção
```

### 2. Subir os containers

```bash
docker compose up -d --build
```

Isso sobe:
- **MySQL** → `localhost:3307`
- **Backend API** → `localhost:8091`
- **Frontend** → `localhost:8090`

### 3. Acessar

| URL | Descrição |
|---|---|
| http://localhost:8090/login.html | Login (admin) |
| http://localhost:8090/dashboard.html | Dashboard com gráficos |

**Credenciais padrão:**
- E-mail: `admin@tabularium.local`
- Senha: `admin123`

### 4. Popular com dados de demonstração (opcional)

```bash
pip install requests
python backend/scripts/seed_demo.py
```

Gera 20 hosts fictícios com hardware, software, licenças e rede.

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
└──────────┬───────────┘       └──────────────────┘
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

### Frontend
- **Login** — autenticação JWT
- **Dashboard** — 4 cards + 4 gráficos Chart.js + tabela de coletas
- **Hosts** — lista com busca + link para detalhe
- **Host Detail** — ficha completa (hardware, discos, rede, licenças, software)
- **Softwares** — inventário agrupado com busca
- **Licenças** — status com filtros por produto e status
- **Usuários** — CRUD completo (admin)
- **Configurações** — AD/LDAP, WinRM, agendamento (admin)
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
├── nginx.conf                  # Proxy reverso
├── .env                        # Variáveis de ambiente
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py             # FastAPI entrypoint
│   │   ├── core/
│   │   │   ├── config.py       # Settings (pydantic)
│   │   │   ├── database.py     # SQLAlchemy + modelos
│   │   │   └── security.py     # JWT + bcrypt
│   │   ├── api/
│   │   │   ├── auth.py         # Login, register, me
│   │   │   ├── users.py        # CRUD usuários
│   │   │   ├── hosts.py        # Checkin + leitura
│   │   │   ├── reports.py      # Software, licenças, scans
│   │   │   └── settings.py     # Configs + testes LDAP/WinRM
│   │   └── schemas/
│   │       └── schemas.py      # Pydantic models
│   └── scripts/
│       └── seed_demo.py        # Dados de demonstração
├── frontend/
│   ├── login.html
│   ├── dashboard.html
│   ├── hosts.html
│   ├── host-detail.html
│   ├── software.html
│   ├── licenses.html
│   ├── users.html
│   ├── settings.html
│   ├── css/
│   │   └── theme.css           # Design system
│   └── js/
│       ├── auth.js             # JWT + fetch wrapper
│       └── layout.js           # Sidebar + header
├── collector/
│   ├── Dockerfile
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

## Documentação

| Documento | Descrição |
|---|---|
| [docs/PLANO SISTEMA.md](docs/PLANO%20SISTEMA.md) | Arquitetura, modelos, endpoints, dados coletados |
| [docs/TAREFAS.md](docs/TAREFAS.md) | Checklist de tarefas (Fases 0–4 + pós-MVP) |
| [docs/PREPARACAO-WINDOWS.md](docs/PREPARACAO-WINDOWS.md) | Como preparar máquinas para o collector |

---

## Portas

| Serviço | Porta Host | Porta Container |
|---|---|---|
| MySQL | 3307 | 3306 |
| Backend API | 8091 | 8000 |
| Frontend (Nginx) | 8090 | 80 |

---

## Licença

Projeto interno — uso restrito.
