#requires -Version 5.1
<#
    Synthetic window/service snapshots only: no network, CIM or live EnumWindows.
    Command-line strings do use the real CommandLineToArgvW/LocalFree parser.
    Run directly with powershell.exe or pwsh.exe -NoProfile -File this-file.ps1.
#>
$ErrorActionPreference = 'Stop'
$windowTestRoot = Split-Path -Parent $PSScriptRoot
$windowTestSource = Join-Path $windowTestRoot 'get-workbench-window.ps1'
. $windowTestSource
$script:WindowChecks = 0
$script:WindowFailures = @()
$script:WindowChromeExe = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$script:WindowEdgeExe = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$script:WindowChromeProfile = 'C:\isolated work\output\desktop_profile\chrome'
$script:WindowEdgeProfile = 'C:\isolated work\output\desktop_profile\edge'

function Assert-WindowTest {
    param([bool]$Condition, [string]$Message = 'assertion failed')
    if (-not $Condition) { throw $Message }
}
function Check-WindowTest {
    param([string]$Name, [scriptblock]$Body)
    $script:WindowChecks++
    try { & $Body; Write-Output ('PASS ' + $Name) }
    catch { $script:WindowFailures += $Name; Write-Output ('FAIL ' + $Name + ': ' + $_.Exception.Message) }
}
function New-WindowService {
    [pscustomobject]@{ State='online'; Url='http://127.0.0.1:43210/'; InstanceId='test-instance'; Pid=123;
        Credential=[pscustomobject]@{ token='SECRET_TOKEN'; service_instance_id='test-instance' }; token='SECRET_TOKEN' }
}
function New-WindowBrowsers {
    @([pscustomobject]@{ Name='Chrome'; ExecutablePath=$script:WindowChromeExe; ProfilePath=$script:WindowChromeProfile },
      [pscustomobject]@{ Name='Edge'; ExecutablePath=$script:WindowEdgeExe; ProfilePath=$script:WindowEdgeProfile })
}
function New-WindowProcess {
    param([uint32]$Number = 200, [string]$Executable = $script:WindowChromeExe, [string]$Profile = $script:WindowChromeProfile)
    [pscustomobject]@{ ProcessId=$Number; Name=[IO.Path]::GetFileName($Executable); ExecutablePath=$Executable;
        CommandLine=('"' + $Executable + '" --user-data-dir="' + $Profile + '" --app=http://127.0.0.1:43210/ --test-secret=SECRET_COMMAND');
        CreationTime='2026-11-03T00:00:01.0000000Z' }
}
function New-WindowRow {
    param([string]$Handle = '9007199254740993', [uint32]$Number = 200)
    [pscustomobject]@{ Hwnd=$Handle; ProcessId=$Number; Visible=$true; Minimized=$false; ObservationValid=$true;
        ClassName='Chrome_WidgetWin_1'; Rect=[int[]]@(-10,20,1430,980); Dpi=[uint32]144; Title='Same title as unrelated app' }
}
function New-WindowSnapshot {
    param([array]$Windows = @((New-WindowRow)), [array]$Processes = @((New-WindowProcess)))
    [pscustomobject]@{ Complete=$true; Windows=$Windows; Processes=$Processes }
}
function Invoke-WindowFixture {
    param($Before = (New-WindowSnapshot), $After = $null, $FirstService = (New-WindowService),
        $LastService = $null, [array]$Browsers = (New-WindowBrowsers))
    if ($null -eq $After) { $After = $Before }
    if ($null -eq $LastService) { $LastService = $FirstService }
    Resolve-WorkbenchWindowDiagnostic -ServiceBefore $FirstService -ServiceAfter $LastService -Browsers $Browsers -Before $Before -After $After
}
function Assert-WindowUnavailable {
    param($Result)
    Assert-WindowTest ($Result.status -ceq 'unavailable') 'must remain unavailable'
    Assert-WindowTest ($Result.candidates.Count -eq 0) 'partial candidates must not escape'
    Assert-WindowTest ($Result.url_binding -ceq 'unverified') 'URL binding must remain unverified'
}

Write-Output ('HOST version=' + $PSVersionTable.PSVersion + ' edition=' + $PSVersionTable.PSEdition)
Write-Output 'SCOPE synthetic snapshots + real argument parser; no live windows/network/services'

