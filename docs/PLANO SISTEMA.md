# Plano do Sistema — Tabularium

> **Versão:** 2.0 — MVP
> **Última atualização:** 2026-05-12

---

## 1. Objetivo do MVP

Sistema de inventário de máquinas Windows em domínio corporativo, coletando dados via **WinRM** e consolidando tudo em uma interface web autenticada para consulta, auditoria e compliance de licenças.

### Dados coletados por host

| Categoria           | Informações principales                                                      |
| ------------------- | ----------------------------------------------------------------------------- |
| **Hardware**        | Fabricante, modelo, serial, CPU, RAM, BIOS, último boot                      |
| **Windows**         | Edição, versão, build, canal de licença, status, chave parcial               |
| **Office**          | Versão, canal, status da licença, chave parcial (quando detectável)          |
| **Softwares**       | Nome, versão, editora, data de instalação, local                             |
| **Rede**            | IP, MAC, gateway, DNS, nome do adaptador                                     |
| **Discos**          | Drive, total, livre, filesystem                                              |
| **Status / Histórico** | Online/offline, timestamp de cada coleta, erros                          |

---

## 2. Arquitetura

```
┌──────────────────────┐
│   Active Directory   │
└──────────┬───────────┘
           │ LDAP (consulta OUs / computadores)
           ▼
┌──────────────────────┐
│  Python Collector    │──── WinRM ───▶ Hosts Windows
└──────────┬───────────┘◀─── JSON ────── (PowerShell remoto)
           │ HTTP POST (check-in)
           ▼
┌──────────────────────┐       ┌──────────────────┐
│   FastAPI Backend    │──────▶│     MySQL        │
└──────────┬───────────┘       └──────────────────┘
           │ REST API
           ▼
┌──────────────────────────────────┐
│  Frontend HTML + JS              │
│  (Bootstrap / Datatables / JWT)  │
└──────────────────────────────────┘
```

---

## 3. Componentes

### 3.1 Collector Python

Responsável por:

- Buscar máquinas no domínio via **LDAP** (`ldap3`)
- Testar conectividade (ICMP / porta WinRM 5985-5986)
- Conectar via **WinRM** (`pywinrm`) e executar scripts PowerShell remotos
- Receber o resultado em **JSON**
- Enviar para a API (`POST /api/hosts/checkin`)

**Bibliotecas prováveis:**

| Biblioteca   | Uso                            |
| ------------ | ------------------------------ |
| `pywinrm`    | Conexão WinRM / execução PS    |
| `ldap3`      | Consulta ao Active Directory   |
| `requests`   | Chamadas HTTP para a API       |
| `schedule`   | Agendamento de coletas         |

### 3.2 Backend FastAPI

**Endpoints planejados:**

| Método  | Rota                     | Descrição                                  | Auth     |
| ------- | ------------------------ | ------------------------------------------ | -------- |
| `POST`  | `/api/auth/login`        | Login (email + senha → JWT)                | Pública  |
| `POST`  | `/api/auth/register`     | Registro de novo usuário (admin apenas)    | Admin    |
| `GET`   | `/api/auth/me`           | Dados do usuário logado                    | User+    |
| `PUT`   | `/api/auth/password`     | Alterar própria senha                      | User+    |
| `GET`   | `/api/users`             | Lista usuários do sistema                  | Admin    |
| `PUT`   | `/api/users/{id}`        | Editar usuário (role, ativo)               | Admin    |
| `DELETE`| `/api/users/{id}`        | Desativar usuário                          | Admin    |
| `POST`  | `/api/hosts/checkin`     | Recebe dados coletados de um host          | Collector|
| `GET`   | `/api/hosts`             | Lista hosts (com filtros/paginação)        | User+    |
| `GET`   | `/api/hosts/{id}`        | Detalhe completo de um host                | User+    |
| `GET`   | `/api/software`          | Lista softwares instalados (agrupado)      | User+    |
| `GET`   | `/api/licenses`          | Status de licenças Windows/Office           | User+    |
| `GET`   | `/api/scans`             | Histórico de coletas                        | User+    |
| `GET`   | `/api/compliance`        | Dashboard de conformidade de licenças      | User+    |

### 3.3 Banco de Dados — MySQL

**Tabelas do sistema:**

| Tabela               | Descrição                                            |
| -------------------- | ---------------------------------------------------- |
| `users`              | Usuários do sistema (login, senha hash, role, ativo) |
| `hosts`              | Dados base do host (hostname, domínio, status)       |
| `host_hardware`      | CPU, RAM, fabricante, modelo, serial, BIOS           |
| `host_network`       | Adaptadores, IP, MAC, gateway, DNS                   |
| `host_disks`         | Volumes, tamanho, espaço livre                       |
| `host_licenses`      | Licenças Windows e Office detectadas                 |
| `host_software`      | Softwares instalados                                 |
| `scan_history`       | Log de cada coleta (timestamp, sucesso/erro)         |
| `purchased_licenses` | Licenças adquiridas (controle de compliance)         |

