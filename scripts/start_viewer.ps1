<#
Starts the viewer on http://127.0.0.1:8000 (run it with .\start from the project folder).
Starts the demo database if needed and replaces a viewer that is already running.
Reruns stay on, also behind the public Tailscale link (the user's choice, 2026-11-08);
for a read-only copy run the viewer with --no-rerun. Stop with Ctrl+C or .\stop.
#>
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

# only one viewer can use port 8000: stop an old one first
& "$PSScriptRoot\stop_viewer.ps1" | Out-Null

Write-Host "Starting the database..."
docker start iv-test-pg | Out-Null

. "$PSScriptRoot\demo_env.ps1"

$viewerArgs = @("-m", "investigator.web", "--label", "eval-v4")
$public = $false
$tailscale = "C:\Program Files\Tailscale\tailscale.exe"
if (Test-Path $tailscale) {
    $status = & $tailscale serve status 2>$null | Out-String
    if ($status -match ":10000 \(Funnel on\)") {
        $public = $true
    }
}

Write-Host ""
Write-Host "Viewer: http://127.0.0.1:8000"
if ($public) {
    Write-Host "Public link is on. To see it: tailscale funnel status"
}
Write-Host "To stop: press Ctrl+C here, or run .\stop in another window"
Write-Host ""
& "$root\.venv\Scripts\python.exe" @viewerArgs
