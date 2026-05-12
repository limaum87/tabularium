# Preparação das Máquinas Windows

> **Obrigatório** para que o Collector consiga acessar cada host via WinRM.

---

## O que o Collector precisa

O Collector roda em um **servidor central** e acessa as máquinas Windows **remotamente via WinRM** (Windows Remote Management). Não é necessário instalar nenhum agente nas máquinas.

### Requisitos por máquina

| Requisito | Como verificar |
|---|---|
| WinRM habilitado | `winrm quickconfig` |
| WinRM escutando na 5985/5986 | `winrm enumerate winrm/config/listener` |
| Firewall liberado | Porta 5985 (HTTP) ou 5986 (HTTPS) |
| Conta de serviço no AD | Com permissões de administrador local |

---

## Opção 1 — Script PowerShell (recomendado)

Rode este script em cada máquina (via GPO, psexec, ou manualmente):

```powershell
# ============================================================
# Tabularium — Preparação do WinRM
# Rode como Administrador
# ============================================================

# 1. Habilita WinRM
Enable-PSRemoting -Force -SkipNetworkProfileCheck

# 2. Configura WinRM para receber conexões
Set-Item WSMan:\localhost\Service\Auth\Basic -Value $false
Set-Item WSMan:\localhost\Service\Auth\Negotiate -Value $true
Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value $true

# 3. Libera firewall para WinRM
Enable-NetFirewallRule -Name "WINRM-HTTP-In-TCP-PUBLIC" -ErrorAction SilentlyContinue
Enable-NetFirewallRule -Name "WINRM-HTTP-In-TCP" -ErrorAction SilentlyContinue

# 4. Reinicia o serviço
Restart-Service WinRM -Force

# 5. Verifica
$listener = Get-ChildItem WSMan:\localhost\Listener
if ($listener) {
    Write-Output "✓ WinRM configurado com sucesso"
    winrm enumerate winrm/config/listener
} else {
    Write-Output "✗ Falha ao configurar WinRM"
}
```

### Via GPO (para todas as máquinas do domínio)

1. **Group Policy Management** → criar GPO vinculada à OU dos computadores
2. **Computer Configuration** → **Policies** → **Windows Settings** → **Scripts** → **Startup**
3. Adicionar o script PowerShell acima
4. Ou via **Preferences** → **Registry** / **Windows Firewall Rules** para habilitar WinRM

---

## Opção 2 — Comandos manuais

Em cada máquina, abra PowerShell como Administrador e execute:

```powershell
# Habilita WinRM
winrm quickconfig -quiet

# Permite autenticação NTLM (usada pelo collector)
winrm set winrm/config/service/auth '@{Negotiate="true"}'

# Permite conexões HTTP (sem SSL)
winrm set winrm/config/service '@{AllowUnencrypted="true"}'

# Verifica status
winrm enumerate winrm/config/listener
```

---

## Conta de Serviço

Crie uma conta no Active Directory para o Collector:

```
Nome:       tabularium
Tipo:       User
Grupo:      Domain Admins (ou configurar delegação)
Descrição:  Conta de serviço para coleta de inventário
```

### Permissões mínimas (sem Domain Admins)

Se preferir não usar Domain Admins, a conta precisa de:

1. **Administrador local** em cada máquina Windows
   - Via GPO: **Restricted Groups** ou **Local Users and Groups**
   - Adicionar `EMPRESA\tabularium` ao grupo `Administrators` local

2. **Leitura do AD** (LDAP)
   - Permissão de `Read` na OU dos computadores

---

## Verificação

Após preparar as máquinas, teste pelo Collector:

1. Acesse **Configurações** no frontend
2. Configure AD e WinRM
3. Clique **Salvar & Testar Conexão** (LDAP) — deve listar computadores
4. Informe um hostname e clique **Testar WinRM** — deve retornar `OK`

Ou manualmente pelo servidor do Collector:

```bash
# Teste LDAP
python -c "
from collector.ldap_discovery import discover_hosts
from collector.config_reader import load_config
cfg = load_config()
hosts = discover_hosts(cfg)
for h in hosts[:10]:
    print(h['hostname'])
"

# Teste WinRM com um host
python -c "
from collector.winrm_collector import WinRMCollector
from collector.config_reader import load_config
cfg = load_config()
c = WinRMCollector(cfg)
data = c.collect('HOSTNAME-DA-MAQUINA')
print(data)
"
```

---

## Troubleshooting

| Problema | Solução |
|---|---|
| `Access Denied` no WinRM | Conta não é admin local na máquina |
| `WinRM cannot complete the operation` | WinRM não habilitado ou firewall bloqueando |
| `The connection to the specified remote host was refused` | Porta 5985 bloqueada no firewall |
| LDAP retorna 0 computadores | Verificar base_dn, bind_dn e senha |
| Timeout no ping mas host está online | ICMP bloqueado — desabilitar ping no config e tentar direto via WinRM |