### 3.4 Sistema de Usuários e Autenticação

#### Tabela `users`

| Campo         | Tipo           | Descrição                             |
| ------------- | -------------- | ------------------------------------- |
| `id`          | INT (PK, AI)   | Identificador único                   |
| `name`        | VARCHAR(150)   | Nome completo                         |
| `email`       | VARCHAR(255)   | E-mail (login único)                  |
| `password`    | VARCHAR(255)   | Hash bcrypt da senha                  |
| `role`        | ENUM           | `admin`, `operator`, `viewer`         |
| `is_active`   | BOOLEAN        | Conta ativa/inativa                   |
| `created_at`  | DATETIME       | Data de criação                       |
| `updated_at`  | DATETIME       | Última atualização                    |

#### Roles e permissões

| Ação                              | `admin` | `operator` | `viewer` |
| --------------------------------- | :-----: | :--------: | :------: |
| Gerenciar usuários                | ✅      | ❌         | ❌       |
| Visualizar hosts / inventário     | ✅      | ✅         | ✅       |
| Visualizar licenças / compliance  | ✅      | ✅         | ✅       |
| Exportar relatórios               | ✅      | ✅         | ❌       |
| Disparar coleta manual            | ✅      | ✅         | ❌       |
| Configurar Collector              | ✅      | ❌         | ❌       |

#### Fluxo de autenticação

```
┌──────────┐   POST /api/auth/login    ┌──────────────┐
│ Frontend │ ────────────────────────▶ │  FastAPI     │
│ (login)  │ ◀──────────────────────── │  (valida)    │
└──────────┘   JWT Token               └──────────────┘
     │
     │  Authorization: Bearer <token>
     ▼
┌──────────┐   GET /api/hosts          ┌──────────────┐
│ Frontend │ ────────────────────────▶ │  FastAPI     │
│ (dados)  │ ◀──────────────────────── │  (valida JWT)│
└──────────┘   JSON com dados          └──────────────┘
```

- Login via **email + senha**
- Senha armazenada com **bcrypt**
- Token **JWT** com expiração (ex.: 8h)
- Middleware FastAPI valida token em toda rota protegida
- Frontend armazena token no `localStorage` e envia no header `Authorization`

---

## 4. Detalhamento dos Dados Coletados

### 4.1 Hardware

| Campo          | Exemplo / Observação            |
| -------------- | ------------------------------- |
| `hostname`     | `DESKTOP-FINANCE-01`            |
| `domain`       | `empresa.local`                 |
| `manufacturer` | `Dell Inc.`                     |
| `model`        | `OptiPlex 7090`                 |
| `serial`       | Número de série do equipamento  |
| `cpu`          | Modelo do processador           |
| `ram_gb`       | Memória em GB                   |
| `bios_version` | Versão da BIOS                  |
| `last_boot`    | Data/hora do último boot        |

### 4.2 Discos

| Campo        | Observação                   |
| ------------ | ---------------------------- |
| `drive`      | Letra (ex.: `C:`)            |
| `total_gb`   | Capacidade total             |
| `free_gb`    | Espaço livre                 |
| `filesystem` | NTFS, ReFS, etc.             |

### 4.3 Rede

| Campo          | Observação                      |
| -------------- | ------------------------------- |
| `ip`           | Endereço IPv4                   |
| `mac`          | MAC address                     |
| `gateway`      | Gateway padrão                  |
| `dns`          | Servidores DNS                  |
| `adapter_name` | Nome do adaptador de rede       |

### 4.4 Windows

| Campo              | Observação                                   |
| ------------------ | -------------------------------------------- |
| `edition`          | Pro, Enterprise, etc.                        |
| `version`          | 22H2, 23H2 …                                 |
| `build`            | Build number                                 |
| `license_channel`  | KMS, MAK, OEM, Retail                        |
| `license_status`   | Licensed / Unlicensed / Notification         |
| `partial_product_key` | Últimos 5 caracteres da chave             |
| `oem_key_found`    | Chave OEM na BIOS (sim/não)                  |

### 4.5 Office

| Campo              | Observação                                   |
| ------------------ | -------------------------------------------- |
| `installed`        | Booleano                                     |
| `version`          | 2016, 2019, 365 …                            |
| `channel`          | Current, Monthly Enterprise, etc.            |
| `license_status`   | Ativo / expirado / trial                     |
| `partial_product_key` | Últimos 5 caracteres                      |
| `detection_method` | WMI, Registry, Office DEPLOYR                |

### 4.6 Software

| Campo              | Observação                            |
| ------------------ | ------------------------------------- |
| `name`             | Nome do software                      |
| `version`          | Versão instalada                      |
| `publisher`        | Editora / fabricante                  |
| `install_date`     | Data de instalação                    |
| `install_location` | Caminho de instalação                 |

---

## 5. Frontend

### Páginas

