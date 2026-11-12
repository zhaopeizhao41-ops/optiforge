<#
.SYNOPSIS
    OptiForge 本地工作台启动器（可选的独立应用窗口）。

.DESCRIPTION
    默认行为保持不变：检查/构建前端并以前台方式运行 workbench.py。
    添加 -App 时，先复用本工作树中已核对的服务；没有可复用实例时才以隐藏进程启动
    一个新服务（先写临时凭据，核对 PID/身份后再原子提升），然后用 Chrome/Edge 的
    --app 窗口打开它。关闭窗口不会停止服务。

    安全边界：
    * 任何在线记录（含健康外服务 / 坏 token / 坏协议）都不会被覆盖；
    * 凭据匹配区分大小写，且要求 URL 为规范化 loopback；
    * 默认模式与 -App 新启动共用工作树级互斥锁；
    * 只有本次启动并核对过端口归属的进程才会被终止。

.PARAMETER Model
    显式交由 workbench.py 载入的已有模型文件。与 -WorkflowOnly 互斥；复用在跑服务时拒绝。

.PARAMETER Port
    服务端口（0..65535）。0 表示由操作系统分配空闲端口。

.PARAMETER SkipBuild
    跳过前端构建（仍要求 frontend/dist/index.html 存在）。

.PARAMETER App
    以独立应用窗口（Chrome/Edge --app）启动；会复用本工作树已核对的服务。

.PARAMETER Browser
    仅与 -App 搭配：Auto（先 Chrome 后 Edge）、Edge、Chrome。

.PARAMETER WorkflowOnly
    以 --workflow-only 启动/要求服务：不连接原生光学软件。
#>
[CmdletBinding()]
param(
    [string]$Model,
    [ValidateRange(0, 65535)]
    [int]$Port = 8765,
    [switch]$SkipBuild,
    [switch]$App,
    [ValidateSet('Auto', 'Edge', 'Chrome')]
    [string]$Browser = 'Auto',
    [switch]$WorkflowOnly
)

$ErrorActionPreference = 'Stop'
$script:WorkspacePath = $PSScriptRoot
$script:ServiceDirectory = Join-Path $script:WorkspacePath 'output\workbench_service'
$script:ServiceFile = Join-Path $script:ServiceDirectory 'service.json'
$script:ProfileRoot = Join-Path $script:WorkspacePath 'output\desktop_profile'
$script:ProtocolVersion = 2

function Assert-PortRange {
    param([int]$Value)
    if ($Value -lt 0 -or $Value -gt 65535) {
        throw "端口必须在 0..65535 之间：$Value"
    }
}

function Assert-LauncherParameters {
    param(
        [string]$Model,
        [bool]$App,
        [string]$Browser,
        [bool]$WorkflowOnly,
        [bool]$BrowserSpecified,
        [int]$Port
    )
    Assert-PortRange -Value $Port
    if ($WorkflowOnly -and -not [string]::IsNullOrWhiteSpace($Model)) {
        throw '-WorkflowOnly 与 -Model 不能同时使用：工作流模式不加载原生模型。'
    }
    if (-not $App -and $BrowserSpecified) {
        throw '-Browser 仅与 -App 搭配使用；默认前台模式不需要浏览器参数。'
    }
    if ($App -and $Browser -notin @('Auto', 'Chrome', 'Edge')) {
        throw "不支持的浏览器：$Browser"
    }
}

function Get-CanonicalLoopbackUri {
    param([string]$Url)
    if ([string]::IsNullOrWhiteSpace($Url)) { return $null }
    try { $uri = [System.Uri]$Url } catch { return $null }
    if ($uri.Scheme -ne 'http') { return $null }
    if ($uri.Host -ne '127.0.0.1') { return $null }
    if (-not [string]::IsNullOrEmpty($uri.UserInfo)) { return $null }
    if (-not [string]::IsNullOrEmpty($uri.Query)) { return $null }
    if (-not [string]::IsNullOrEmpty($uri.Fragment)) { return $null }
    if ($uri.AbsolutePath -ne '/') { return $null }
    if ($uri.Port -lt 1 -or $uri.Port -gt 65535) { return $null }
    return $uri
}

