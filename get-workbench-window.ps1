#requires -Version 5.1
<#
.SYNOPSIS
    Read-only, point-in-time diagnostics for this workspace's browser profiles.
.DESCRIPTION
    No service/browser is started. A unique profile window does not establish its
    URL or App identity and is never authorization to act on the returned handle.
    Dot-source to expose the synthetic-test seams without observing the desktop.
#>
[CmdletBinding()]
param(
    [string]$WorkspacePath = $PSScriptRoot,
    [ValidateSet('Auto', 'Chrome', 'Edge')]
    [string]$Browser = 'Auto'
)

$script:WindowDiagnosticRoot = $PSScriptRoot

function Initialize-WorkbenchWindowReader {
    if ('OptiForgeDesktopRead.Native' -as [type]) { return }
    # Microsoft Learn Win32 references are listed in frontend/README.md.
    Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;
namespace OptiForgeDesktopRead {
    public sealed class WindowRow {
        public string Hwnd;
        public uint ProcessId;
        public bool Visible;
        public bool Minimized;
        public bool ObservationValid;
        public string ClassName;
        public int[] Rect;
        public uint? Dpi;
    }
    public sealed class WindowSnapshot {
        public bool Complete;
        public WindowRow[] Windows;
    }
    public static class Native {
        private delegate bool EnumProc(IntPtr hwnd, IntPtr parameter);
        [StructLayout(LayoutKind.Sequential)] private struct Rect { public int Left, Top, Right, Bottom; }
        [DllImport("user32.dll", SetLastError=true)]
        [return: MarshalAs(UnmanagedType.Bool)] private static extern bool EnumWindows(EnumProc callback, IntPtr parameter);
        [DllImport("user32.dll", SetLastError=true)] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
        [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool IsWindowVisible(IntPtr hwnd);
        [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool IsIconic(IntPtr hwnd);
        [DllImport("user32.dll", SetLastError=true)] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
        [DllImport("user32.dll", EntryPoint="GetClassNameW", CharSet=CharSet.Unicode, SetLastError=true)]
        private static extern int GetClassName(IntPtr hwnd, StringBuilder name, int length);
        [DllImport("user32.dll")] private static extern uint GetDpiForWindow(IntPtr hwnd);
        [DllImport("shell32.dll", EntryPoint="CommandLineToArgvW", CharSet=CharSet.Unicode, SetLastError=true)]
        private static extern IntPtr CommandLineToArgv(string command, out int count);
        [DllImport("kernel32.dll")] private static extern IntPtr LocalFree(IntPtr memory);

        public static string[] Parse(string command) {
            int count;
            IntPtr memory = CommandLineToArgv(command, out count);
            if (memory == IntPtr.Zero) throw new InvalidOperationException("Argument parsing unavailable.");
            try {
                string[] result = new string[count];
                for (int i = 0; i < count; i++) result[i] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(memory, i * IntPtr.Size));
                return result;
            } finally { if (LocalFree(memory) != IntPtr.Zero) throw new InvalidOperationException("Argument memory release unavailable."); }
        }

        public static WindowSnapshot ReadWindows() {
            List<WindowRow> rows = new List<WindowRow>();
            bool complete = true;
            EnumProc callback = delegate(IntPtr hwnd, IntPtr parameter) {
                try {
                    if (!IsWindowVisible(hwnd)) return true;
                    uint before, after;
                    if (GetWindowThreadProcessId(hwnd, out before) == 0 || before == 0) { complete = false; return true; }
                    Rect rect;
                    bool hasRect = GetWindowRect(hwnd, out rect);
                    StringBuilder name = new StringBuilder(512);
                    int length = GetClassName(hwnd, name, name.Capacity);
                    uint? dpi = null;
                    try { uint value = GetDpiForWindow(hwnd); if (value != 0) dpi = value; }
                    catch (EntryPointNotFoundException) { }
                    bool minimized = IsIconic(hwnd);
                    if (GetWindowThreadProcessId(hwnd, out after) == 0 || before != after || !IsWindowVisible(hwnd)) {
                        complete = false; return true;
                    }
                    string handle = IntPtr.Size == 8 ? unchecked((ulong)hwnd.ToInt64()).ToString(CultureInfo.InvariantCulture)
                        : unchecked((uint)hwnd.ToInt32()).ToString(CultureInfo.InvariantCulture);
                    rows.Add(new WindowRow { Hwnd=handle, ProcessId=before, Visible=true, Minimized=minimized,
                        ObservationValid=hasRect && length > 0 && length < name.Capacity - 1, ClassName=length > 0 ? name.ToString() : null,
                        Rect=hasRect ? new int[] { rect.Left, rect.Top, rect.Right, rect.Bottom } : null, Dpi=dpi });
                } catch { complete = false; }
                return true;
            };
            bool success = EnumWindows(callback, IntPtr.Zero);
            GC.KeepAlive(callback);
            return new WindowSnapshot { Complete=success && complete, Windows=rows.ToArray() };
        }
    }
}
'@ | Out-Null
}

function ConvertTo-WorkbenchWindowPath {
    param([object]$Value)
    if ($Value -isnot [string] -or [string]::IsNullOrWhiteSpace($Value) -or $Value -match '[\x00-\x1f"<>|*?]') { return $null }
    # Absolute ordinary drive/UNC paths only; no relative, device or wildcard aliases.
    if ($Value -notmatch '^(?:[a-zA-Z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))') { return $null }
    try {
        $full = [IO.Path]::GetFullPath($Value)
        $root = [IO.Path]::GetPathRoot($full)
        while ($full.Length -gt $root.Length -and ($full.EndsWith('\') -or $full.EndsWith('/'))) { $full = $full.Substring(0, $full.Length - 1) }
        return $full
    } catch { return $null }
}

function ConvertFrom-WorkbenchWindowCommandLine {
    param([object]$CommandLine)
    if ($CommandLine -isnot [string] -or [string]::IsNullOrWhiteSpace($CommandLine) -or $CommandLine.IndexOf([char]0) -ge 0) { return $null }
    # The native parser is permissive about unterminated quotes; reject those as
    # incomplete observation, while leaving actual tokenization to the Win32 API.
    $quoted = $false; $slashes = 0
    foreach ($character in $CommandLine.ToCharArray()) {
        if ($character -eq '\') { $slashes++; continue }
        if ($character -eq '"' -and $slashes % 2 -eq 0) { $quoted = -not $quoted }
        $slashes = 0
    }
    if ($quoted) { return $null }
    try {
        Initialize-WorkbenchWindowReader
        $arguments = [OptiForgeDesktopRead.Native]::Parse($CommandLine)
        if ($arguments.Count -eq 0 -or [string]::IsNullOrWhiteSpace($arguments[0])) { return $null }
        return ,$arguments
    } catch { return $null }
}

function Get-WorkbenchWindowService {
    param([string]$Root)
    # A child scope contains launcher's dot-source parameters. Never invoke its
    # launcher entry point and never return the Credential object to the CLI.
    & {
        param($LauncherFile, $StatusFile)
        . $LauncherFile
        Get-OnlineServiceStatus -ServiceFile $StatusFile
    } (Join-Path $script:WindowDiagnosticRoot 'start-workbench.ps1') (Join-Path $Root 'output\workbench_service\service.json')
}

function Get-WorkbenchWindowBrowsers {
    param([string]$Root, [string]$RequestedBrowser)
    & {
        param($LauncherFile, $DiagnosticRoot, $DiagnosticBrowser)
        . $LauncherFile
        $names = if ($DiagnosticBrowser -eq 'Auto') { @('Chrome', 'Edge') } else { @($DiagnosticBrowser) }
        foreach ($name in $names) {
            $resolved = Resolve-Browser -Browser $name
            if ($null -ne $resolved -and $resolved.Name -in @('Chrome', 'Edge')) {
                [pscustomobject]@{ Name=$resolved.Name; ExecutablePath=$resolved.Path;
                    ProfilePath=(Join-Path $DiagnosticRoot ('output\desktop_profile\' + $resolved.Name.ToLowerInvariant())) }
            }
        }
    } (Join-Path $script:WindowDiagnosticRoot 'start-workbench.ps1') $Root $RequestedBrowser
}

function Get-WorkbenchWindowSnapshot {
    Initialize-WorkbenchWindowReader
    $native = [OptiForgeDesktopRead.Native]::ReadWindows()
    $rows = @($native.Windows)
    $numbers = @($rows | ForEach-Object { $_.ProcessId } | Sort-Object -Unique)
    $processes = @()
    if ($numbers.Count -gt 0) {
        $filter = ($numbers | ForEach-Object { 'ProcessId=' + [uint32]$_ }) -join ' OR '
        $processes = @(Get-CimInstance -ClassName Win32_Process -Filter $filter -Property ProcessId,Name,ExecutablePath,CommandLine,CreationDate -ErrorAction Stop |
            ForEach-Object { [pscustomobject]@{ ProcessId=$_.ProcessId; Name=$_.Name; ExecutablePath=$_.ExecutablePath;
                CommandLine=$_.CommandLine; CreationTime=$_.CreationDate } })
    }
    [pscustomobject]@{ Complete=$native.Complete; Windows=$rows; Processes=$processes }
}

function ConvertTo-WorkbenchCreationKey {
    param([object]$Value)
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) { return $null }
    try {
        if ($Value -is [datetime]) { return $Value.ToUniversalTime().ToString('o', [Globalization.CultureInfo]::InvariantCulture) }
        $parsed = [DateTimeOffset]::Parse([string]$Value, [Globalization.CultureInfo]::InvariantCulture)
        return $parsed.UtcDateTime.ToString('o', [Globalization.CultureInfo]::InvariantCulture)
    } catch { return $null }
}

function Get-WorkbenchWindowProcessMatch {
    param($Process, [array]$Browsers, [scriptblock]$ParseArguments)
    $unknown = [pscustomobject]@{ Status='unknown'; Browser=$null; CreationTime=$null }
    $excluded = [pscustomobject]@{ Status='excluded'; Browser=$null; CreationTime=$null }
    if ($null -eq $Process) { return $unknown }
    $executable = ConvertTo-WorkbenchWindowPath $Process.ExecutablePath
    $expected = @($Browsers | Where-Object { $null -ne $executable -and [string]::Equals($_.ExecutablePath, $executable, [StringComparison]::OrdinalIgnoreCase) })
    if ($expected.Count -eq 0) {
        if ($null -ne $executable) { return $excluded }
        if (-not [string]::IsNullOrWhiteSpace([string]$Process.Name) -and
            @($Browsers | Where-Object { [string]::Equals([IO.Path]::GetFileName($_.ExecutablePath), [string]$Process.Name, [StringComparison]::OrdinalIgnoreCase) }).Count -eq 0) { return $excluded }
        return $unknown
    }
    if ($expected.Count -ne 1 -or -not [string]::Equals([IO.Path]::GetFileName($executable), [string]$Process.Name, [StringComparison]::OrdinalIgnoreCase)) { return $unknown }
    try { $arguments = & $ParseArguments $Process.CommandLine } catch { return $unknown }
    if ($null -eq $arguments -or $arguments.Count -lt 1) { return $unknown }
    $commandExecutable = ConvertTo-WorkbenchWindowPath $arguments[0]
    if (-not [string]::Equals($executable, $commandExecutable, [StringComparison]::OrdinalIgnoreCase)) { return $unknown }
    $profiles = @(); $badProfile = $false
    for ($index = 1; $index -lt $arguments.Count; $index++) {
        $argument = [string]$arguments[$index]
        if ($argument -ceq '--') { return $unknown }
        if ($argument -ceq '--type') {
            if ($index + 1 -ge $arguments.Count -or [string]::IsNullOrWhiteSpace([string]$arguments[$index + 1]) -or
                ([string]$arguments[$index + 1]).StartsWith('--', [StringComparison]::Ordinal)) { return $unknown }
            return $excluded
        }
        if ($argument.StartsWith('--type=', [StringComparison]::Ordinal)) {
            if ([string]::IsNullOrWhiteSpace($argument.Substring('--type='.Length))) { return $unknown }
            return $excluded
        }
        if ($argument -ceq '--user-data-dir') {
            if ($index + 1 -ge $arguments.Count -or ([string]$arguments[$index + 1]).StartsWith('--', [StringComparison]::Ordinal)) { $badProfile = $true }
            else { $index++; $profiles += [string]$arguments[$index] }
        } elseif ($argument.StartsWith('--user-data-dir=', [StringComparison]::Ordinal)) {
            $profiles += $argument.Substring('--user-data-dir='.Length)
        } elseif ($argument.StartsWith('--user-data-dir', [StringComparison]::OrdinalIgnoreCase)) { $badProfile = $true }
    }
    if ($badProfile -or $profiles.Count -gt 1) { return $unknown }
    if ($profiles.Count -eq 0) { return $excluded }
    $profile = ConvertTo-WorkbenchWindowPath $profiles[0]
    if ($null -eq $profile) { return $unknown }
    if (-not [string]::Equals($expected[0].ProfilePath, $profile, [StringComparison]::OrdinalIgnoreCase)) { return $excluded }
    $creation = ConvertTo-WorkbenchCreationKey $Process.CreationTime
    if ($null -eq $creation) { return $unknown }
    return [pscustomobject]@{ Status='matching'; Browser=$expected[0].Name; CreationTime=$creation }
}

function ConvertTo-WorkbenchWindowSet {
    param($Snapshot, [array]$Browsers, [scriptblock]$ParseArguments)
    $matches = @(); $unknown = @(); $seen = @{}
    if ($null -eq $Snapshot -or $Snapshot.Complete -isnot [bool] -or -not $Snapshot.Complete -or
        $Snapshot.Windows -isnot [array] -or $Snapshot.Processes -isnot [array]) {
        return [pscustomobject]@{ Complete=$false; Matching=@(); Unknown=@() }
    }
    foreach ($window in $Snapshot.Windows) {
        if ($null -eq $window -or $window.Visible -isnot [bool]) { return [pscustomobject]@{ Complete=$false; Matching=@(); Unknown=@() } }
        if (-not $window.Visible) { continue }
        $handle = [string]$window.Hwnd
        $handleNumber = [uint64]0; $processNumber = [uint32]0
        if (-not [uint64]::TryParse($handle, [ref]$handleNumber) -or $handleNumber -eq 0 -or
            -not [uint32]::TryParse([string]$window.ProcessId, [ref]$processNumber) -or $processNumber -eq 0 -or $seen.ContainsKey($handle)) {
            return [pscustomobject]@{ Complete=$false; Matching=@(); Unknown=@() }
        }
        $seen[$handle] = $true
        $processRows = @($Snapshot.Processes | Where-Object { [string]$_.ProcessId -ceq [string]$processNumber })
        $process = if ($processRows.Count -eq 1) { $processRows[0] } else { $null }
        $match = Get-WorkbenchWindowProcessMatch -Process $process -Browsers $Browsers -ParseArguments $ParseArguments
        if ($match.Status -eq 'excluded') { continue }
        if ($match.Status -eq 'unknown' -or $window.ObservationValid -isnot [bool] -or -not $window.ObservationValid -or $window.Minimized -isnot [bool] -or
            [string]::IsNullOrWhiteSpace([string]$window.ClassName) -or $window.Rect -isnot [array] -or $window.Rect.Count -ne 4 -or
            @($window.Rect | Where-Object { $_ -isnot [int] }).Count -gt 0) {
            $unknown += $handle + ':' + $processNumber; continue
        }
        $dpi = $null
        if ($null -ne $window.Dpi -and [uint32]::TryParse([string]$window.Dpi, [ref]$processNumber) -and $processNumber -gt 0) { $dpi = $processNumber }
        $matches += [pscustomobject][ordered]@{ hwnd=$handle; process_id=[uint32]$window.ProcessId; process_created_at=$match.CreationTime;
            browser=$match.Browser; visible=$true; minimized=$window.Minimized; class_name=[string]$window.ClassName;
            bounds=[ordered]@{ left=$window.Rect[0]; top=$window.Rect[1]; right=$window.Rect[2]; bottom=$window.Rect[3]; coordinates='GetWindowRect; DPI-virtualized screen coordinates' };
            dpi=$dpi; url_binding='unverified' }
    }
    [pscustomobject]@{ Complete=$true; Matching=@($matches | Sort-Object hwnd); Unknown=@($unknown | Sort-Object) }
}

function ConvertTo-WorkbenchWindowServiceIdentity {
    param($Service)
    if ($null -eq $Service -or $Service.State -cne 'online' -or [string]::IsNullOrWhiteSpace([string]$Service.InstanceId)) { return $null }
    try { $endpoint = [uri]$Service.Url } catch { return $null }
    $listener = [uint32]0
    if ($endpoint.Scheme -ne 'http' -or $endpoint.Host -ne '127.0.0.1' -or $endpoint.AbsolutePath -ne '/' -or
        $endpoint.UserInfo -ne '' -or $endpoint.Query -ne '' -or $endpoint.Fragment -ne '' -or $endpoint.Port -lt 1 -or
        -not [uint32]::TryParse([string]$Service.Pid, [ref]$listener) -or $listener -eq 0) { return $null }
    [pscustomobject][ordered]@{ endpoint=$endpoint.AbsoluteUri; instance_id=[string]$Service.InstanceId; listener_pid=$listener }
}

function Resolve-WorkbenchWindowDiagnostic {
    param($ServiceBefore, $ServiceAfter, [array]$Browsers, $Before, $After,
        [scriptblock]$ParseArguments = { param($line) ConvertFrom-WorkbenchWindowCommandLine $line })
    $result = [ordered]@{ schema_version=1; status='unavailable'; reason='service_unavailable'; url_binding='unverified';
        observation='point_in_time; profile association only; no action guarantee'; observed_at=[DateTime]::UtcNow.ToString('o');
        service=$null; profiles=@(); candidates=@() }
    $firstService = ConvertTo-WorkbenchWindowServiceIdentity $ServiceBefore
    $lastService = ConvertTo-WorkbenchWindowServiceIdentity $ServiceAfter
    if ($null -eq $firstService -or $null -eq $lastService) { return [pscustomobject]$result }
    if ($firstService.endpoint -cne $lastService.endpoint -or $firstService.instance_id -cne $lastService.instance_id -or
        $firstService.listener_pid -ne $lastService.listener_pid) { $result.reason='service_changed'; return [pscustomobject]$result }
    $result.service = $lastService
    if ($Browsers.Count -eq 0) { $result.reason='browser_unavailable'; return [pscustomobject]$result }
    $validBrowsers = @()
    foreach ($item in $Browsers) {
        $executable = ConvertTo-WorkbenchWindowPath $item.ExecutablePath
        $profile = ConvertTo-WorkbenchWindowPath $item.ProfilePath
        if ($item.Name -cnotin @('Chrome', 'Edge') -or $null -eq $executable -or $null -eq $profile -or
            @($validBrowsers | Where-Object { $_.Name -eq $item.Name -or $_.ExecutablePath -eq $executable }).Count -gt 0) {
            $result.reason='browser_metadata_incomplete'; return [pscustomobject]$result
        }
        $validBrowsers += [pscustomobject]@{ Name=$item.Name; ExecutablePath=$executable; ProfilePath=$profile }
    }
    $result.profiles = @($validBrowsers | ForEach-Object { [pscustomobject]@{ browser=$_.Name; profile_path=$_.ProfilePath } })
    $first = ConvertTo-WorkbenchWindowSet -Snapshot $Before -Browsers $validBrowsers -ParseArguments $ParseArguments
    $last = ConvertTo-WorkbenchWindowSet -Snapshot $After -Browsers $validBrowsers -ParseArguments $ParseArguments
    if (-not $first.Complete -or -not $last.Complete -or $first.Unknown.Count -gt 0 -or $last.Unknown.Count -gt 0) {
        $result.reason='observation_incomplete'; return [pscustomobject]$result
    }
    $firstKeys = @($first.Matching | ForEach-Object { $_.hwnd + ':' + $_.process_id + ':' + $_.process_created_at + ':' + $_.browser })
    $lastKeys = @($last.Matching | ForEach-Object { $_.hwnd + ':' + $_.process_id + ':' + $_.process_created_at + ':' + $_.browser })
    if (($firstKeys -join '|') -cne ($lastKeys -join '|')) { $result.reason='window_set_changed'; return [pscustomobject]$result }
    $result.candidates = @($last.Matching)
    $result.reason = 'stable_profile_observation'
    $result.status = if ($last.Matching.Count -eq 0) { 'not_found' } elseif ($last.Matching.Count -eq 1) { 'unique_candidate' } else { 'ambiguous' }
    return [pscustomobject]$result
}

function Get-WorkbenchWindowDiagnostic {
    param([string]$Root = $script:WindowDiagnosticRoot, [ValidateSet('Auto','Chrome','Edge')][string]$RequestedBrowser = 'Auto',
        [scriptblock]$ReadService = { param($root) Get-WorkbenchWindowService -Root $root },
        [scriptblock]$ReadBrowsers = { param($root,$browser) Get-WorkbenchWindowBrowsers -Root $root -RequestedBrowser $browser },
        [scriptblock]$ReadSnapshot = { Get-WorkbenchWindowSnapshot })
    try {
        $serviceBefore = & $ReadService $Root
        if ($null -eq (ConvertTo-WorkbenchWindowServiceIdentity $serviceBefore)) {
            return Resolve-WorkbenchWindowDiagnostic -ServiceBefore $serviceBefore -ServiceAfter $null -Browsers @() -Before $null -After $null
        }
        $browsers = @(& $ReadBrowsers $Root $RequestedBrowser)
        $before = & $ReadSnapshot
        $after = & $ReadSnapshot
        $serviceAfter = & $ReadService $Root
        return Resolve-WorkbenchWindowDiagnostic -ServiceBefore $serviceBefore -ServiceAfter $serviceAfter -Browsers $browsers -Before $before -After $after
    } catch {
        $failure = Resolve-WorkbenchWindowDiagnostic -ServiceBefore $null -ServiceAfter $null -Browsers @() -Before $null -After $null
        $failure.reason = 'diagnostic_unavailable'
        return $failure
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    Get-WorkbenchWindowDiagnostic -Root $WorkspacePath -RequestedBrowser $Browser | ConvertTo-Json -Depth 8
}
