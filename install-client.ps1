$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'WebCollector.exe'
if (-not (Test-Path -LiteralPath $source)) { throw 'WebCollector.exe is missing. Extract the entire ZIP first.' }
$targetDir = Join-Path $env:LOCALAPPDATA 'WebCollector\App'
New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
$targetExe = Join-Path $targetDir 'WebCollector.exe'
$targetMcp = Join-Path $targetDir 'WebCollectorMCP.exe'
$running = Get-Process -Name WebCollector,WebCollectorMCP -ErrorAction SilentlyContinue | Where-Object { $_.Path -in @($targetExe, $targetMcp) }
if ($running) { throw 'Please close all dedicated platform browsers, the collector client and MCP client before updating. Active tasks will not be terminated automatically.' }
if ([IO.Path]::GetFullPath($source) -ne [IO.Path]::GetFullPath($targetExe)) {
    Copy-Item -LiteralPath $source -Destination $targetExe -Force
}
$mcpSource = Join-Path $PSScriptRoot 'WebCollectorMCP.exe'
$mcpTarget = Join-Path $targetDir 'WebCollectorMCP.exe'
if ((Test-Path -LiteralPath $mcpSource) -and ([IO.Path]::GetFullPath($mcpSource) -ne [IO.Path]::GetFullPath($mcpTarget))) {
    Copy-Item -LiteralPath $mcpSource -Destination $mcpTarget -Force
}
$desktopDir = [Environment]::GetFolderPath('Desktop')
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktopDir 'WebCollector.lnk'))
$shortcut.TargetPath = $targetExe
$shortcut.WorkingDirectory = $targetDir
$shortcut.Save()
Start-Process -FilePath $targetExe -WorkingDirectory $targetDir -WindowStyle Normal
