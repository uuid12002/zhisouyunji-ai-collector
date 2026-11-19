$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$runtime = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $runtime)) {
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundled) { & $bundled -m venv .venv }
    elseif (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv .venv }
    else { python -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
}
& $runtime -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ is required.'"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
$requirements = 'requirements.txt'
if (Test-Path -LiteralPath 'requirements-lock.txt') { $requirements = 'requirements-lock.txt' }
& $runtime -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check network and retry.' }
& $runtime launch.py