| Página              | Rota                  | Função                                              | Auth  |
| ------------------- | --------------------- | --------------------------------------------------- | ----- |
| Login               | `/login.html`         | Autenticação do usuário                             | —     |
| Dashboard           | `/dashboard.html`     | Visão geral: hosts online, alertas, compliance      | User+ |
| Hosts               | `/hosts.html`         | Lista de todos os hosts com filtros                 | User+ |
| Detalhe do Host     | `/host-detail.html`   | Ficha completa de um host                           | User+ |
| Licenças            | `/licenses.html`      | Status de licenças e compliance                     | User+ |
| Softwares           | `/software.html`      | Inventário de software (agrupado)                   | User+ |
| Usuários            | `/users.html`         | Gerenciar usuários do sistema                       | Admin |

### Stack do Frontend

- **Bootstrap 5** — layout responsivo
- **DataTables** — tabelas com busca, ordenação e paginação
- **Chart.js** — gráficos no dashboard
- **fetch()** — chamadas REST à API FastAPI
- **JWT** — autenticação via token no header `Authorization`

---

## 6. Execução do Collector

```
┌────────────┐    ┌────────────┐    ┌────────────┐    ┌────────────┐
│ 1. LDAP    │───▶│ 2. Teste   │───▶│ 3. WinRM   │───▶│ 4. Envia   │
│    Scan    │    │    ICMP    │    │   + PS     │    │    API     │
│ (busca     │    │ (online?)  │    │ (coleta    │    │ (POST      │
│  OUs/PCs)  │    │            │    │  dados)    │    │  checkin)  │
└────────────┘    └────────────┘    └────────────┘    └────────────┘
```

1. **LDAP Scan** — consulta OUs do AD para obter lista de computadores
2. **Teste ICMP** — verifica se cada host está online antes de tentar WinRM
3. **WinRM + PowerShell** — executa script remoto e recebe JSON com os dados
4. **Envio à API** — faz `POST /api/hosts/checkin` com o payload completo

---

## 7. Roadmap de Desenvolvimento

### Fase 1 — Fundação (Backend + Banco)

> **Objetivo:** API funcionando com banco MySQL, pronta para receber dados.

- [ ] **1.1** Inicializar projeto FastAPI (estrutura de pastas, requirements.txt)
- [ ] **1.2** Configurar conexão com MySQL (SQLAlchemy / pymysql)
- [ ] **1.3** Criar migration inicial com todas as tabelas
- [ ] **1.4** Implementar sistema de usuários
  - [ ] Modelo `users` com bcrypt
  - [ ] `POST /api/auth/login` → retorna JWT
  - [ ] `POST /api/auth/register` (admin apenas)
  - [ ] `GET /api/auth/me`
  - [ ] `PUT /api/auth/password`
  - [ ] Middleware de validação JWT
- [ ] **1.5** CRUD de usuários (`GET /api/users`, `PUT /api/users/{id}`, `DELETE /api/users/{id}`)
- [ ] **1.6** Seed do banco com usuário `admin` padrão
- [ ] **1.7** Criar tabelas de inventário (`hosts`, `host_hardware`, `host_network`, etc.)
- [ ] **1.8** Implementar `POST /api/hosts/checkin` (receber payload do collector)
- [ ] **1.9** Implementar `GET /api/hosts` e `GET /api/hosts/{id}`
- [ ] **1.10** Implementar endpoints de leitura (`/software`, `/licenses`, `/scans`, `/compliance`)
- [ ] **1.11** Testes unitários dos endpoints com `pytest`

### Fase 2 — Interface Web

> **Objetivo:** Frontend funcional com autenticação e visualização dos dados.

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

### Fase 3 — Collector Python

> **Objetivo:** Coleta automática de dados do AD via WinRM e envio para a API.

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

### Fase 4 — Integração e Testes

> **Objetivo:** Tudo funcionando de ponta a ponta, pronto para uso real.

- [ ] **4.1** Teste integrado: collector → API → banco → frontend
- [ ] **4.2** Testar com pelo menos 5 máquinas reais do domínio
- [ ] **4.3** Validar dados: conferir se o que o PowerShell retorna bate com o que aparece no frontend
- [ ] **4.4** Ajustar timeouts e tratamento de erros do WinRM
- [ ] **4.5** Docker Compose para subir tudo (MySQL + FastAPI + Frontend)
- [ ] **4.6** Documentação de instalação e uso (`README.md`)

---

## 8. Próximos Passos (pós-MVP)

- [ ] Agendamento de coletas pelo frontend (admin)
- [ ] Alertas por e-mail (host offline, licença expirada)
- [ ] Exportação de relatórios (PDF / Excel)
- [ ] Suporte a hosts Linux (via SSH)
- [ ] API de integração com GLPI / ServiceDesk
- [ ] Dashboard com gráficos de tendência (histórico de RAM, disco)
- [ ] Notificações in-app (bell icon com alertas recentes)
