$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not $env:COLLECTOR_ADMIN_KEY -or $env:COLLECTOR_ADMIN_KEY.Length -lt 24) {
    throw 'Set COLLECTOR_ADMIN_KEY (at least 24 random characters) before starting.'
}
if (-not $env:COLLECTOR_ALLOWED_HOSTS) {
    throw 'Set COLLECTOR_ALLOWED_HOSTS to your HTTPS domain,127.0.0.1,localhost.'
}
if (-not (Test-Path -LiteralPath '.venv-server/Scripts/python.exe')) {
    py -3.12 -m venv .venv-server
    if ($LASTEXITCODE -ne 0) { throw 'Python venv creation failed' }
}
& ./.venv-server/Scripts/python.exe -m pip install -r requirements-server.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& ./.venv-server/Scripts/python.exe -m uvicorn remote_server:app --host 127.0.0.1 --port 8765 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
