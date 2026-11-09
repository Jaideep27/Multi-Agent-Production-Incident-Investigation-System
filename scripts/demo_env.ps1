<#
Environment for local demo runs against the demo database built by demo_db_in_docker.ps1.
Dot-source it in the PowerShell window you run from:   . .\scripts\demo_env.ps1
These are the local test credentials from tests/conftest.py, not secrets.
#>
$env:IV_PG_HOST = "127.0.0.1"
$env:IV_PG_PORT = "54329"
$env:IV_PG_DB = "incidents"
$env:AGENT_RO_PASSWORD = "agent-test-pw"
$env:SCORER_RO_PASSWORD = "scorer-test-pw"
$env:GENERATOR_RW_PASSWORD = "generator-test-pw"
$env:HARNESS_RW_PASSWORD = "harness-test-pw"
