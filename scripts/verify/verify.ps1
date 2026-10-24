# Reproducible verification from any working directory. Requires uv and Node 22.
$ErrorActionPreference = 'Stop'
$repositoryPath = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$evidencePath = Join-Path $repositoryPath '.verification'
New-Item -ItemType Directory -Force -Path $evidencePath | Out-Null
$env:UV_CACHE_DIR = Join-Path $repositoryPath '.uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $repositoryPath '.python'
$env:npm_config_cache = Join-Path $repositoryPath '.npm-cache'
$results = New-Object 'System.Collections.Generic.List[object]'
$summary = [ordered]@{ passed = $false; started_at = [DateTime]::UtcNow.ToString('o'); commands = @() }

function Invoke-VerificationCheck {
    param([string]$Label, [string]$Executable, [string[]]$Parameters)
    Write-Host $Label
    & $Executable @Parameters
    $exitStatus = $LASTEXITCODE
    $results.Add([ordered]@{ name = $Label; exit_code = $exitStatus })
    if ($exitStatus -ne 0) { throw "$Label failed with exit code $exitStatus" }
}

Push-Location -LiteralPath $repositoryPath
try {
    Invoke-VerificationCheck -Label 'Static repository integrity' -Executable 'node' -Parameters @('scripts/verify/check-structure.cjs')
    Invoke-VerificationCheck -Label 'Python dependencies' -Executable 'uv' -Parameters @('sync', '--locked', '--extra', 'dev')
    Invoke-VerificationCheck -Label 'ML dependency imports' -Executable 'uv' -Parameters @('run', 'python', '-c', 'import numpy, numba, shap, xgboost, sklearn; print({m.__name__: m.__version__ for m in (numpy, numba, shap, xgboost, sklearn)})')
    Invoke-VerificationCheck -Label 'Python compilation' -Executable 'uv' -Parameters @('run', 'python', '-m', 'compileall', '-q', 'src', 'tests', 'migrations', 'scripts')
    Invoke-VerificationCheck -Label 'Python regression suite' -Executable 'uv' -Parameters @('run', 'pytest', '-q', '--junitxml=.verification/pytest.xml')
    Push-Location -LiteralPath (Join-Path $repositoryPath 'frontend')
    try {
        Invoke-VerificationCheck -Label 'Frontend dependencies' -Executable 'npm.cmd' -Parameters @('ci')
        Invoke-VerificationCheck -Label 'TypeScript and Vite build' -Executable 'npm.cmd' -Parameters @('run', 'build')
        Invoke-VerificationCheck -Label 'Chromium installation' -Executable 'npx.cmd' -Parameters @('playwright', 'install', 'chromium')
        Invoke-VerificationCheck -Label 'Browser regression suite' -Executable 'npm.cmd' -Parameters @('test')
    } finally { Pop-Location }
    $summary.passed = $true
} catch {
    $summary.error = $_.Exception.Message
    throw
} finally {
    $summary.finished_at = [DateTime]::UtcNow.ToString('o')
    $summary.commands = $results.ToArray()
    $utf8Encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path $evidencePath 'result.json'), ($summary | ConvertTo-Json -Depth 5), $utf8Encoding)
    Pop-Location
}

