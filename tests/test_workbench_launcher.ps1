#requires -Version 5.1
<#
    OF-DESKTOP-024 launcher tests (rework).
    Isolated temp run roots only; every spawned process tracked and stopped by PID.
    Browser windows are never launched for real: resolution/argument/failure paths use
    the OPTIFORGE_LAUNCHER_TEST_BROWSER* seams with harmless executables.
#>
$ErrorActionPreference = 'Stop'
$script:Root = Split-Path -Parent $PSScriptRoot
$script:Launcher = Join-Path $script:Root 'start-workbench.ps1'
. $script:Launcher

$script:Results = @()
$script:TempRoots = @()
$script:Pids = @()
$script:HostExe = [System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
$script:HostVersion = $PSVersionTable.PSVersion.ToString()
$script:ChildVersion = (& $script:HostExe -NoProfile -Command '$PSVersionTable.PSVersion.ToString()' 2>&1 | Out-String).Trim()
Write-Host "[HOST] exe=$script:HostExe version=$script:HostVersion edition=$($PSVersionTable.PSEdition)"
Write-Host "[CHILD] exe=$script:HostExe version=$script:ChildVersion"
if ($script:ChildVersion -ne $script:HostVersion) { throw "Child host version mismatch: host=$script:HostVersion child=$script:ChildVersion" }

function Check {
    param([string]$Name, [scriptblock]$Body)
    try {
        & $Body
        $script:Results += [pscustomobject]@{ name = $Name; ok = $true; error = '' }
        Write-Host "[PASS] $Name" -ForegroundColor Green
    } catch {
        $script:Results += [pscustomobject]@{ name = $Name; ok = $false; error = $_.Exception.Message }
        Write-Host "[FAIL] $Name :: $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Assert-Throws {
    param([scriptblock]$Body, [string]$Pattern = '')
    $threw = $false
    try { & $Body } catch {
        $threw = $true
        if ($Pattern -and $_.Exception.Message -notmatch $Pattern) {
            throw "异常信息不匹配：$($_.Exception.Message)"
        }
    }
    if (-not $threw) { throw '预期抛出异常但没有' }
}

function New-TempRoot {
    $path = Join-Path $env:TEMP ("optiforge launcher test " + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $path | Out-Null
    $script:TempRoots += $path
    return $path
}

function Get-FreePort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    $listener.Stop()
    return $port
}

function Write-ServiceFile {
    param([string]$Root, [int]$Port, [string]$Token, [string]$Instance)
    $dir = Join-Path $Root 'output\workbench_service'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    @{ url = "http://127.0.0.1:$Port/"; token = $Token; protocol_version = 2; service_instance_id = $Instance } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dir 'service.json') -Encoding utf8
}

function Get-ServiceFilePath {
    param([string]$Root)
    return (Join-Path $Root 'output\workbench_service\service.json')
}

function Initialize-RunRoot {
    param([string]$Root)
    Copy-Item -LiteralPath (Join-Path $script:Root 'workbench.py') -Destination $Root
    foreach ($dir in 'core', 'tools', 'domain') {
        Copy-Item -LiteralPath (Join-Path $script:Root $dir) -Destination $Root -Recurse
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $Root 'frontend\dist') | Out-Null
    Set-Content -LiteralPath (Join-Path $Root 'frontend\dist\index.html') -Value '<!doctype html><title>fixture</title>'
}

function Start-Fixture {
    param([string]$Root, [string]$Mode, [string]$Token, [string]$Instance)
    $port = Get-FreePort
    $scriptPath = Join-Path $Root 'fixture_service.py'
    @'
import argparse, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, required=True)
parser.add_argument('--mode', default='workflow')
parser.add_argument('--token', default='fixture-token')
parser.add_argument('--instance', default='fixture-instance')
args = parser.parse_args()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *unused):
        pass
    def send_json(self, status, payload):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if self.path == '/api/health':
            if args.mode == 'foreign':
                body = b'not-an-optiforge-service'
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            protocol = 1 if args.mode == 'bad-protocol' else 2
            status = 'error' if args.mode == 'bad-status' else 'success'
            self.send_json(200, {'protocol_version': protocol, 'status': status, 'service': 'optiforge-workbench'})
            return
        if self.path == '/api/session':
            capabilities = {'native_engine': False, 'model_import': False}
            if args.mode == 'normal':
                capabilities = {'native_engine': True, 'model_import': True}
            if args.mode == 'mismatch':
                capabilities = {'native_engine': True, 'model_import': False}
            if args.mode == 'bad-capability':
                capabilities = {'native_engine': 'true', 'model_import': 'true'}
            payload = {'protocol_version': 2, 'service_instance_id': args.instance, 'capabilities': capabilities}
            if args.mode != 'missing-token':
                payload['token'] = args.token
            self.send_json(200, payload)
            return
        self.send_json(404, {'error': 'unknown'})

ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
'@ | Set-Content -LiteralPath $scriptPath -Encoding utf8
    $arguments = "-B `"$scriptPath`" --port $port --mode $Mode --token `"$Token`" --instance `"$Instance`""
    $process = Start-Process -FilePath (Get-Command python).Source `
        -ArgumentList $arguments -WindowStyle Hidden -PassThru
    $script:Pids += $process.Id
    $deadline = (Get-Date).AddSeconds(15)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if ($process.HasExited) { break }
        try {
            $raw = Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 1 -UseBasicParsing
            if ($Mode -eq 'foreign') { $ready = $raw.StatusCode -eq 200; break }
            $health = $raw.Content | ConvertFrom-Json
            if ($health.service -eq 'optiforge-workbench' -and $health.protocol_version -eq 2) { $ready = $true; break }
            $ready = $true
            break
        } catch { }
        Start-Sleep -Milliseconds 150
    }
    if (-not $ready) { throw "fixture service failed to start on port $port (mode $Mode)" }
    return [pscustomobject]@{ Port = $port; Process = $process }
}

function Stop-OwnedProcess {
    param([int]$ProcessId)
    try {
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        $process.WaitForExit(5000) | Out-Null
    } catch { }
}

function Wait-PortClosed {
    param([int]$Port, [int]$TimeoutSeconds = 10)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($null -eq (Get-PortOwnerPid -Port $Port)) { return $true }
        Start-Sleep -Milliseconds 200
    }
    return $false
}

function Set-TestBrowserEnvironment {
    param([string]$Path, [string]$Roots, [bool]$Only)
    if ($Path) { $env:OPTIFORGE_LAUNCHER_TEST_BROWSER = $Path } else { Remove-Item Env:OPTIFORGE_LAUNCHER_TEST_BROWSER -ErrorAction SilentlyContinue }
    if ($Roots) { $env:OPTIFORGE_LAUNCHER_TEST_BROWSER_ROOTS = $Roots } else { Remove-Item Env:OPTIFORGE_LAUNCHER_TEST_BROWSER_ROOTS -ErrorAction SilentlyContinue }
    if ($Only) { $env:OPTIFORGE_LAUNCHER_TEST_BROWSER_ONLY = '1' } else { Remove-Item Env:OPTIFORGE_LAUNCHER_TEST_BROWSER_ONLY -ErrorAction SilentlyContinue }
}

function New-FakeBrowser {
    param([string]$Root)
    $path = Join-Path $Root 'chrome.exe'
    Copy-Item -LiteralPath (Join-Path $env:SystemRoot "System32\where.exe") -Destination $path -Force
    return $path
}

try {
    Check 'syntax: launcher parser clean' {
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile($script:Launcher, [ref]$null, [ref]$errors) | Out-Null
        Assert-True (($errors | Measure-Object).Count -eq 0) "parse errors: $($errors | Out-String)"
    }

    Check 'params: defaults and ValidateRange(0..65535)' {
        $tokens = $null
        $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($script:Launcher, [ref]$tokens, [ref]$errors)
        $port = $ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq 'Port' }
        Assert-True ($null -ne $port -and $port.DefaultValue.Extent.Text -eq '8765') 'Port default is not 8765'
        $attributeText = ($port.Attributes | ForEach-Object { $_.Extent.Text }) -join ' '
        Assert-True ($attributeText -match 'ValidateRange\(0,\s*65535\)') 'Port range attribute missing'
        Assert-PortRange -Value 0
        Assert-PortRange -Value 65535
        Assert-Throws { Assert-PortRange -Value -1 } '0..65535'
        Assert-Throws { Assert-PortRange -Value 65536 } '0..65535'
    }

    Check 'contract: WorkflowOnly + Model rejected' {
        Assert-Throws { Assert-LauncherParameters -Model 'a.zmx' -App $false -Browser 'Auto' -WorkflowOnly $true -BrowserSpecified $false -Port 8765 } '不能同时'
    }

    Check 'contract: Browser without App rejected' {
        Assert-Throws { Assert-LauncherParameters -Model '' -App $false -Browser 'Chrome' -WorkflowOnly $false -BrowserSpecified $true -Port 8765 } '仅与 -App'
    }

    Check 'contract: default parameters accepted' {
        Assert-LauncherParameters -Model '' -App $false -Browser 'Auto' -WorkflowOnly $false -BrowserSpecified $false -Port 8765
    }

    Check 'args: default / workflow / model / service-file' {
        $default = Get-WorkbenchArgumentList -Port 8765 -WorkflowOnly $false -WorkbenchPath 'C:\a b\workbench.py' -Model '' -ServiceFile ''
        Assert-True ($default -contains '--port' -and $default -contains '8765') 'default args missing port'
        Assert-True (-not ($default -contains '--workflow-only')) 'default args wrongly have workflow flag'
        $workflow = Get-WorkbenchArgumentList -Port 0 -WorkflowOnly $true -WorkbenchPath 'x' -Model '' -ServiceFile ''
        Assert-True ($workflow -contains '--workflow-only') 'workflow flag missing'
        $model = Get-WorkbenchArgumentList -Port 1 -WorkflowOnly $false -WorkbenchPath 'x' -Model 'C:\m y\a.zmx' -ServiceFile ''
        Assert-True ($model -contains '--model' -and $model -contains 'C:\m y\a.zmx') 'model args missing'
        $serviceFile = Get-WorkbenchArgumentList -Port 1 -WorkflowOnly $false -WorkbenchPath 'x' -Model '' -ServiceFile 'C:\s p\service.json'
        Assert-True ($serviceFile -contains '--service-file' -and $serviceFile -contains 'C:\s p\service.json') 'service-file args missing'
        $text = Get-WorkbenchArguments -Port 8765 -WorkflowOnly $false -WorkbenchPath 'C:\a b\workbench.py' -Model 'C:\m y\a.zmx' -ServiceFile 'C:\s p\s.json'
        Assert-True ($text -match '-B "C:\\a b\\workbench\.py" --port 8765') "unexpected string: $text"
        Assert-True ($text -match '--model "C:\\m y\\a\.zmx"') "model not quoted: $text"
        Assert-True ($text -match '--service-file "C:\\s p\\s\.json"') "service-file not quoted: $text"
    }

    Check 'browser args: app window, isolated profile, no security downgrade' {
        $args = Get-BrowserArguments -Url 'http://127.0.0.1:4321/' -ProfileDirectory 'C:\x y\chrome'
        Assert-True ($args -match '--app="http://127\.0\.0\.1:4321/"') 'missing --app'
        Assert-True ($args -match '--user-data-dir="C:\\x y\\chrome"') 'missing isolated profile'
        Assert-True ($args -notmatch '--no-sandbox|--disable-security|--disable-web-security') 'security downgrade flag present'
    }

    Check 'browser resolution: test roots, missing browser' {
        $empty = New-TempRoot
        $fakeRoot = New-TempRoot
        New-Item -ItemType File -Path (Join-Path $fakeRoot 'chrome.exe') | Out-Null
        Set-TestBrowserEnvironment -Roots $empty -Only $true
        Assert-True ($null -eq (Resolve-Browser -Browser 'Chrome')) 'unexpected browser found'
        Assert-Throws { Open-AppWindow -ResolvedBrowser $null -Url 'http://127.0.0.1:1/' -ProfileRoot $empty } '未找到 Chrome 或 Edge'
        Set-TestBrowserEnvironment -Roots $fakeRoot -Only $true
        $resolved = Resolve-Browser -Browser 'Chrome'
        Assert-True ($null -ne $resolved -and $resolved.Path -eq (Join-Path $fakeRoot 'chrome.exe')) 'fake browser not resolved'
        Set-TestBrowserEnvironment
    }

    Check 'lock: mutual exclusion and release' {
        $path = Join-Path (New-TempRoot) 'launch.lock'
        $first = Enter-LaunchLock -Path $path -TimeoutSeconds 2
        Assert-Throws { Enter-LaunchLock -Path $path -TimeoutSeconds 1 } '互斥锁被占用'
        Exit-LaunchLock -Lock $first
        $second = Enter-LaunchLock -Path $path -TimeoutSeconds 2
        Exit-LaunchLock -Lock $second
    }

    Check 'probe: matching workflow service is online and reusable' {
        $root = New-TempRoot
        $fixture = Start-Fixture -Root $root -Mode 'workflow' -Token 'tok-1' -Instance 'inst-1'
        try {
            Write-ServiceFile -Root $root -Port $fixture.Port -Token 'tok-1' -Instance 'inst-1'
            $file = Get-ServiceFilePath -Root $root
            $status = Get-OnlineServiceStatus -ServiceFile $file
            Assert-True ($status.State -eq 'online') "state=$($status.State)"
            Assert-True ($status.Pid -eq $fixture.Process.Id) 'PID mismatch'
            Assert-True (Test-ServiceMatchesRequest -Status $status -Port $fixture.Port -WorkflowOnly $true) 'should match port/mode'
            Assert-True (Test-ServiceMatchesRequest -Status $status -Port 0 -WorkflowOnly $true) 'Port 0 should match'
        } finally { Stop-OwnedProcess -ProcessId $fixture.Process.Id }
    }

    Check 'probe: different requested port refuses, credentials and PID intact' {
        $root = New-TempRoot
        $fixture = Start-Fixture -Root $root -Mode 'workflow' -Token 'tok-2' -Instance 'inst-2'
        try {
            Write-ServiceFile -Root $root -Port $fixture.Port -Token 'tok-2' -Instance 'inst-2'
            $file = Get-ServiceFilePath -Root $root
            $before = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
            $other = Get-FreePort
            Assert-Throws { Assert-NoConflictingOnlineService -ServiceFile $file -Port $other -WorkflowOnly $true -Model '' } '不匹配|不覆盖'
            Assert-True ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -eq $before) 'service.json changed'
            Assert-True ($null -ne (Get-Process -Id $fixture.Process.Id -ErrorAction SilentlyContinue)) 'service was killed'
        } finally { Stop-OwnedProcess -ProcessId $fixture.Process.Id }
    }

    Check 'probe: capability mismatch refuses' {
        $root = New-TempRoot
        $fixture = Start-Fixture -Root $root -Mode 'normal' -Token 'tok-3' -Instance 'inst-3'
        try {
            Write-ServiceFile -Root $root -Port $fixture.Port -Token 'tok-3' -Instance 'inst-3'
            $file = Get-ServiceFilePath -Root $root
            $before = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
            Assert-Throws { Assert-NoConflictingOnlineService -ServiceFile $file -Port $fixture.Port -WorkflowOnly $true -Model '' } '不匹配|不覆盖'
            Assert-Throws { Assert-NoConflictingOnlineService -ServiceFile $file -Port $fixture.Port -WorkflowOnly $false -Model 'x.zmx' } '不会隐式载入'
            Assert-True ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -eq $before) 'service.json changed'
        } finally { Stop-OwnedProcess -ProcessId $fixture.Process.Id }
    }

    Check 'probe: token case-only change is identity mismatch (case-sensitive)' {
        $root = New-TempRoot
        $fixture = Start-Fixture -Root $root -Mode 'workflow' -Token 'Tok-Case' -Instance 'inst-4'
        try {
            Write-ServiceFile -Root $root -Port $fixture.Port -Token 'tok-case' -Instance 'inst-4'
            $file = Get-ServiceFilePath -Root $root
            $status = Get-OnlineServiceStatus -ServiceFile $file
            Assert-True ($status.State -eq 'identity-mismatch') "state=$($status.State)"
            Assert-Throws { Assert-NoConflictingOnlineService -ServiceFile $file -Port $fixture.Port -WorkflowOnly $true -Model '' } '拒绝覆盖'
        } finally { Stop-OwnedProcess -ProcessId $fixture.Process.Id }
    }

    Check 'probe: foreign health / bad protocol are unverified, never stale' {
        foreach ($mode in 'foreign', 'bad-protocol') {
            $root = New-TempRoot
            $fixture = Start-Fixture -Root $root -Mode $mode -Token 't' -Instance 'i'
            try {
                Write-ServiceFile -Root $root -Port $fixture.Port -Token 't' -Instance 'i'
                $file = Get-ServiceFilePath -Root $root
                $status = Get-OnlineServiceStatus -ServiceFile $file
                Assert-True ($status.State -eq 'unverified') "$mode state=$($status.State)"
                Assert-Throws { Assert-NoConflictingOnlineService -ServiceFile $file -Port 0 -WorkflowOnly $true -Model '' } '拒绝覆盖'
                Assert-True ($null -ne (Get-Process -Id $fixture.Process.Id -ErrorAction SilentlyContinue)) "$mode service was killed"
            } finally { Stop-OwnedProcess -ProcessId $fixture.Process.Id }
        }
    }

    Check 'malformed credentials: unreadable JSON / bad URL / bad schema rejected' {
        $root = New-TempRoot
        $dir = Join-Path $root 'output\workbench_service'
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $file = Join-Path $dir 'service.json'
        Set-Content -LiteralPath $file -Value 'not-json' -Encoding utf8
        $status = Get-OnlineServiceStatus -ServiceFile $file
        Assert-True ($status.State -eq 'unreadable') "invalid JSON state=$($status.State)"
        Assert-Throws { Assert-NoConflictingOnlineService -ServiceFile $file -Port 0 -WorkflowOnly $true -Model '' } '无法解析|人工检查'
        Set-Content -LiteralPath $file -Value '{"url":"http://127.0.0.1:1234/?x=1","token":"t","protocol_version":2,"service_instance_id":"i"}' -Encoding utf8
        $status = Get-OnlineServiceStatus -ServiceFile $file
        Assert-True ($status.State -eq 'unreadable') "bad URL state=$($status.State)"
        Assert-Throws { Assert-NoConflictingOnlineService -ServiceFile $file -Port 0 -WorkflowOnly $true -Model '' } '无法解析|人工检查'

        foreach ($mode in 'bad-status', 'missing-token', 'bad-capability') {
            $fixture = Start-Fixture -Root $root -Mode $mode -Token 't' -Instance 'i'
            try {
                Write-ServiceFile -Root $root -Port $fixture.Port -Token 't' -Instance 'i'
                $status = Get-OnlineServiceStatus -ServiceFile $file
                Assert-True ($status.State -eq 'unverified') "$mode state=$($status.State)"
                Assert-Throws { Assert-NoConflictingOnlineService -ServiceFile $file -Port 0 -WorkflowOnly $true -Model '' } '拒绝覆盖'
                Assert-True ($null -ne (Get-Process -Id $fixture.Process.Id -ErrorAction SilentlyContinue)) "$mode service was killed"
            } finally { Stop-OwnedProcess -ProcessId $fixture.Process.Id }
        }
    }
    Check 'App missing browser: fails before starting any service' {
        $root = New-TempRoot
        Initialize-RunRoot -Root $root
        $fixture = Start-Fixture -Root $root -Mode 'workflow' -Token 'tok-5' -Instance 'inst-5'
        try {
            Write-ServiceFile -Root $root -Port $fixture.Port -Token 'tok-5' -Instance 'inst-5'
            $file = Get-ServiceFilePath -Root $root
            $before = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
            Set-TestBrowserEnvironment -Roots (New-TempRoot) -Only $true
            Assert-Throws {
                Invoke-WorkbenchLauncher -Model '' -Port $fixture.Port -SkipBuild $true -App $true `
                    -Browser 'Chrome' -WorkflowOnly $true -BrowserSpecified $false -WorkspacePath $root
            } '不会启动服务'
            Assert-True ($null -ne (Get-Process -Id $fixture.Process.Id -ErrorAction SilentlyContinue)) 'existing service was killed'
            Assert-True ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -eq $before) 'service.json changed'
        } finally {
            Set-TestBrowserEnvironment
            Stop-OwnedProcess -ProcessId $fixture.Process.Id
        }
    }

    Check 'default foreground refuses online record without overwriting it' {
        $root = New-TempRoot
        Initialize-RunRoot -Root $root
        $fixture = Start-Fixture -Root $root -Mode 'workflow' -Token 'tok-6' -Instance 'inst-6'
        try {
            Write-ServiceFile -Root $root -Port $fixture.Port -Token 'tok-6' -Instance 'inst-6'
            $file = Get-ServiceFilePath -Root $root
            $before = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
            Assert-Throws {
                Invoke-WorkbenchLauncher -Model '' -Port 0 -SkipBuild $true -App $false `
                    -Browser 'Auto' -WorkflowOnly $true -BrowserSpecified $false -WorkspacePath $root
            } '已有在线服务'
            Assert-True ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -eq $before) 'service.json changed'
            Assert-True ($null -ne (Get-Process -Id $fixture.Process.Id -ErrorAction SilentlyContinue)) 'service was killed'
        } finally { Stop-OwnedProcess -ProcessId $fixture.Process.Id }
    }

    Check 'App new service: temp credential promoted only after browser launch' {
        $root = New-TempRoot
        Initialize-RunRoot -Root $root
        $fakeBrowser = New-FakeBrowser -Root $root
        Set-TestBrowserEnvironment -Path $fakeBrowser
        $service = $null
        try {
            Invoke-WorkbenchLauncher -Model '' -Port 0 -SkipBuild $true -App $true `
                -Browser 'Chrome' -WorkflowOnly $true -BrowserSpecified $false -WorkspacePath $root
            $file = Get-ServiceFilePath -Root $root
            Assert-True (Test-Path -LiteralPath $file) 'promoted service.json missing'
            Assert-True ((Get-ChildItem (Join-Path $root 'output\workbench_service') -Filter 'service.pending.*' -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0) 'pending credential leftover'
            $status = Get-OnlineServiceStatus -ServiceFile $file
            Assert-True ($status.State -eq 'online') "state=$($status.State)"
            Assert-True ($status.Port -gt 0 -and $status.Port -notin @(8765, 8794, 49620)) "port=$($status.Port)"
            $service = $status
        } finally {
            Set-TestBrowserEnvironment
            if ($service) {
                Stop-OwnedProcess -ProcessId $service.Pid
                Assert-True (Wait-PortClosed -Port $service.Port) 'service port still open'
            }
        }
    }

    Check 'App new service promotes a legal offline credential atomically' {
        $root = New-TempRoot
        Initialize-RunRoot -Root $root
        $serviceDir = Join-Path $root 'output\workbench_service'
        New-Item -ItemType Directory -Force -Path $serviceDir | Out-Null
        $official = Join-Path $serviceDir 'service.json'
        Set-Content -LiteralPath $official -Value '{"url":"http://127.0.0.1:1/","token":"stale","protocol_version":2,"service_instance_id":"stale"}' -Encoding utf8
        $before = (Get-FileHash -LiteralPath $official -Algorithm SHA256).Hash
        $fakeBrowser = New-FakeBrowser -Root $root
        Set-TestBrowserEnvironment -Path $fakeBrowser
        $service = $null
        try {
            Invoke-WorkbenchLauncher -Model '' -Port 0 -SkipBuild $true -App $true `
                -Browser 'Chrome' -WorkflowOnly $true -BrowserSpecified $false -WorkspacePath $root
            $after = (Get-FileHash -LiteralPath $official -Algorithm SHA256).Hash
            Assert-True ($after -ne $before) 'official credential was not replaced'
            Assert-True ((Get-ChildItem $serviceDir -Filter 'service.pending.*' -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0) 'pending credential leftover'
            Assert-True ((Get-ChildItem $serviceDir -Filter 'service.backup.*' -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0) 'backup credential leftover'
            $service = Get-OnlineServiceStatus -ServiceFile $official
            Assert-True ($service.State -eq 'online') "state=$($service.State)"
            Assert-True ($service.Port -gt 0 -and $service.Port -notin @(8765, 8794, 49620)) "port=$($service.Port)"
            Assert-True ($service.InstanceId -ne 'stale') 'stale identity was retained'
        } finally {
            Set-TestBrowserEnvironment
            if ($service -and $service.State -eq 'online') {
                Stop-OwnedProcess -ProcessId $service.Pid
                Assert-True (Wait-PortClosed -Port $service.Port) 'service port still open'
            }
        }
    }

    Check 'App promotion failure preserves old bytes, stops own service, clears pending' {
        $root = New-TempRoot
        Initialize-RunRoot -Root $root
        $serviceDir = Join-Path $root 'output\workbench_service'
        New-Item -ItemType Directory -Force -Path $serviceDir | Out-Null
        $official = Join-Path $serviceDir 'service.json'
        Set-Content -LiteralPath $official -Value '{"url":"http://127.0.0.1:1/","token":"stale","protocol_version":2,"service_instance_id":"stale"}' -Encoding utf8
        $before = (Get-FileHash -LiteralPath $official -Algorithm SHA256).Hash
        (Get-Item -LiteralPath $official).IsReadOnly = $true
        $fakeBrowser = New-FakeBrowser -Root $root
        Set-TestBrowserEnvironment -Path $fakeBrowser
        try {
            Assert-Throws {
                Invoke-WorkbenchLauncher -Model '' -Port 0 -SkipBuild $true -App $true `
                    -Browser 'Chrome' -WorkflowOnly $true -BrowserSpecified $false -WorkspacePath $root
            } '提升失败'
            Assert-True ((Get-FileHash -LiteralPath $official -Algorithm SHA256).Hash -eq $before) 'old credential bytes changed after promotion failure'
            Assert-True ((Get-ChildItem $serviceDir -Filter 'service.pending.*' -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0) 'pending credential leftover'
            Assert-True ((Get-ChildItem $serviceDir -Filter 'service.backup.*' -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0) 'backup credential leftover'
            $left = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -and $_.CommandLine -like "*$root*workbench.py*" }
            Assert-True (($left | Measure-Object).Count -eq 0) 'own service survived promotion failure'
        } finally {
            (Get-Item -LiteralPath $official).IsReadOnly = $false
            Set-TestBrowserEnvironment
        }
    }

    Check 'App reuses online credentials without rewriting them' {
        $root = New-TempRoot
        Initialize-RunRoot -Root $root
        $fixture = Start-Fixture -Root $root -Mode 'workflow' -Token 'tok-reuse' -Instance 'inst-reuse'
        try {
            Write-ServiceFile -Root $root -Port $fixture.Port -Token 'tok-reuse' -Instance 'inst-reuse'
            $file = Get-ServiceFilePath -Root $root
            $before = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
            $fakeBrowser = New-FakeBrowser -Root $root
            Set-TestBrowserEnvironment -Path $fakeBrowser
            Invoke-WorkbenchLauncher -Model '' -Port $fixture.Port -SkipBuild $true -App $true `
                -Browser 'Chrome' -WorkflowOnly $true -BrowserSpecified $false -WorkspacePath $root
            Assert-True ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -eq $before) 'online credential was rewritten'
            Assert-True ($null -ne (Get-Process -Id $fixture.Process.Id -ErrorAction SilentlyContinue)) 'online service was stopped'
        } finally {
            Set-TestBrowserEnvironment
            Stop-OwnedProcess -ProcessId $fixture.Process.Id
        }
    }

    Check 'App browser launch failure: service stopped, old credentials preserved' {
        $root = New-TempRoot
        Initialize-RunRoot -Root $root
        # 旧（陈旧）正式凭据：失败后必须逐字节保持。
        $serviceDir = Join-Path $root 'output\workbench_service'
        New-Item -ItemType Directory -Force -Path $serviceDir | Out-Null
        $official = Join-Path $serviceDir 'service.json'
        Set-Content -LiteralPath $official -Value '{"url":"http://127.0.0.1:1/","token":"stale","protocol_version":2,"service_instance_id":"stale"}' -Encoding utf8
        $before = (Get-FileHash -LiteralPath $official -Algorithm SHA256).Hash
        $badBrowser = Join-Path $root 'chrome.exe'
        Set-Content -LiteralPath $badBrowser -Value 'not-an-executable'
        Set-TestBrowserEnvironment -Path $badBrowser
        try {
            Assert-Throws {
                Invoke-WorkbenchLauncher -Model '' -Port 0 -SkipBuild $true -App $true `
                    -Browser 'Chrome' -WorkflowOnly $true -BrowserSpecified $false -WorkspacePath $root
            } '浏览器启动失败|not a valid|不是有效'
            Assert-True ((Get-FileHash -LiteralPath $official -Algorithm SHA256).Hash -eq $before) 'official credentials changed after failure'
            Assert-True ((Get-ChildItem $serviceDir -Filter 'service.pending.*' -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0) 'pending credential leftover'
            $left = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -and $_.CommandLine -like "*$root*workbench.py*" }
            Assert-True (($left | Measure-Object).Count -eq 0) 'service process survived browser failure'
        } finally { Set-TestBrowserEnvironment }
    }

    Check 'failed start: old official credentials byte-identical, temp cleaned' {
        $root = New-TempRoot
        $serviceDir = Join-Path $root 'output\workbench_service'
        New-Item -ItemType Directory -Force -Path $serviceDir | Out-Null
        $official = Join-Path $serviceDir 'service.json'
        Set-Content -LiteralPath $official -Value '{"url":"http://127.0.0.1:1/","token":"stale","protocol_version":2,"service_instance_id":"stale"}' -Encoding utf8
        $before = (Get-FileHash -LiteralPath $official -Algorithm SHA256).Hash
        $temporary = Join-Path $serviceDir 'service.pending.fail.json'
        Assert-Throws {
            Start-WorkbenchService -Port 0 -WorkflowOnly $true -WorkspacePath $root `
                -OutputDirectory $serviceDir -ServiceFile $temporary -PythonExecutable $env:ComSpec -TimeoutSeconds 4
        } '未在'
        Assert-True ((Get-FileHash -LiteralPath $official -Algorithm SHA256).Hash -eq $before) 'official credentials changed'
        Assert-True (-not (Test-Path -LiteralPath $temporary)) 'temporary credential not removed'
    }

    Check 'default foreground: real temp service starts, then stops by PID' {
        $root = New-TempRoot
        Initialize-RunRoot -Root $root
        $launcherCopy = Join-Path $root 'start-workbench.ps1'
        Copy-Item -LiteralPath $script:Launcher -Destination $launcherCopy
        $outLog = Join-Path $root 'default.out.log'
        $errLog = Join-Path $root 'default.err.log'
        $arguments = '-NoProfile -File "' + $launcherCopy + '" -SkipBuild -Port 0 -WorkflowOnly'
        $process = Start-Process -FilePath $script:HostExe -ArgumentList $arguments `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru -WindowStyle Hidden
        $script:Pids += $process.Id
        $file = Get-ServiceFilePath -Root $root
        $status = $null
        $deadline = (Get-Date).AddSeconds(90)
        while ((Get-Date) -lt $deadline) {
            if ($process.HasExited) { break }
            $candidate = Get-OnlineServiceStatus -ServiceFile $file
            if ($candidate.State -eq 'online') { $status = $candidate; break }
            Start-Sleep -Milliseconds 250
        }
        Assert-True ($null -ne $status) 'default foreground did not become ready'
        Assert-True ($status.Port -gt 0 -and $status.Port -notin @(8765, 8794, 49620)) "port=$($status.Port)"
        Stop-OwnedProcess -ProcessId $status.Pid
        Assert-True (Wait-PortClosed -Port $status.Port) 'port still open after stopping service'
        $null = $process.WaitForExit(20000)
        if (-not $process.HasExited) { Stop-OwnedProcess -ProcessId $process.Id }
    }
    Check 'timeout cleanup: own child killed, foreign listener and credentials intact' {
        $root = New-TempRoot
        $serviceDir = Join-Path $root 'output\workbench_service'
        New-Item -ItemType Directory -Force -Path $serviceDir | Out-Null
        $official = Join-Path $serviceDir 'service.json'
        Set-Content -LiteralPath $official -Value '{"url":"http://127.0.0.1:1/","token":"stale","protocol_version":2,"service_instance_id":"stale"}' -Encoding utf8
        $before = (Get-FileHash -LiteralPath $official -Algorithm SHA256).Hash
        @'
import time
print("slow child", flush=True)
time.sleep(30)
'@ | Set-Content -LiteralPath (Join-Path $root 'workbench.py') -Encoding ascii
        $foreign = Start-Fixture -Root $root -Mode 'workflow' -Token 'foreign' -Instance 'foreign'
        try {
            $threw = $false
            try {
                Start-WorkbenchService -Port $foreign.Port -WorkflowOnly $true -WorkspacePath $root `
                    -OutputDirectory $serviceDir -TimeoutSeconds 1 | Out-Null
            } catch { $threw = $true }
            Assert-True $threw 'expected a timeout'
            $deadline = (Get-Date).AddSeconds(5)
            $own = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -and $_.CommandLine -like "*$root*workbench.py*" })
            while ($own.Count -gt 0 -and (Get-Date) -lt $deadline) {
                Start-Sleep -Milliseconds 200
                $own = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
                    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$root*workbench.py*" })
            }
            Assert-True ($own.Count -eq 0) "own child survived timeout: $($own.ProcessId -join ',')"
            Assert-True ($null -ne (Get-Process -Id $foreign.Process.Id -ErrorAction SilentlyContinue)) 'unrelated listener was killed'
            Assert-True ((Get-FileHash -LiteralPath $official -Algorithm SHA256).Hash -eq $before) 'official credentials changed on timeout'
        } finally { Stop-OwnedProcess -ProcessId $foreign.Process.Id }
    }

    Check 'concurrent App launches: single service, valid credentials' {
        $root = New-TempRoot
        Initialize-RunRoot -Root $root
        $fakeBrowser = New-FakeBrowser -Root $root
        $env:OPTIFORGE_LAUNCHER_TEST_BROWSER = $fakeBrowser
        $out1 = Join-Path $root 'c1.out.log'; $err1 = Join-Path $root 'c1.err.log'
        $out2 = Join-Path $root 'c2.out.log'; $err2 = Join-Path $root 'c2.err.log'
        $launcherCopy = Join-Path $root 'start-workbench.ps1'
        Copy-Item -LiteralPath $script:Launcher -Destination $launcherCopy
        $arguments = '-NoProfile -File "' + $launcherCopy + '" -SkipBuild -App -Port 0'
        $first = Start-Process -FilePath $script:HostExe -ArgumentList $arguments `
            -WorkingDirectory $script:Root -RedirectStandardOutput $out1 -RedirectStandardError $err1 -PassThru -WindowStyle Hidden
        $second = Start-Process -FilePath $script:HostExe -ArgumentList $arguments `
            -WorkingDirectory $script:Root -RedirectStandardOutput $out2 -RedirectStandardError $err2 -PassThru -WindowStyle Hidden
        $script:Pids += $first.Id
        $script:Pids += $second.Id
        $null = $first.WaitForExit(120000)
        $null = $second.WaitForExit(120000)
        $first.Refresh(); $second.Refresh()
        Assert-True ($first.HasExited -and $second.HasExited) 'concurrent launchers did not finish'
        $file = Get-ServiceFilePath -Root $root
        $status = Get-OnlineServiceStatus -ServiceFile $file
        Assert-True ($status.State -eq 'online') "credentials invalid after concurrency: $($status.State)"
        Assert-True ((Get-ChildItem (Join-Path $root 'output\workbench_service') -Filter 'service.pending.*' -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0) 'pending credential leftover'
        $services = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -like "*$root*workbench.py*" })
        Assert-True ($services.Count -eq 1) "expected 1 service process, got $($services.Count)"
        $codes = @($first.ExitCode, $second.ExitCode) | ForEach-Object {
            if ($null -eq $_) { 'unknown' } else { [string]$_ }
        }
        $successLogs = @(Select-String -Path $out1, $out2 -Pattern 'OptiForge: http' -ErrorAction SilentlyContinue)
        Assert-True ($successLogs.Count -ge 1) "no launcher reported success; exit codes: $($codes -join ',')"
        Stop-OwnedProcess -ProcessId $status.Pid
        Assert-True (Wait-PortClosed -Port $status.Port) 'service port still open'
        Set-TestBrowserEnvironment
    }
} finally {
    try { Set-TestBrowserEnvironment } catch { }
    foreach ($processId in $script:Pids) { Stop-OwnedProcess -ProcessId $processId }
    foreach ($root in $script:TempRoots) {
        try {
            $owned = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($root) }
            foreach ($process in $owned) { Stop-OwnedProcess -ProcessId $process.ProcessId }
            $services = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -and $_.CommandLine -like "*$root*" }
            foreach ($service in $services) { Stop-OwnedProcess -ProcessId $service.ProcessId }
            $resolvedRoot = [System.IO.Path]::GetFullPath($root)
            $expectedParent = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd('\')
            if ((Split-Path -Parent $resolvedRoot).TrimEnd('\') -ine $expectedParent -or
                [System.IO.Path]::GetFileName($resolvedRoot) -notmatch '^optiforge launcher test [a-f0-9]{32}$') {
                throw "Refusing cleanup outside the owned temporary directory: $resolvedRoot"
            }
            Remove-Item -LiteralPath $resolvedRoot -Recurse -Force -ErrorAction SilentlyContinue
        } catch { }
    }
    # 清理结果必须实际核对，不用 SilentlyContinue 掩盖残留。
    $leftPids = @($script:Pids | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
    $leftRoots = @($script:TempRoots | Where-Object { Test-Path -LiteralPath $_ })
    $leftProcs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $proc = $_
        $proc.ExecutablePath -and ($script:TempRoots | Where-Object { $proc.ExecutablePath.StartsWith($_) })
    })
    if ($leftPids.Count -gt 0 -or $leftRoots.Count -gt 0 -or $leftProcs.Count -gt 0) {
        $script:Results += [pscustomobject]@{
            name  = 'cleanup: no owned PIDs / temp roots / child processes'
            ok    = $false
            error = "pids=$($leftPids -join ',') roots=$($leftRoots -join ',') procs=$($leftProcs.ProcessId -join ',')"
        }
    }
}

$passed = ($script:Results | Where-Object { $_.ok }).Count
$failed = @($script:Results | Where-Object { -not $_.ok }).Count
[pscustomobject]@{ total = $script:Results.Count; passed = $passed; failed = $failed } | ConvertTo-Json
if ($failed -gt 0) {
    $script:Results | Where-Object { -not $_.ok } | Format-Table -AutoSize | Out-String | Write-Host
    exit 1
}
exit 0
