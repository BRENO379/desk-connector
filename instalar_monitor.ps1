# ==============================================================
# INSTALADOR DO MONITOR UMBLER (Torre de Comando LGM)
# Faz, em um unico passo:
#   1) confere se o Python esta instalado
#   2) instala a biblioteca "requests"
#   3) guarda o token da Umbler (uma unica vez)
#   4) testa a conexao (comando "debug")
#   5) cria 2 tarefas automaticas no Agendador do Windows
#
# COMO USAR: clique com o botao direito neste arquivo > "Executar com o PowerShell"
# (ou, no terminal:  powershell -ExecutionPolicy Bypass -File instalar_monitor.ps1)
# ==============================================================

$ErrorActionPreference = "Stop"
$pasta = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $pasta

function Parar($msg) {
    Write-Host "`nERRO: $msg" -ForegroundColor Red
    Read-Host "Pressione ENTER para sair"
    exit 1
}

# --- 1) Python -------------------------------------------------
Write-Host "[1/5] Verificando o Python..." -ForegroundColor Cyan
try { $versao = & python --version 2>&1 } catch { $versao = $null }
if (-not $versao -or $versao -notmatch "Python 3") {
    Parar "Python 3 nao encontrado. Instale em https://www.python.org/downloads/ (marque 'Add Python to PATH') e rode de novo."
}
Write-Host "      $versao"

# --- 2) Biblioteca requests -----------------------------------
Write-Host "[2/5] Instalando a biblioteca 'requests'..." -ForegroundColor Cyan
& python -m pip install --quiet requests
if ($LASTEXITCODE -ne 0) { Parar "Falha ao instalar 'requests'. Verifique a internet." }

# --- 3) Token --------------------------------------------------
Write-Host "[3/5] Configurando o token da Umbler..." -ForegroundColor Cyan
if (-not [Environment]::GetEnvironmentVariable("UTALK_TOKEN", "User")) {
    $seguro = Read-Host "Cole o token (nao aparece na tela) e tecle ENTER" -AsSecureString
    $token = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($seguro))
    if ([string]::IsNullOrWhiteSpace($token)) { Parar "Token vazio." }
    [Environment]::SetEnvironmentVariable("UTALK_TOKEN", $token.Trim(), "User")
    Write-Host "      Token salvo na sua conta do Windows."
} else {
    Write-Host "      Token ja configurado (mantido)."
}
# Disponibiliza o token para este teste, sem precisar reabrir o terminal
$env:UTALK_TOKEN = [Environment]::GetEnvironmentVariable("UTALK_TOKEN", "User")

# --- 4) Teste de conexao --------------------------------------
Write-Host "[4/5] Testando a conexao com a Umbler..." -ForegroundColor Cyan
& python monitor_umbler.py canais
if ($LASTEXITCODE -eq 1) {
    Parar "Falha na consulta (veja a mensagem acima e o arquivo monitor_umbler.log). Tarefas NAO foram criadas."
}

# --- 5) Tarefas agendadas -------------------------------------
Write-Host "[5/5] Criando tarefas no Agendador do Windows..." -ForegroundColor Cyan
$bat = Join-Path $pasta "rodar_monitor.bat"
$tarefas = @(
    @{ Nome = "LGM Umbler - Fila parada"; Args = "fila 120" },
    @{ Nome = "LGM Umbler - Canais";      Args = "canais"   }
)
foreach ($t in $tarefas) {
    # /F sobrescreve se ja existir (pode rodar o instalador de novo sem duplicar)
    & schtasks /Create /F /SC MINUTE /MO 15 /TN $t.Nome /TR "`"$bat`" $($t.Args)" | Out-Null
    if ($LASTEXITCODE -ne 0) { Parar "Nao foi possivel criar a tarefa '$($t.Nome)'. Tente executar como Administrador." }
    Write-Host "      Criada: $($t.Nome) (a cada 15 min)"
}

Write-Host "`nPRONTO. O monitor roda sozinho a cada 15 minutos." -ForegroundColor Green
Write-Host "Resultados: saida_monitor.txt e monitor_umbler.log (nesta pasta)."
Write-Host "Para alertas por e-mail/WhatsApp, defina as variaveis ALERTA_* (veja o topo de monitor_umbler.py)."
Write-Host "Para remover: schtasks /Delete /TN `"LGM Umbler - Fila parada`" /F  (e o mesmo para 'Canais')."
Read-Host "`nPressione ENTER para fechar"
