<#
Stops the viewer: the process that owns port 8000 (run it with .\stop).
Any state counts, not only "Listen": on this machine the server socket can show up as
another state. The database keeps running; stop it with: docker stop iv-test-pg
#>
$owners = @()
foreach ($connection in (Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue)) {
    $id = $connection.OwningProcess
    if ($id -ne 0 -and $owners -notcontains $id) {
        $owners += $id
    }
}
if ($owners.Count -eq 0) {
    Write-Host "The viewer is not running."
    exit 0
}
foreach ($id in $owners) {
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1
Write-Host "Viewer stopped."
