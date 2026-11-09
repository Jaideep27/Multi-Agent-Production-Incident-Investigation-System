<#
Run the test suite on Windows.

Why this exists: the test fixtures run db/init/*.sh with `bash` and *.sql with `psql`.
On Windows `bash` is usually the WSL launcher (fails with no distro installed) and `psql`
is not installed, so a plain `pytest` errors in setup. This script runs the unchanged suite
inside a Linux container that has both tools, against a throwaway Postgres 16 container.

Your working tree is mounted read-only and copied inside the container, so nothing in the
repo is written to.

Usage:
  powershell -ExecutionPolicy Bypass -File scripts\test_in_docker.ps1
  powershell -ExecutionPolicy Bypass -File scripts\test_in_docker.ps1 tests/unit -q
  powershell -ExecutionPolicy Bypass -File scripts\test_in_docker.ps1 -Clean
#>
param(
    [switch]$Clean,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$DbContainer = "iv-test-pg"
$Network = "iv-test-net"
$RunnerImage = "iv-test-runner"
$PipCacheVolume = "iv-test-pip-cache"
# Test-only password; it matches the default in tests/conftest.py.
$AdminPassword = "postgres-test-pw"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Test-DockerObject($kind, $name) {
    # `docker <kind> inspect` exits non-zero when the object does not exist.
    & docker $kind inspect $name *> $null
    return ($LASTEXITCODE -eq 0)
}

if ($Clean) {
    if (Test-DockerObject "container" $DbContainer) { docker rm -f $DbContainer | Out-Null }
    if (Test-DockerObject "network" $Network) { docker network rm $Network | Out-Null }
    Write-Output "Removed $DbContainer and $Network (the $RunnerImage image and pip cache are kept)."
    exit 0
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker is not running. Start Docker Desktop and try again."
    exit 1
}

# 1. Network, so the runner can reach the database by container name.
if (-not (Test-DockerObject "network" $Network)) {
    docker network create $Network | Out-Null
}

# 2. Postgres 16. Started once and reused; the tests create and drop their own database.
if (-not (Test-DockerObject "container" $DbContainer)) {
    docker run -d --name $DbContainer --network $Network -p 54329:5432 `
        -e "POSTGRES_PASSWORD=$AdminPassword" postgres:16 | Out-Null
} else {
    $running = docker inspect -f "{{.State.Running}}" $DbContainer
    if ($running -ne "true") { docker start $DbContainer | Out-Null }
    $networks = docker inspect -f "{{json .NetworkSettings.Networks}}" $DbContainer
    if (-not ($networks -match $Network)) { docker network connect $Network $DbContainer }
}

$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    docker exec $DbContainer pg_isready -U postgres -q
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $ready) {
    Write-Error "Postgres in $DbContainer did not become ready within 30 seconds."
    exit 1
}

# 3. Runner image: Python plus the Postgres client tools. Built once.
if (-not (Test-DockerObject "image" $RunnerImage)) {
    $dockerfile = @'
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*
'@
    $dockerfile | docker build -q -t $RunnerImage - | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Error "Building $RunnerImage failed."; exit 1 }
}

# 4. Copy the repo, install it, run pytest. Extra arguments are passed straight to pytest.
# Kept on one line with no double quotes: Windows PowerShell 5.1 mangles embedded double
# quotes when passing arguments to native programs. (So pytest arguments cannot contain spaces.)
$inner = "set -e; cp -r /src /work; cd /work; " +
    "pip install -q --disable-pip-version-check --root-user-action=ignore -e '.[dev]'; " +
    'python -m pytest $@'

docker run --rm --network $Network `
    -v "${RepoRoot}:/src:ro" `
    -v "${PipCacheVolume}:/root/.cache/pip" `
    -e "IV_TEST_PG_HOST=$DbContainer" `
    -e "IV_TEST_PG_PORT=5432" `
    -e "IV_TEST_PG_ADMIN_PASSWORD=$AdminPassword" `
    $RunnerImage bash -c $inner bash @PytestArgs

exit $LASTEXITCODE
