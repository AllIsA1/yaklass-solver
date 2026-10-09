# Установка yaklass-bot на Windows (PowerShell).
#   powershell -ExecutionPolicy Bypass -File install-cli.ps1
#   powershell -ExecutionPolicy Bypass -File install-cli.ps1 yaklass_bot-0.1.0-py3-none-any.whl
param([string]$Src = $PSScriptRoot)
$ErrorActionPreference = "Stop"
$target = "$Src[browser]"

if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv tool install --force $target
    $py = Join-Path (uv tool dir) "yaklass-bot\Scripts\python.exe"
} elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
    pipx install --force $target
    $py = Join-Path (pipx environment --value PIPX_LOCAL_VENVS) "yaklass-bot\Scripts\python.exe"
} else {
    Write-Host "Нужен uv или pipx. Установите один из них:"
    Write-Host "  uv:   powershell -c `"irm https://astral.sh/uv/install-cli.ps1 | iex`""
    Write-Host "  pipx: https://pipx.pypa.io/stable/installation/"
    exit 1
}

& $py -m playwright install chromium
if ($LASTEXITCODE -ne 0) { Write-Host "Chromium не установлен: укажите [run] channel в config.toml" }

Write-Host ""
Write-Host "Установлено."
# Стартовая настройка (браузер, API/аккаунт, vision...). Позже: yaklass-bot setup
$exe = Join-Path (Split-Path $py) "yaklass-bot.exe"
if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
    & $exe setup
} else {
    Write-Host "Настройка: yaklass-bot setup"
}
