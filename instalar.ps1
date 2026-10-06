# Instalador da skill /publicar-projeto (Claude Code).
# Uso (PowerShell, na pasta extraida do zip):
#   powershell -ExecutionPolicy Bypass -File .\instalar.ps1
# Copia para %USERPROFILE%\.claude\skills\publicar-projeto. Se ja existir, faz backup antes (nunca apaga).

$ErrorActionPreference = "Stop"
$origem = $PSScriptRoot
$destino = Join-Path $env:USERPROFILE ".claude\skills\publicar-projeto"

Write-Host "== Instalando a skill publicar-projeto"
if ((Resolve-Path $origem).Path -eq $destino) {
    Write-Host "Ja esta na pasta de instalacao; so vou conferir os pre-requisitos."
} else {
    if (Test-Path $destino) {
        $backup = "$destino.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
        Move-Item $destino $backup
        Write-Host "Instalacao anterior movida para: $backup"
    }
    New-Item -ItemType Directory -Force (Split-Path $destino) | Out-Null
    Copy-Item $origem $destino -Recurse
    Get-ChildItem $destino -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
    Write-Host "Copiado para: $destino"
}

function Ok($t)    { Write-Host "  [OK]    $t" -ForegroundColor Green }
function Falta($t) { Write-Host "  [FALTA] $t" -ForegroundColor Yellow }

Write-Host "`n== Pre-requisitos"
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) { Ok ("Python: " + (& python --version 2>&1)) } else { Falta "Python 3.11+  ->  winget install --id Python.Python.3.13" }

if (Get-Command git -ErrorAction SilentlyContinue) { Ok ("Git: " + (& git --version)) } else { Falta "Git  ->  winget install --id Git.Git" }

$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh -and (Test-Path "$env:ProgramFiles\GitHub CLI\gh.exe")) { $gh = "$env:ProgramFiles\GitHub CLI\gh.exe" }
if ($gh) {
    & $gh auth status *> $null
    if ($LASTEXITCODE -eq 0) { Ok "GitHub CLI logado" } else { Falta "GitHub CLI sem login  ->  gh auth login  (sem isso o push fica bloqueado)" }
} else { Falta "GitHub CLI  ->  winget install --id GitHub.cli  e depois  gh auth login" }

$tess = (Get-Command tesseract -ErrorAction SilentlyContinue).Source
if (-not $tess -and (Test-Path "$env:ProgramFiles\Tesseract-OCR\tesseract.exe")) { $tess = "$env:ProgramFiles\Tesseract-OCR\tesseract.exe" }
if ($tess) {
    $langs = (& $tess --list-langs 2>&1) -join " "
    if ($langs -match "\bpor\b") { Ok "Tesseract com portugues" } else { Falta "Tesseract sem o idioma 'por' (reinstale marcando Portuguese)" }
} else { Falta "Tesseract OCR  ->  winget install --id UB-Mannheim.TesseractOCR  (marque Portuguese; sem ele imagens nao podem ser aprovadas)" }

if ($py) {
    & python -c "import pymupdf" *> $null
    if ($LASTEXITCODE -eq 0) { Ok "PyMuPDF" } else { Falta "PyMuPDF (opcional, melhora o OCR)  ->  python -m pip install pymupdf" }
    & python -c "import pytest" *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n== Testes da skill (projetos ficticios em pasta temporaria)"
        & python -m pytest -q -p no:cacheprovider (Join-Path $destino "tests")
    } else { Falta "pytest (opcional, para rodar os testes)  ->  python -m pip install pytest" }
}

Write-Host "`n== Ajustes desta maquina"
Write-Host "  - config.json: confira git_user_name, github_login e destino_base."
Write-Host "  - Abra uma sessao nova do Claude Code e use:  /publicar-projeto `"C:\caminho\do\projeto`""