Check-WindowTest 'dot-source exposes functions without native reader or launcher entry' {
    Assert-WindowTest ($null -eq ('OptiForgeDesktopRead.Native' -as [type])) 'native reader initialized during dot-source'
    Assert-WindowTest ($null -ne (Get-Command Resolve-WorkbenchWindowDiagnostic -CommandType Function))
}
Check-WindowTest 'real native parser preserves quoted executable and profile with spaces' {
    $arguments = ConvertFrom-WorkbenchWindowCommandLine ('"' + $script:WindowChromeExe + '" --user-data-dir="' + $script:WindowChromeProfile + '" --name="a b"')
    Assert-WindowTest ($arguments.Count -eq 3)
    Assert-WindowTest ($arguments[0] -ceq $script:WindowChromeExe)
    Assert-WindowTest ($arguments[1] -ceq ('--user-data-dir=' + $script:WindowChromeProfile))
    Assert-WindowTest ($arguments[2] -ceq '--name=a b')
}
Check-WindowTest 'native parser rejects null empty leading blank and unterminated quotes' {
    foreach ($line in @($null, '', '   ', ' "C:\chrome.exe"', '"C:\chrome.exe" --user-data-dir="C:\unfinished', "x`0y")) {
        Assert-WindowTest ($null -eq (ConvertFrom-WorkbenchWindowCommandLine $line))
    }
}
Check-WindowTest 'zero stable matching windows means not_found with online service' {
    $result = Invoke-WindowFixture -Before (New-WindowSnapshot -Windows @() -Processes @())
    Assert-WindowTest ($result.status -ceq 'not_found')
    Assert-WindowTest ($result.candidates.Count -eq 0)
    Assert-WindowTest ($result.service.listener_pid -eq 123)
}
Check-WindowTest 'one stable profile window is only a unique_candidate' {
    $result = Invoke-WindowFixture
    Assert-WindowTest ($result.status -ceq 'unique_candidate')
    Assert-WindowTest ($result.candidates.Count -eq 1)
    Assert-WindowTest ($result.url_binding -ceq 'unverified' -and $result.candidates[0].url_binding -ceq 'unverified')
    Assert-WindowTest ($result.PSObject.Properties.Name -notcontains 'selected')
}
Check-WindowTest 'native handles remain lossless JSON strings above JS safe integer range' {
    $result = Invoke-WindowFixture
    $json = $result | ConvertTo-Json -Depth 8
    Assert-WindowTest ($result.candidates[0].hwnd -is [string])
    Assert-WindowTest ($json -match '"hwnd"\s*:\s*"9007199254740993"')
}
Check-WindowTest 'two same-process windows are ambiguous without a selected handle' {
    $snapshot = New-WindowSnapshot -Windows @((New-WindowRow -Handle '100'),(New-WindowRow -Handle '101'))
    $result = Invoke-WindowFixture -Before $snapshot
    Assert-WindowTest ($result.status -ceq 'ambiguous' -and $result.candidates.Count -eq 2)
    Assert-WindowTest ($result.PSObject.Properties.Name -notcontains 'selected')
}
Check-WindowTest 'Auto scope can contain both browser profiles and reports ambiguity' {
    $snapshot = New-WindowSnapshot -Windows @((New-WindowRow -Handle '100'),(New-WindowRow -Handle '101' -Number 201)) -Processes @(
        (New-WindowProcess),(New-WindowProcess -Number 201 -Executable $script:WindowEdgeExe -Profile $script:WindowEdgeProfile))
    Assert-WindowTest ((Invoke-WindowFixture -Before $snapshot).status -ceq 'ambiguous')
}
Check-WindowTest 'profile equality respects Windows case and canonical dot segments' {
    $process = New-WindowProcess -Profile 'c:\ISOLATED WORK\output\desktop_profile\x\..\CHROME\\'
    Assert-WindowTest ((Invoke-WindowFixture -Before (New-WindowSnapshot -Processes @($process))).status -ceq 'unique_candidate')
}
Check-WindowTest 'split profile flag is parsed without substring matching' {
    $process = New-WindowProcess
    $process.CommandLine = '"' + $script:WindowChromeExe + '" --user-data-dir "' + $script:WindowChromeProfile + '"'
    Assert-WindowTest ((Invoke-WindowFixture -Before (New-WindowSnapshot -Processes @($process))).status -ceq 'unique_candidate')
}
Check-WindowTest 'profile sibling and nested paths do not match' {
    foreach ($suffix in @('-other', '\nested')) {
        $process = New-WindowProcess -Profile ($script:WindowChromeProfile + $suffix)
        Assert-WindowTest ((Invoke-WindowFixture -Before (New-WindowSnapshot -Processes @($process))).status -ceq 'not_found')
    }
}
Check-WindowTest 'same title and filename from different executable is unrelated' {
    $process = New-WindowProcess -Executable 'C:\unrelated\chrome.exe'
    Assert-WindowTest ((Invoke-WindowFixture -Before (New-WindowSnapshot -Processes @($process))).status -ceq 'not_found')
}
Check-WindowTest 'title and origin text never establish binding' {
    $snapshot = New-WindowSnapshot
    $snapshot.Windows[0].Title = 'OptiForge http://127.0.0.1:43210/ SECRET_TITLE'
    $snapshot.Processes[0].CommandLine = '"' + $script:WindowChromeExe + '" --user-data-dir="' + $script:WindowChromeProfile + '" https://other-origin.example/'
    $result = Invoke-WindowFixture -Before $snapshot
    Assert-WindowTest ($result.status -ceq 'unique_candidate' -and $result.url_binding -ceq 'unverified')
    Assert-WindowTest (($result | ConvertTo-Json -Depth 8) -notmatch 'SECRET_TITLE|other-origin')
}
Check-WindowTest 'child --type process is excluded even with the matching profile' {
    foreach ($flag in @('--type=renderer', '--type gpu-process')) {
        $process = New-WindowProcess; $process.CommandLine += ' ' + $flag
        Assert-WindowTest ((Invoke-WindowFixture -Before (New-WindowSnapshot -Processes @($process))).status -ceq 'not_found')
    }
}
Check-WindowTest 'ordinary browser without a profile switch is excluded' {
    $process = New-WindowProcess; $process.CommandLine = '"' + $script:WindowChromeExe + '"'
    Assert-WindowTest ((Invoke-WindowFixture -Before (New-WindowSnapshot -Processes @($process))).status -ceq 'not_found')
}
Check-WindowTest 'empty child type and positional switch boundary are not proof of exclusion or ownership' {
    foreach ($flags in @('--type=', '--type', '--type --app=x', '-- --user-data-dir=C:\x')) {
        $process = New-WindowProcess; $process.CommandLine += ' ' + $flags
        Assert-WindowUnavailable (Invoke-WindowFixture -Before (New-WindowSnapshot -Processes @($process)))
    }
}
Check-WindowTest 'duplicate malformed missing relative and unsupported profile flags are unknown' {
    foreach ($flags in @(
        ('--user-data-dir="' + $script:WindowChromeProfile + '" --user-data-dir="C:\other"'),
        '--user-data-dir=', '--user-data-dir', '--user-data-dir --app=http://127.0.0.1/',
        '--user-data-dir=relative', '--user-data-dir=C:relative', '--user-data-directory=C:\other',
        ('--USER-DATA-DIR="' + $script:WindowChromeProfile + '"'), '--user-data-dir=\\?\C:\path')) {
        $process = New-WindowProcess; $process.CommandLine = '"' + $script:WindowChromeExe + '" ' + $flags
        Assert-WindowUnavailable (Invoke-WindowFixture -Before (New-WindowSnapshot -Processes @($process)))
    }
}
Check-WindowTest 'executable argument must match executable metadata exactly' {
    $process = New-WindowProcess; $process.CommandLine = $process.CommandLine.Replace($script:WindowChromeExe, 'C:\spoof\chrome.exe')
    Assert-WindowUnavailable (Invoke-WindowFixture -Before (New-WindowSnapshot -Processes @($process)))
}
Check-WindowTest 'same-executable visible window missing command line prevents uniqueness' {
    $process = New-WindowProcess -Number 201; $process.CommandLine = $null
    $snapshot = New-WindowSnapshot -Windows @((New-WindowRow -Handle '100'),(New-WindowRow -Handle '101' -Number 201)) -Processes @((New-WindowProcess),$process)
    Assert-WindowUnavailable (Invoke-WindowFixture -Before $snapshot)
}
Check-WindowTest 'relevant missing path name creation or process record is unknown' {
    foreach ($field in @('ExecutablePath','Name','CreationTime')) {
        $process = New-WindowProcess; $process.$field = $null
        Assert-WindowUnavailable (Invoke-WindowFixture -Before (New-WindowSnapshot -Processes @($process)))
    }
    Assert-WindowUnavailable (Invoke-WindowFixture -Before (New-WindowSnapshot -Processes @()))
}
Check-WindowTest 'unrelated known process name can be excluded without its private metadata' {
    $process = [pscustomobject]@{ ProcessId=200; Name='explorer.exe'; ExecutablePath=$null; CommandLine=$null; CreationTime=$null }
    Assert-WindowTest ((Invoke-WindowFixture -Before (New-WindowSnapshot -Processes @($process))).status -ceq 'not_found')
}
Check-WindowTest 'unknown process name and path cannot be silently excluded' {
    $process = New-WindowProcess; $process.Name=$null; $process.ExecutablePath=$null
    Assert-WindowUnavailable (Invoke-WindowFixture -Before (New-WindowSnapshot -Processes @($process)))
}
Check-WindowTest 'missing geometry class or partial enumeration prevents uniqueness' {
    foreach ($field in @('Rect','ClassName','ObservationValid')) {
        $snapshot = New-WindowSnapshot; $snapshot.Windows[0].$field = $null
        Assert-WindowUnavailable (Invoke-WindowFixture -Before $snapshot)
    }
    $snapshot = New-WindowSnapshot; $snapshot.Complete=$false
    Assert-WindowUnavailable (Invoke-WindowFixture -Before $snapshot)
}
Check-WindowTest 'minimized visible windows remain candidates with honest DPI' {
    $snapshot = New-WindowSnapshot; $snapshot.Windows[0].Minimized=$true; $snapshot.Windows[0].Dpi=$null
    $result = Invoke-WindowFixture -Before $snapshot
    Assert-WindowTest ($result.status -ceq 'unique_candidate' -and $result.candidates[0].minimized)
    Assert-WindowTest ($null -eq $result.candidates[0].dpi)
    Assert-WindowTest ($result.candidates[0].bounds.left -eq -10)
}
Check-WindowTest 'hidden windows do not count as visible top-level candidates' {
    $snapshot = New-WindowSnapshot; $snapshot.Windows[0].Visible=$false
    Assert-WindowTest ((Invoke-WindowFixture -Before $snapshot).status -ceq 'not_found')
}
Check-WindowTest 'appearing or disappearing matching window invalidates the entire observation' {
    $one = New-WindowSnapshot
    $two = New-WindowSnapshot -Windows @((New-WindowRow),(New-WindowRow -Handle '101'))
    Assert-WindowUnavailable (Invoke-WindowFixture -Before $one -After $two)
    Assert-WindowUnavailable (Invoke-WindowFixture -Before $two -After $one)
}
Check-WindowTest 'new unknown browser window in second enumeration prevents uniqueness' {
    $process = New-WindowProcess -Number 201; $process.CommandLine=$null
    $after = New-WindowSnapshot -Windows @((New-WindowRow),(New-WindowRow -Handle '101' -Number 201)) -Processes @((New-WindowProcess),$process)
    Assert-WindowUnavailable (Invoke-WindowFixture -After $after)
}
Check-WindowTest 'HWND reassignment and PID creation-time reuse are races' {
    $before = New-WindowSnapshot
    $after = New-WindowSnapshot -Windows @((New-WindowRow -Number 201)) -Processes @((New-WindowProcess -Number 201))
    Assert-WindowUnavailable (Invoke-WindowFixture -Before $before -After $after)
    $after = New-WindowSnapshot; $after.Processes[0].CreationTime='2026-11-03T00:00:02Z'
    Assert-WindowUnavailable (Invoke-WindowFixture -Before $before -After $after)
}
Check-WindowTest 'geometry and minimize changes do not pretend to be model changes' {
    $after = New-WindowSnapshot; $after.Windows[0].Rect=[int[]]@(0,0,1440,960); $after.Windows[0].Minimized=$true
    $result = Invoke-WindowFixture -After $after
    Assert-WindowTest ($result.status -ceq 'unique_candidate' -and $result.candidates[0].minimized)
    Assert-WindowTest ($result.candidates[0].bounds.left -eq 0)
}
Check-WindowTest 'duplicate HWND or conflicting process metadata is incomplete' {
    Assert-WindowUnavailable (Invoke-WindowFixture -Before (New-WindowSnapshot -Windows @((New-WindowRow),(New-WindowRow))))
    Assert-WindowUnavailable (Invoke-WindowFixture -Before (New-WindowSnapshot -Processes @((New-WindowProcess),(New-WindowProcess))))
}
Check-WindowTest 'malformed snapshot visibility HWND PID and arrays fail conservatively' {
    foreach ($pair in @(@('Hwnd','0'),@('ProcessId',0),@('Visible',$null))) {
        $snapshot = New-WindowSnapshot; $snapshot.Windows[0].($pair[0])=$pair[1]
        Assert-WindowUnavailable (Invoke-WindowFixture -Before $snapshot)
    }
    $snapshot = New-WindowSnapshot; $snapshot.Windows=$null
    Assert-WindowUnavailable (Invoke-WindowFixture -Before $snapshot)
}
Check-WindowTest 'offline mismatch malformed service never means no window' {
    foreach ($state in @('none','unreadable','offline','unverified','identity-mismatch')) {
        $service = New-WindowService; $service.State=$state
        Assert-WindowUnavailable (Invoke-WindowFixture -FirstService $service)
    }
    foreach ($url in @('https://127.0.0.1:43210/','http://remote.example/','http://127.0.0.1:43210/?secret=x','http://user@127.0.0.1:43210/')) {
        $service = New-WindowService; $service.Url=$url
        Assert-WindowUnavailable (Invoke-WindowFixture -FirstService $service)
    }
}
Check-WindowTest 'service origin instance and listener PID must all remain stable' {
    foreach ($field in @('Url','InstanceId','Pid','State')) {
        $service = New-WindowService
        $service.$field = switch ($field) { 'Url' { 'http://127.0.0.1:43211/' } 'InstanceId' { 'other-instance' } 'Pid' { 124 } 'State' { 'offline' } }
        Assert-WindowUnavailable (Invoke-WindowFixture -LastService $service)
    }
}
Check-WindowTest 'missing browser resolution and duplicate identities are unavailable' {
    Assert-WindowUnavailable (Invoke-WindowFixture -Browsers @())
    $browsers = New-WindowBrowsers
    Assert-WindowUnavailable (Invoke-WindowFixture -Browsers @($browsers[0],$browsers[0]))
}
Check-WindowTest 'only explicitly whitelisted observations leave the diagnostic' {
    $json = Invoke-WindowFixture | ConvertTo-Json -Depth 8
    Assert-WindowTest ($json -notmatch 'SECRET|Credential|CommandLine|ExecutablePath|Title|lease|token')
    $result = $json | ConvertFrom-Json
    Assert-WindowTest (($result.service.PSObject.Properties.Name -join ',') -ceq 'endpoint,instance_id,listener_pid')
}
Check-WindowTest 'coordinator takes two whole snapshots bracketed by service verification' {
    $script:WindowCallOrder = @()
    $readService = { param($root) $script:WindowCallOrder += 'service'; New-WindowService }
    $readBrowsers = { param($root,$browser) $script:WindowCallOrder += 'browsers'; New-WindowBrowsers }
    $readSnapshot = { $script:WindowCallOrder += 'snapshot'; New-WindowSnapshot }
    $result = Get-WorkbenchWindowDiagnostic -Root 'C:\synthetic-only' -ReadService $readService -ReadBrowsers $readBrowsers -ReadSnapshot $readSnapshot
    Assert-WindowTest ($result.status -ceq 'unique_candidate')
    Assert-WindowTest (($script:WindowCallOrder -join ',') -ceq 'service,browsers,snapshot,snapshot,service')
}
Check-WindowTest 'coordinator stops before browser or native reads when service is unavailable' {
    $readService = { param($root) [pscustomobject]@{ State='offline' } }
    $forbidden = { throw 'forbidden native/browser path' }
    $result = Get-WorkbenchWindowDiagnostic -ReadService $readService -ReadBrowsers $forbidden -ReadSnapshot $forbidden
    Assert-WindowUnavailable $result
    Assert-WindowTest ($result.reason -ceq 'service_unavailable')
}
Check-WindowTest 'provider exceptions are sanitized without raw exception or secret output' {
    $result = Get-WorkbenchWindowDiagnostic -ReadService { throw 'SECRET_EXCEPTION' }
    Assert-WindowUnavailable $result
    Assert-WindowTest (($result | ConvertTo-Json -Depth 8) -notmatch 'SECRET_EXCEPTION')
}
Check-WindowTest 'diagnostic source has observational native imports only' {
    $source = Get-Content -LiteralPath $windowTestSource -Raw
    Assert-WindowTest ($source -notmatch 'SetWindowPos|ShowWindow|SendMessage|SetForegroundWindow|SendInput|Start-Process|Stop-Process|Invoke-WorkbenchLauncher\s+-')
    Assert-WindowTest ($source -match 'CommandLineToArgvW' -and $source -match 'finally \{ if \(LocalFree\(memory\) != IntPtr.Zero\)')
}

Write-Output ('TOTAL=' + $script:WindowChecks + ' PASS=' + ($script:WindowChecks - $script:WindowFailures.Count) + ' FAIL=' + $script:WindowFailures.Count)
if ($script:WindowFailures.Count -gt 0) { exit 1 }
exit 0
