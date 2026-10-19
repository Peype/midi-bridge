# Construit StageKontrol-Bridge-Windows.zip sur un PC Windows, sans passer par GitHub.
# Mêmes étapes que le workflow GitHub Actions : environnement Python, tests, PyInstaller, archive.
#
# Prérequis : Python 3.12 (python.org, case « Add python.exe to PATH » cochée).
# Lancement, depuis le dossier du bridge :
#   powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
# Résultat : dist\StageKontrol-Bridge-Windows.zip (et le dossier dist\StageKontrol-Bridge).

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

# Environnement Python propre à Windows (séparé du .venv du Mac).
if (-not (Test-Path .venv-win)) {
    py -3.12 -m venv .venv-win
}
$python = '.\.venv-win\Scripts\python.exe'

& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt -r requirements-dev.txt -r requirements-build.txt

& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests en échec : construction annulée.' }

& $python -m PyInstaller --noconfirm stagekontrol-bridge.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller en échec.' }

Copy-Item packaging\LISEZMOI-Windows.txt dist\StageKontrol-Bridge\ -Force
Compress-Archive -Force -Path dist\StageKontrol-Bridge -DestinationPath dist\StageKontrol-Bridge-Windows.zip

Write-Host ''
Write-Host 'Terminé : dist\StageKontrol-Bridge-Windows.zip'
Write-Host 'Test rapide : dist\StageKontrol-Bridge\StageKontrol-Bridge.exe'