function Invoke-JsonGet {
    param([string]$Url, [int]$TimeoutSeconds = 3)
    try {
        return Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec $TimeoutSeconds -UseBasicParsing
    } catch {
        return $null
    }
}

function Test-TcpOpen {
    param([string]$HostName, [int]$Port, [int]$TimeoutMilliseconds = 800)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMilliseconds)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-PortOwnerPid {
    param([int]$Port)
    if ($Port -lt 1 -or $Port -gt 65535) { return $null }
    try {
        $connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
            Select-Object -First 1
        if ($connection) { return [int]$connection.OwningProcess }
    } catch {
        return $null
    }
    return $null
}

function Get-OnlineServiceStatus {
    <#
        读取服务凭据并探测其记录端点，返回：
          none | unreadable | offline | unverified | identity-mismatch | online
        unverified / identity-mismatch 表示端点在线但无法确认是本服务的有效凭据：
        调用方必须拒绝覆盖，绝不能当作“陈旧”处理。
    #>
    param([string]$ServiceFile = $script:ServiceFile)
    $result = [pscustomobject]@{
        State        = 'none'
        Port         = $null
        Url          = $null
        InstanceId   = $null
        Pid          = $null
        Capabilities = $null
        Credential   = $null
    }
    if (-not (Test-Path -LiteralPath $ServiceFile)) { return $result }
    try {
        $credential = Get-Content -LiteralPath $ServiceFile -Raw -ErrorAction Stop | ConvertFrom-Json
    } catch {
        $result.State = 'unreadable'
        return $result
    }
    $result.Credential = $credential
    $uri = Get-CanonicalLoopbackUri -Url ([string]$credential.url)
    if ($null -eq $uri) {
        $result.State = 'unreadable'
        return $result
    }
    $result.Port = $uri.Port
    $result.Url = "http://127.0.0.1:$($uri.Port)/"

    if (-not (Test-TcpOpen -HostName '127.0.0.1' -Port $uri.Port)) {
        $result.State = 'offline'
        return $result
    }

    $fieldsValid = ($credential.protocol_version -eq $script:ProtocolVersion) -and
        (-not [string]::IsNullOrWhiteSpace([string]$credential.token)) -and
        (-not [string]::IsNullOrWhiteSpace([string]$credential.service_instance_id))
    if (-not $fieldsValid) {
        $result.State = 'unverified'
        return $result
    }

    $base = "http://127.0.0.1:$($uri.Port)"
    $health = Invoke-JsonGet -Url "$base/api/health"
    if ($null -eq $health -or $health.status -ne 'success' -or
        $health.protocol_version -ne $script:ProtocolVersion -or
        $health.service -ne 'optiforge-workbench') {
        $result.State = 'unverified'
        return $result
    }

    $session = Invoke-JsonGet -Url "$base/api/session"
    if ($null -eq $session -or $session.protocol_version -ne $script:ProtocolVersion) {
        $result.State = 'unverified'
        return $result
    }
    if ([string]::IsNullOrWhiteSpace([string]$session.token) -or
        [string]::IsNullOrWhiteSpace([string]$session.service_instance_id)) {
        $result.State = 'unverified'
        return $result
    }
    if ([string]$session.token -cne [string]$credential.token) {
        $result.State = 'identity-mismatch'
        return $result
    }
    if ([string]$session.service_instance_id -cne [string]$credential.service_instance_id) {
        $result.State = 'identity-mismatch'
        return $result
    }
    $capabilities = $session.capabilities
    if ($null -eq $capabilities -or
        $capabilities.native_engine -isnot [bool] -or
        $capabilities.model_import -isnot [bool]) {
        $result.State = 'unverified'
        return $result
    }
    $ownerPid = Get-PortOwnerPid -Port $uri.Port
    if ($null -eq $ownerPid) {
        $result.State = 'unverified'
        return $result
    }
    $result.State = 'online'
    $result.InstanceId = [string]$session.service_instance_id
    $result.Pid = $ownerPid
    $result.Capabilities = $capabilities
    return $result
}

