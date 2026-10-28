param(
    [string]$Model,
    [int]$Port = 8765,
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$workspacePath = $PSScriptRoot
Push-Location -LiteralPath $workspacePath
try {
    if (-not $SkipBuild) {
        if (-not (Test-Path -LiteralPath (Join-Path $workspacePath 'frontend/node_modules'))) {
            throw 'Run npm ci in frontend first, then start the workbench again.'
        }
        Push-Location -LiteralPath (Join-Path $workspacePath 'frontend')
        try {
            & npm run build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
        } finally { Pop-Location }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $workspacePath 'frontend/dist/index.html'))) {
        throw 'Frontend build missing. Run npm run build in frontend.'
    }
    $launchArguments = @('-B', (Join-Path $workspacePath 'workbench.py'), '--port', $Port)
    if ($Model) { $launchArguments += @('--model', $Model) }
    & python @launchArguments
    if ($LASTEXITCODE -ne 0) { throw "Workbench exited with code $LASTEXITCODE." }
} finally { Pop-Location }
