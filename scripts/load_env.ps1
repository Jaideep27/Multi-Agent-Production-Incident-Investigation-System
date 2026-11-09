<#
Load .env (KEY=VALUE lines) into the current PowerShell session.
Dot-source it:   . .\scripts\load_env.ps1
#>
$envFile = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path ".env"
if (-not (Test-Path $envFile)) {
    Write-Error ".env not found; run scripts\setup.ps1 first"
    return
}
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $parts = $line.Split('=', 2)
    Set-Item -Path ("env:" + $parts[0].Trim()) -Value $parts[1].Trim()
}