function Test-CapabilityMatch {
    param($Capabilities, [bool]$WorkflowOnly)
    if ($null -eq $Capabilities) { return $false }
    if ($WorkflowOnly) {
        return ($Capabilities.native_engine -eq $false) -and ($Capabilities.model_import -eq $false)
    }
    return ($Capabilities.native_engine -eq $true) -and ($Capabilities.model_import -eq $true)
}

function Test-ServiceMatchesRequest {
    param($Status, [int]$Port, [bool]$WorkflowOnly)
    if ($null -eq $Status -or $Status.State -ne 'online') { return $false }
    if ($Port -ne 0 -and $Status.Port -ne $Port) { return $false }
    return (Test-CapabilityMatch -Capabilities $Status.Capabilities -WorkflowOnly $WorkflowOnly)
}

function Assert-NoConflictingOnlineService {
    <#
        返回可复用的在线服务；否则在“在线但不匹配 / 无法验证 / 身份不符”时抛出，
        避免启动新服务覆盖现有凭据。offline/none/unreadable 返回 $null（可按陈旧处理）。
    #>
    param(
        [string]$ServiceFile = $script:ServiceFile,
        [int]$Port,
        [bool]$WorkflowOnly,
        [string]$Model
    )
    $status = Get-OnlineServiceStatus -ServiceFile $ServiceFile
    if ($status.State -eq 'online') {
        if (-not [string]::IsNullOrWhiteSpace($Model)) {
            throw "本工作树已有在线服务（$($status.Url)，PID $($status.Pid)）。-Model 不会隐式载入到该服务；请先停止该服务，或改用独立工作树。"
        }
        if (-not (Test-ServiceMatchesRequest -Status $status -Port $Port -WorkflowOnly $WorkflowOnly)) {
            $mode = if ($WorkflowOnly) { 'workflow-only' } else { 'native' }
            throw "本工作树已有在线服务（$($status.Url)，PID $($status.Pid)），但端口或能力与本次请求不匹配（请求端口 $Port，模式 $mode）。不会覆盖其 service.json；请先停止该服务或使用匹配的参数。"
        }
        return $status
    }
    if ($status.State -eq 'unreadable') {
        throw "service.json 存在但无法解析出可验证的 loopback 端点；无法证明其离线，拒绝启动并保留文件。请人工检查或移走该文件。"
    }
    if ($status.State -eq 'unverified' -or $status.State -eq 'identity-mismatch') {
        throw "service.json 指向的端点在线，但无法确认是本工作树的有效服务（状态：$($status.State)）。拒绝覆盖该凭据；请人工检查或移走该文件。"
    }
    return $null
}

function Assert-PortAvailableForStart {
    param([int]$Port, [bool]$WorkflowOnly = $false)
    if ($Port -eq 0) { return }
    $ownerPid = Get-PortOwnerPid -Port $Port
    if ($null -eq $ownerPid) { return }
    $status = Get-OnlineServiceStatus
    if ($status.State -eq 'online' -and $status.Port -eq $Port) {
        throw "端口 $Port 已被本工作树的服务占用（PID $ownerPid）。请直接使用该服务（-App 会自动复用），或改用其他 -Port。未终止任何进程。"
    }
    throw "端口 $Port 已被其他进程占用（PID $ownerPid）。不会终止该进程；请换一个 -Port 或先确认它。"
}

function Enter-LaunchLock {
    param([string]$Path, [int]$TimeoutSeconds = 10)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            return [System.IO.File]::Open($Path, [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        } catch [System.IO.IOException] {
            Start-Sleep -Milliseconds 200
        }
    }
    throw '另一个启动过程正在使用本工作树的服务凭据（启动互斥锁被占用）。请稍后重试。'
}

function Exit-LaunchLock {
    param($Lock)
    if ($null -ne $Lock) { try { $Lock.Dispose() } catch { } }
}

