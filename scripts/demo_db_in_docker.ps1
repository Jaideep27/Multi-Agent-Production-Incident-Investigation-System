<#
Create (once) a persistent "incidents" database in the iv-test-pg container and load incidents
from the plan into it, for real investigation runs from Windows.

Run scripts\test_in_docker.ps1 once first: it creates the container, network and image.
The database is reachable from Windows at 127.0.0.1:54329. Local demo only: it uses the
test role passwords (see tests/conftest.py).

Usage:
  powershell -ExecutionPolicy Bypass -File scripts\demo_db_in_docker.ps1             # all plan entries
  powershell -ExecutionPolicy Bypass -File scripts\demo_db_in_docker.ps1 --only PG-1
#>
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$GeneratorArgs
)

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$inner = "set -e; cp -r /src /work; cd /work; " +
    "pip install -q --disable-pip-version-check --root-user-action=ignore -e '.[dev]'; " +
    'bash scripts/demo_db_setup.sh $@'

docker run --rm --network iv-test-net `
    -v "${RepoRoot}:/src:ro" `
    -v "iv-test-pip-cache:/root/.cache/pip" `
    -e "PGHOST=iv-test-pg" -e "PGPORT=5432" -e "PGPASSWORD=postgres-test-pw" `
    -e "AGENT_RO_PASSWORD=agent-test-pw" -e "SCORER_RO_PASSWORD=scorer-test-pw" `
    -e "GENERATOR_RW_PASSWORD=generator-test-pw" -e "HARNESS_RW_PASSWORD=harness-test-pw" `
    iv-test-runner bash -c $inner bash @GeneratorArgs

exit $LASTEXITCODE