function Get-WorkbenchArgumentList {
    param([int]$Port, [bool]$WorkflowOnly, [string]$WorkbenchPath, [string]$Model, [string]$ServiceFile)
    $arguments = @('-B', $WorkbenchPath, '--port', $Port)
    if ($WorkflowOnly) { $arguments += '--workflow-only' }
    if (-not [string]::IsNullOrWhiteSpace($Model)) { $arguments += @('--model', $Model) }
    if (-not [string]::IsNullOrWhiteSpace($ServiceFile)) { $arguments += @('--service-file', $ServiceFile) }
    return $arguments
}

function Get-WorkbenchArguments {
    param([int]$Port, [bool]$WorkflowOnly, [string]$WorkbenchPath, [string]$Model, [string]$ServiceFile)
    $quotedWorkbench = '"' + $WorkbenchPath + '"'
    $arguments = "-B $quotedWorkbench --port $Port"
    if ($WorkflowOnly) { $arguments += ' --workflow-only' }
    if (-not [string]::IsNullOrWhiteSpace($Model)) { $arguments += ' --model "' + $Model + '"' }
    if (-not [string]::IsNullOrWhiteSpace($ServiceFile)) { $arguments += ' --service-file "' + $ServiceFile + '"' }
    return $arguments
}

function Resolve-Browser {
    param([string]$Browser)
    $testPath = $env:OPTIFORGE_LAUNCHER_TEST_BROWSER
    if (-not [string]::IsNullOrWhiteSpace($testPath) -and (Test-Path -LiteralPath $testPath)) {
        return [pscustomobject]@{ Name = 'Test'; Path = $testPath }
    }
    $ordered = if ($Browser -eq 'Auto') { @('Chrome', 'Edge') } else { @($Browser) }
    $searchRoots = $null
    if (-not [string]::IsNullOrWhiteSpace($env:OPTIFORGE_LAUNCHER_TEST_BROWSER_ROOTS)) {
        $searchRoots = $env:OPTIFORGE_LAUNCHER_TEST_BROWSER_ROOTS -split ';' | Where-Object { $_ }
    }
    $onlyRoots = ($env:OPTIFORGE_LAUNCHER_TEST_BROWSER_ONLY -eq '1')
    foreach ($name in $ordered) {
        $candidates = @()
        if (-not $onlyRoots) {
            $candidates = switch ($name) {
                'Chrome' {
                    @(
                        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
                        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
                        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
                    )
                }
                'Edge' {
                    @(
                        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
                        (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
                        (Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\Application\msedge.exe')
                    )
                }
            }
        }
        foreach ($candidate in $candidates) {
            if ($candidate -and (Test-Path -LiteralPath $candidate)) {
                return [pscustomobject]@{ Name = $name; Path = $candidate }
            }
        }
        if ($searchRoots) {
            $binary = if ($name -eq 'Chrome') { 'chrome.exe' } else { 'msedge.exe' }
            foreach ($root in $searchRoots) {
                $candidate = Join-Path $root $binary
                if (Test-Path -LiteralPath $candidate) {
                    return [pscustomobject]@{ Name = $name; Path = $candidate }
                }
            }
        }
    }
    return $null
}

function Get-BrowserArguments {
    param([string]$Url, [string]$ProfileDirectory)
    return "--app=`"$Url`" --user-data-dir=`"$ProfileDirectory`" --no-first-run --no-default-browser-check --window-size=1440,960"
}

function Open-AppWindow {
    param(
        [pscustomobject]$ResolvedBrowser,
        [string]$Url,
        [string]$ProfileRoot
    )
    if ($null -eq $ResolvedBrowser -or -not (Test-Path -LiteralPath $ResolvedBrowser.Path)) {
        throw '未找到 Chrome 或 Edge，无法打开独立应用窗口。请安装其中一个浏览器；不会自动改用默认浏览器。'
    }
    $profileDirectory = Join-Path $ProfileRoot $ResolvedBrowser.Name.ToLowerInvariant()
    New-Item -ItemType Directory -Force -Path $profileDirectory | Out-Null
    $arguments = Get-BrowserArguments -Url $Url -ProfileDirectory $profileDirectory
    $process = Start-Process -FilePath $ResolvedBrowser.Path -ArgumentList $arguments -PassThru -ErrorAction Stop
    return [pscustomobject]@{
        Browser = $ResolvedBrowser.Name
        Path    = $ResolvedBrowser.Path
        Pid     = $process.Id
        Profile = $profileDirectory
    }
}

function Test-ProcessOwnsPort {
    param([int]$ProcessId, [int]$Port)
    return ((Get-PortOwnerPid -Port $Port) -eq $ProcessId)
}

function Get-LogTail {
    param([string[]]$Paths, [int]$Lines = 20)
    $parts = @()
    foreach ($path in $Paths) {
        if (Test-Path -LiteralPath $path) {
            $parts += "--- $path ---"
            $parts += (Get-Content -LiteralPath $path -Tail $Lines -ErrorAction SilentlyContinue)
        }
    }
    return ($parts -join [Environment]::NewLine)
}

function Remove-TemporaryCredential {
    param([string]$Path, [string]$OfficialPath = $script:ServiceFile)
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    if ($Path -eq $OfficialPath) { return }
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    }
}

function Start-WorkbenchService {
    <#
        以隐藏进程启动工作台并等待“实际服务文件 + 在线身份 + 端口归属”就绪。
        ServiceFile 非正式路径时使用临时凭据；失败只清理本次创建且核对属于本 PID 的
        进程与临时凭据，不删除正式凭据。
    #>
    param(
        [int]$Port,
        [bool]$WorkflowOnly,
        [string]$Model,
        [string]$WorkspacePath = $script:WorkspacePath,
        [string]$OutputDirectory = $script:ServiceDirectory,
        [string]$ServiceFile = $null,
        [int]$TimeoutSeconds = 25,
        [string]$PythonExecutable = $null,
        $LaunchLock = $null
    )
    Assert-PortRange -Value $Port
    New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
    $ownLock = $null
    if ($null -eq $LaunchLock) {
        $ownLock = Enter-LaunchLock -Path (Join-Path $OutputDirectory 'launch.lock')
    }
    $credentialPath = if ($ServiceFile) { $ServiceFile } else { Join-Path $OutputDirectory 'service.json' }
    $temporary = ($credentialPath -ne (Join-Path $OutputDirectory 'service.json'))
    try {
        $python = if ($PythonExecutable) { $PythonExecutable } else { (Get-Command python -ErrorAction Stop).Source }
        $workbenchPath = Join-Path $WorkspacePath 'workbench.py'
        $arguments = Get-WorkbenchArguments -Port $Port -WorkflowOnly $WorkflowOnly `
            -WorkbenchPath $workbenchPath -Model $Model -ServiceFile $credentialPath
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
        $outLog = Join-Path $OutputDirectory "launcher-$stamp.out.log"
        $errLog = Join-Path $OutputDirectory "launcher-$stamp.err.log"
        $process = Start-Process -FilePath $python -ArgumentList $arguments `
            -WorkingDirectory $WorkspacePath -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog

        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        $status = $null
        while ((Get-Date) -lt $deadline) {
            if ($process.HasExited) { break }
            $status = Get-OnlineServiceStatus -ServiceFile $credentialPath
            if ($status.State -eq 'online' -and $status.Pid -eq $process.Id) { break }
            $status = $null
            Start-Sleep -Milliseconds 250
        }
        if ($null -eq $status) {
            $tail = Get-LogTail -Paths @($outLog, $errLog)
            # 只终止本次 Start-Process 返回的子进程对象本身：未写凭据/未监听的慢启动
            # 也会被清理；不按端口反查 PID，因此无关端口占用者不会被触碰。
            try { $process.Refresh() } catch { }
            if (-not $process.HasExited) {
                try {
                    $process.Kill()
                    $process.WaitForExit(5000) | Out-Null
                } catch { }
            }
            if ($temporary) { Remove-TemporaryCredential -Path $credentialPath }
            throw "工作台服务未在 $TimeoutSeconds 秒内就绪。已终止本次启动的子进程并清理临时凭据；正式凭据未改动。`n$tail"
        }
        return [pscustomobject]@{
            Url         = $status.Url
            Port        = $status.Port
            Pid         = $process.Id
            InstanceId  = $status.InstanceId
            ServiceFile = $credentialPath
            Temporary   = $temporary
            OutLog      = $outLog
            ErrLog      = $errLog
        }
    } finally {
        if ($null -ne $ownLock) { Exit-LaunchLock -Lock $ownLock }
    }
}

function Stop-StartedService {
    param($Service)
    if ($null -eq $Service) { return }
    if (Test-ProcessOwnsPort -ProcessId $Service.Pid -Port $Service.Port) {
        Stop-Process -Id $Service.Pid -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-WorkbenchLauncher {
    param(
        [string]$Model,
        [int]$Port,
        [bool]$SkipBuild,
        [bool]$App,
        [string]$Browser,
        [bool]$WorkflowOnly,
        [bool]$BrowserSpecified,
        [string]$WorkspacePath = $script:WorkspacePath
    )
    Assert-LauncherParameters -Model $Model -App $App -Browser $Browser `
        -WorkflowOnly $WorkflowOnly -BrowserSpecified $BrowserSpecified -Port $Port

    $serviceDirectory = Join-Path $WorkspacePath 'output\workbench_service'
    $officialCredential = Join-Path $serviceDirectory 'service.json'

    Push-Location -LiteralPath $WorkspacePath
    try {
        if (-not $SkipBuild) {
            $nodeModules = Join-Path $WorkspacePath 'frontend\node_modules'
            if (-not (Test-Path -LiteralPath $nodeModules)) {
                throw 'Run npm ci in frontend first, then start the workbench again.'
            }
            Push-Location -LiteralPath (Join-Path $WorkspacePath 'frontend')
            try {
                & npm run build
                if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
            } finally { Pop-Location }
        }
        $distIndex = Join-Path $WorkspacePath 'frontend\dist\index.html'
        if (-not (Test-Path -LiteralPath $distIndex)) {
            throw 'Frontend build missing. Run npm run build in frontend.'
        }

        if ($App) {
            # 浏览器预检必须先于任何服务启动；复用时浏览器失败绝不停止已有实例。
            $resolvedBrowser = Resolve-Browser -Browser $Browser
            if ($null -eq $resolvedBrowser) {
                throw '未找到 Chrome 或 Edge，无法打开独立应用窗口。请安装其中一个浏览器；不会自动改用默认浏览器，也不会启动服务。'
            }

            $status = Assert-NoConflictingOnlineService -ServiceFile $officialCredential `
                -Port $Port -WorkflowOnly $WorkflowOnly -Model $Model
            $startedService = $null
            $lock = $null
            if ($null -eq $status) {
                New-Item -ItemType Directory -Force -Path $serviceDirectory | Out-Null
                $lock = Enter-LaunchLock -Path (Join-Path $serviceDirectory 'launch.lock') -TimeoutSeconds 15
                try {
                    # 持锁后重新探测：另一个启动过程可能已经就绪。
                    $status = Assert-NoConflictingOnlineService -ServiceFile $officialCredential `
                        -Port $Port -WorkflowOnly $WorkflowOnly -Model $Model
                    if ($null -eq $status) {
                        Assert-PortAvailableForStart -Port $Port -WorkflowOnly $WorkflowOnly
                        $temporaryCredential = Join-Path $serviceDirectory ('service.pending.' + [guid]::NewGuid().ToString('N') + '.json')
                        try {
                            $startedService = Start-WorkbenchService -Port $Port -WorkflowOnly $WorkflowOnly `
                                -Model $Model -WorkspacePath $WorkspacePath -OutputDirectory $serviceDirectory `
                                -ServiceFile $temporaryCredential -TimeoutSeconds 40 -LaunchLock $lock
                        } catch {
                            Remove-TemporaryCredential -Path $temporaryCredential -OfficialPath $officialCredential
                            throw
                        }
                        try {
                            $window = Open-AppWindow -ResolvedBrowser $resolvedBrowser -Url $startedService.Url `
                                -ProfileRoot (Join-Path $WorkspacePath 'output\desktop_profile')
                        } catch {
                            Stop-StartedService -Service $startedService
                            Remove-TemporaryCredential -Path $temporaryCredential -OfficialPath $officialCredential
                            Write-Host '浏览器启动失败，已停止本次启动的服务并清理临时凭据。'
                            throw
                        }
                        try {
                            if (Test-Path -LiteralPath $officialCredential) {
                                # PS5.1 binds a literal $null to an empty string here;
                                # [NullString]::Value passes the true null ReplaceFile backup.
                                [System.IO.File]::Replace($temporaryCredential, $officialCredential, [NullString]::Value)
                            } else {
                                [System.IO.File]::Move($temporaryCredential, $officialCredential)
                            }
                        } catch {
                            Stop-StartedService -Service $startedService
                            Remove-TemporaryCredential -Path $temporaryCredential -OfficialPath $officialCredential
                            throw "服务已就绪，但临时凭据提升失败：$($_.Exception.Message)"
                        }
                        Write-Host "OptiForge: $($startedService.Url)"
                        Write-Host "服务 PID: $($startedService.Pid)（实例 $($startedService.InstanceId)）"
                        Write-Host "浏览器: $($window.Browser)（PID $($window.Pid)，专用 profile: $($window.Profile)）"
                        Write-Host "关闭窗口不会停止服务。停止服务：Stop-Process -Id $($startedService.Pid)"
                        return
                    }
                } finally {
                    Exit-LaunchLock -Lock $lock
                }
            }

            # 复用路径：在线且匹配（Model 已在 Assert 中拒绝）。
            try {
                $window = Open-AppWindow -ResolvedBrowser $resolvedBrowser -Url $status.Url `
                    -ProfileRoot (Join-Path $WorkspacePath 'output\desktop_profile')
            } catch {
                Write-Host '浏览器启动失败；已在线服务保持运行，未停止任何实例。'
                throw
            }
            Write-Host "OptiForge: $($status.Url)"
            Write-Host "服务 PID: $($status.Pid)（实例 $($status.InstanceId)，已复用）"
            Write-Host "浏览器: $($window.Browser)（PID $($window.Pid)，专用 profile: $($window.Profile)）"
            Write-Host "关闭窗口不会停止服务。停止服务：Stop-Process -Id $($status.Pid)"
            return
        }

        # 默认前台模式：与 App 新启动共用同一互斥锁，防止覆盖在线凭据。
        New-Item -ItemType Directory -Force -Path $serviceDirectory | Out-Null
        $lock = Enter-LaunchLock -Path (Join-Path $serviceDirectory 'launch.lock') -TimeoutSeconds 5
        try {
            $status = Assert-NoConflictingOnlineService -ServiceFile $officialCredential `
                -Port $Port -WorkflowOnly $WorkflowOnly -Model $Model
            if ($null -ne $status) {
                throw "已有在线服务（$($status.Url)，PID $($status.Pid)）。默认前台模式不会复用或覆盖其凭据；请使用 -App 复用，或先停止该服务。"
            }
            if ($Port -ne 0) {
                $ownerPid = Get-PortOwnerPid -Port $Port
                if ($null -ne $ownerPid) {
                    throw "端口 $Port 已被进程 $ownerPid 占用。默认模式不会复用或终止它；请换一个 -Port，或使用 -App 复用本工作树服务。"
                }
            }
            $arguments = Get-WorkbenchArgumentList -Port $Port -WorkflowOnly $WorkflowOnly `
                -WorkbenchPath (Join-Path $WorkspacePath 'workbench.py') -Model $Model
            & python @arguments
            if ($LASTEXITCODE -ne 0) { throw "Workbench exited with code $LASTEXITCODE." }
        } finally {
            Exit-LaunchLock -Lock $lock
        }
    } finally {
        Pop-Location
    }
}

# 被 dot-source 时只暴露函数，便于在隔离目录中做无窗口测试。
if ($MyInvocation.InvocationName -ne '.') {
    Invoke-WorkbenchLauncher -Model $Model -Port $Port -SkipBuild:$SkipBuild `
        -App:$App -Browser $Browser -WorkflowOnly:$WorkflowOnly `
        -BrowserSpecified $PSBoundParameters.ContainsKey('Browser')
}
