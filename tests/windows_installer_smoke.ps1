# Windows-only integration test. Drives the actual NSIS wizard; never uses /S.
# Invoke on windows-latest with pwsh -File tests/windows_installer_smoke.ps1
#   -InstallerPath <Setup.exe> -ZipPath <release.zip> -ReportPath <report.json>
# Only unique, newly-created QGIS profiles and processes launched here are touched.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [Parameter(Mandatory = $true)][string]$ZipPath,
    [string]$ReportPath = (Join-Path $PWD 'windows_installer_smoke.json'),
    [ValidateRange(30, 300)][int]$TimeoutSeconds = 180
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$clock = [Diagnostics.Stopwatch]::StartNew()
$report = [ordered]@{
    schema_version = 1; windows_executed = $false; success = $false
    started_utc = [DateTime]::UtcNow.ToString('o'); timeout_seconds = $TimeoutSeconds
    automation = 'Visible NSIS wizard, Win32 control messages; no silent switch'
    checks = @(); processes = @(); error = $null
}
$owned = @{}
$createdProfiles = [Collections.Generic.List[string]]::new()
$steps = [Collections.Generic.List[object]]::new()
$ReportPath = [IO.Path]::GetFullPath($ReportPath)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($ReportPath)) | Out-Null
$logPath = [IO.Path]::ChangeExtension($ReportPath, '.log')
[IO.File]::WriteAllText($logPath, '')
$registryPath = $null
$registryCleanupAllowed = $false
$archive = $null
$activeLaunch = $null

function Write-Step([string]$Message) {
    $line = '{0,7:N2}s  {1}' -f $clock.Elapsed.TotalSeconds, $Message
    Write-Host $line
    [IO.File]::AppendAllText($logPath, $line + [Environment]::NewLine)
}
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Check-Deadline {
    if ($clock.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
        throw "Global timeout after $TimeoutSeconds seconds."
    }
}

try {
    Assert-True ([Environment]::OSVersion.Platform -eq 'Win32NT') 'This test requires Windows.'
    $InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path
    $ZipPath = (Resolve-Path -LiteralPath $ZipPath).Path
    $report.installer = $InstallerPath
    $report.zip = $ZipPath
    $report.installer_sha256 = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $report.zip_sha256 = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public sealed class HcauWindow {
    public IntPtr Handle;
    public uint Pid;
    public int Id;
    public string ClassName;
    public string Text;
    public bool Enabled;
}
public static class HcauWin32 {
    private delegate bool EnumProc(IntPtr handle, IntPtr data);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc fn, IntPtr data);
    [DllImport("user32.dll")] private static extern bool EnumChildWindows(IntPtr parent, EnumProc fn, IntPtr data);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr handle);
    [DllImport("user32.dll")] private static extern bool IsWindowEnabled(IntPtr handle);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr handle, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetClassName(IntPtr handle, StringBuilder text, int size);
    [DllImport("user32.dll")] public static extern int GetDlgCtrlID(IntPtr handle);
    [DllImport("user32.dll")] private static extern IntPtr GetParent(IntPtr handle);
    [DllImport("user32.dll", SetLastError=true)] private static extern bool PostMessage(IntPtr handle, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern IntPtr SendMessageTimeout(IntPtr handle, uint message, IntPtr wParam, IntPtr lParam, uint flags, uint timeout, out IntPtr result);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true, EntryPoint="SendMessageTimeoutW")]
    private static extern IntPtr SendTextMessageTimeout(IntPtr handle, uint message, IntPtr wParam, StringBuilder text, uint flags, uint timeout, out IntPtr result);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true, EntryPoint="SendMessageTimeoutW")]
    private static extern IntPtr SendStringMessageTimeout(IntPtr handle, uint message, IntPtr wParam, string text, uint flags, uint timeout, out IntPtr result);
    private static HcauWindow Inspect(IntPtr handle) {
        uint pid; GetWindowThreadProcessId(handle, out pid);
        var cls = new StringBuilder(256); GetClassName(handle, cls, cls.Capacity);
        var text = new StringBuilder(4096); IntPtr result;
        SendTextMessageTimeout(handle, 0x000D, (IntPtr)text.Capacity, text, 2, 250, out result);
        return new HcauWindow { Handle=handle, Pid=pid, Id=GetDlgCtrlID(handle),
            ClassName=cls.ToString(), Text=text.ToString(), Enabled=IsWindowEnabled(handle) };
    }
    public static HcauWindow[] Windows(uint[] pids) {
        var allowed = new HashSet<uint>(pids); var result = new List<HcauWindow>();
        EnumWindows((h, d) => { uint pid; GetWindowThreadProcessId(h, out pid);
            if (allowed.Contains(pid) && IsWindowVisible(h)) result.Add(Inspect(h)); return true; }, IntPtr.Zero);
        return result.ToArray();
    }
    public static HcauWindow[] Children(IntPtr parent) {
        var result = new List<HcauWindow>();
        EnumChildWindows(parent, (h, d) => { if (IsWindowVisible(h)) result.Add(Inspect(h)); return true; }, IntPtr.Zero);
        return result.ToArray();
    }
    public static void Click(IntPtr button) {
        if (!IsWindowVisible(button) || !IsWindowEnabled(button)) throw new Exception("Button is not available.");
        // Same BN_CLICKED command as the visible button. Queued, so installation cannot block this caller.
        if (!PostMessage(GetParent(button), 0x0111, (IntPtr)GetDlgCtrlID(button), button))
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
    }
    public static void SelectProfile(IntPtr combo, string profile) {
        IntPtr index, selected;
        if (SendStringMessageTimeout(combo, 0x0158, (IntPtr)(-1), profile, 2, 2000, out index) == IntPtr.Zero || index.ToInt64() < 0)
            throw new Exception("Test profile is absent from the actual ComboBox: " + profile);
        if (SendMessageTimeout(combo, 0x014E, index, IntPtr.Zero, 2, 2000, out selected) == IntPtr.Zero || selected != index)
            throw new Exception("Could not select the test profile.");
        // CB_SETCURSEL itself does not emit CBN_SELCHANGE (Microsoft Win32 contract).
        if (!PostMessage(GetParent(combo), 0x0111, (IntPtr)(GetDlgCtrlID(combo) | (1 << 16)), combo))
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
    }
}
'@

    function Refresh-Children {
        if ($null -eq $script:activeLaunch) { return }
        # NSIS uninstallers copy themselves to TEMP and start a child process.
        $rows = @(Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, CreationDate -ErrorAction Stop)
        do {
            $changed = $false
            foreach ($row in $rows) {
                $childId = [int]$row.ProcessId
                if ($script:owned.ContainsKey($childId) -or -not $script:owned.ContainsKey([int]$row.ParentProcessId)) { continue }
                if ($row.CreationDate.ToUniversalTime() -lt $script:activeLaunch.StartedUtc.AddSeconds(-1)) { continue }
                $proc = Get-Process -Id $childId -ErrorAction SilentlyContinue
                if ($null -eq $proc) { continue }
                $script:owned[$childId] = $proc.StartTime.ToUniversalTime().Ticks
                $script:activeLaunch.Ids.Add($childId) | Out-Null
                Write-Step "Tracking own child PID $childId (parent $($row.ParentProcessId))."
                $changed = $true
            }
        } while ($changed)
    }
    function Get-OwnWindows {
        Refresh-Children
        $live = [Collections.Generic.List[uint32]]::new()
        foreach ($processId in $script:activeLaunch.Ids) {
            $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
            if ($null -ne $proc -and $proc.StartTime.ToUniversalTime().Ticks -eq $script:owned[$processId]) {
                $live.Add([uint32]$processId)
            }
        }
        return @([HcauWin32]::Windows($live.ToArray()))
    }
    function Wait-Control([string]$Description, [scriptblock]$Find, [int]$Seconds = 25) {
        Write-Step $Description
        $until = [Math]::Min($clock.Elapsed.TotalSeconds + $Seconds, $TimeoutSeconds)
        while ($clock.Elapsed.TotalSeconds -lt $until) {
            Check-Deadline
            $found = & $Find
            if ($null -ne $found -and $found -ne $false) { return $found }
            Start-Sleep -Milliseconds 150
        }
        $windows = @(Get-OwnWindows | ForEach-Object {
            [ordered]@{ pid = $_.Pid; title = $_.Text; controls = @([HcauWin32]::Children($_.Handle) | Select-Object Id, ClassName, Text, Enabled) }
        })
        $report.last_windows = $windows
        throw "Timed out: $Description. No interactive wizard control was available; inspect last_windows in the report."
    }
    function Find-Button([string]$TextPattern) {
        foreach ($win in @(Get-OwnWindows)) {
            foreach ($control in @([HcauWin32]::Children($win.Handle))) {
                if ($control.ClassName -eq 'Button' -and $control.Id -eq 1 -and $control.Enabled -and $control.Text -match $TextPattern) {
                    return $control
                }
            }
        }
        return $null
    }
    function Start-OwnProcess([string]$Path) {
        Check-Deadline
        $started = [DateTime]::UtcNow
        $proc = Start-Process -FilePath $Path -PassThru
        $ids = [Collections.Generic.List[int]]::new(); $ids.Add($proc.Id)
        $script:owned[$proc.Id] = $proc.StartTime.ToUniversalTime().Ticks
        $script:activeLaunch = [pscustomobject]@{ Ids = $ids; StartedUtc = $started; Process = $proc }
        $report.windows_executed = $true
        $report.processes += [ordered]@{ pid = $proc.Id; executable = $Path; started_utc = $started.ToString('o') }
        Write-Step "Started own PID $($proc.Id): $Path"
    }
    function Wait-Closed {
        $null = Wait-Control 'Waiting for own wizard to close' {
            $windows = @(Get-OwnWindows)
            if ($windows.Count -eq 0) { return $true }
            return $null
        } 15
    }
    function Run-Install {
        Start-OwnProcess $InstallerPath
        $next = Wait-Control 'Welcome: Next' { Find-Button '(?i)siguiente|next' }
        [HcauWin32]::Click($next.Handle)
        $combo = Wait-Control 'Finding the visible QGIS profile selector' {
            foreach ($win in @(Get-OwnWindows)) {
                $controls = @([HcauWin32]::Children($win.Handle) | Where-Object ClassName -eq 'ComboBox')
                if ($controls.Count -eq 1) { return $controls[0] }
            }
            return $null
        }
        [HcauWin32]::SelectProfile($combo.Handle, $profileName)
        $null = Wait-Control "Selected profile destination: $pluginPath" {
            foreach ($win in @(Get-OwnWindows)) {
                foreach ($control in @([HcauWin32]::Children($win.Handle))) {
                    if ($control.Text -eq $pluginPath) { return $true }
                }
            }
            return $null
        }
        $install = Wait-Control 'Profile: Install' { Find-Button '(?i)instalar|install|siguiente|next' }
        [HcauWin32]::Click($install.Handle)
        $finish = Wait-Control 'Installation: Finish' { Find-Button '(?i)terminar|finalizar|finish|cerrar|close' } 40
        [HcauWin32]::Click($finish.Handle)
        Wait-Closed
    }
    function Hash-Tree([string]$Directory) {
        $result = [ordered]@{}
        foreach ($file in @(Get-ChildItem -LiteralPath $Directory -File -Recurse | Sort-Object FullName)) {
            Check-Deadline
            $relative = [IO.Path]::GetRelativePath($Directory, $file.FullName).Replace('\', '/')
            $result[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        return $result
    }
    function Assert-Payload([string]$Directory) {
        foreach ($item in $expected.GetEnumerator()) {
            Check-Deadline
            $path = Join-Path $Directory $item.Key
            Assert-True (Test-Path -LiteralPath $path -PathType Leaf) "Missing payload file: $path"
            Assert-True ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -eq $item.Value) "Payload differs from ZIP: $path"
        }
    }
    function Assert-ControlFiles {
        foreach ($item in $controlFiles.GetEnumerator()) {
            Assert-True (Test-Path -LiteralPath $item.Key -PathType Leaf) "Foreign/control file disappeared: $($item.Key)"
            Assert-True ([IO.File]::ReadAllText($item.Key) -eq $item.Value) "Foreign/control file changed: $($item.Key)"
        }
    }
    function Record-Check([string]$Name, [object]$Details) {
        $steps.Add([ordered]@{ name = $Name; passed = $true; elapsed_seconds = [Math]::Round($clock.Elapsed.TotalSeconds, 2); details = $Details })
        Write-Step "PASS: $Name"
    }

    # Read expected bytes directly from the official ZIP, independently of the EXE manifest.
    $archive = [IO.Compression.ZipFile]::OpenRead($ZipPath)
    $expected = [ordered]@{}
    foreach ($entry in $archive.Entries) {
        Assert-True ($entry.FullName.StartsWith('caumax_consulta/') -and -not $entry.FullName.EndsWith('/')) "Unexpected ZIP entry: $($entry.FullName)"
        $relative = $entry.FullName.Substring('caumax_consulta/'.Length)
        Assert-True ($relative -notmatch '(^|/)\.\.(/|$)|\\|:' -and -not $relative.StartsWith('/')) 'Unsafe ZIP entry.'
        $stream = $entry.Open(); $digest = [Security.Cryptography.SHA256]::Create()
        try { $expected[$relative] = [Convert]::ToHexString($digest.ComputeHash($stream)).ToLowerInvariant() }
        finally { $digest.Dispose(); $stream.Dispose() }
    }
    Assert-True ($expected.Count -gt 0 -and $expected.Contains('metadata.txt')) 'ZIP has no plugin payload.'
    $report.payload_files = $expected.Count

    # Shell-known APPDATA, not an environment override ignored by NSIS CSIDL_APPDATA.
    $profilesRoot = Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'QGIS\QGIS3\profiles'
    $tag = [Guid]::NewGuid().ToString('N')
    $profileName = "HidroCAUMAX-ci-$tag"
    $profilePath = Join-Path $profilesRoot $profileName
    $controlProfile = Join-Path $profilesRoot "$profileName-control"
    foreach ($path in @($profilePath, $controlProfile)) {
        Assert-True (-not (Test-Path -LiteralPath $path)) "Refusing an existing test profile: $path"
        [IO.Directory]::CreateDirectory($path) | Out-Null
        $createdProfiles.Add($path)
    }
    $pluginPath = Join-Path $profilePath 'python\plugins\caumax_consulta'
    $registryPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\HidroCAUMAX-QGIS3-$profileName"
    Assert-True (-not (Test-Path -LiteralPath $registryPath)) 'Unique test registry key already exists.'
    $registryCleanupAllowed = $true
    $report.profile = $profilePath
    $report.control_profile = $controlProfile
    $report.isolation = 'Two unique profiles below the real current-user APPDATA; no existing profile or shell-folder configuration modified'
    $controlFiles = [ordered]@{
        (Join-Path $profilePath 'proyecto-ajeno.txt') = "project-$tag"
        (Join-Path $profilePath 'python\plugins\otro_complemento\datos.txt') = "other-plugin-$tag"
        (Join-Path $controlProfile 'python\plugins\caumax_consulta\datos.txt') = "other-profile-$tag"
    }
    foreach ($item in $controlFiles.GetEnumerator()) {
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($item.Key)) | Out-Null
        [IO.File]::WriteAllText($item.Key, $item.Value)
    }
    Write-Step "Isolated target profile: $profilePath"
    Run-Install
    Assert-Payload $pluginPath
    Assert-ControlFiles
    Assert-True (Test-Path -LiteralPath (Join-Path $pluginPath 'uninstall-HidroCAUMAX.exe')) 'Uninstaller was not installed.'
    Assert-True (Test-Path -LiteralPath $registryPath) 'Per-profile uninstall registry key is absent.'
    Record-Check 'fresh_install_exact_zip' @{ files = $expected.Count; target = $pluginPath }

    # Reinstall is a genuine replacement with a preserved full previous directory.
    $oldExtra = Join-Path $pluginPath 'datos-del-usuario.txt'
    [IO.File]::WriteAllText($oldExtra, "preserve-old-extra-$tag")
    $oldTree = Hash-Tree $pluginPath
    Run-Install
    Assert-Payload $pluginPath
    Assert-ControlFiles
    $operations = Join-Path $profilePath 'python\.HidroCAUMAX-install'
    $backups = @(Get-ChildItem -LiteralPath $operations -Directory | ForEach-Object { Join-Path $_.FullName 'previous' } | Where-Object { Test-Path -LiteralPath $_ -PathType Container })
    Assert-True ($backups.Count -eq 1) 'Expected exactly one preserved previous installation.'
    $backupPath = $backups[0]
    $backupTree = Hash-Tree $backupPath
    Assert-True (($backupTree | ConvertTo-Json -Compress) -ceq ($oldTree | ConvertTo-Json -Compress)) 'The update changed the preserved previous installation.'
    Record-Check 'update_preserves_complete_backup_and_foreign_data' @{ backup = $backupPath; files = $backupTree.Count }

    # Emulate a subsequent ZIP upgrade: overwrite the plugin payload, leave the older
    # EXE/INI as QGIS may do, and advance metadata's version. This is an explicit
    # test fixture, not a claim that a future release ZIP has been published.
    foreach ($entry in $archive.Entries) {
        $destination = Join-Path $pluginPath $entry.FullName.Substring('caumax_consulta/'.Length)
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destination, $true)
    }
    $metadataPath = Join-Path $pluginPath 'metadata.txt'
    $metadataBytes = [IO.File]::ReadAllBytes($metadataPath)
    $metadataText = [Text.Encoding]::UTF8.GetString($metadataBytes)
    $versionMatch = [regex]::Match($metadataText, '(?m)^version\s*=\s*(\d+)\.(\d+)\.(\d+)\s*$')
    Assert-True $versionMatch.Success 'Could not read the actual installed version.'
    $futureVersion = '{0}.{1}.{2}' -f $versionMatch.Groups[1].Value, $versionMatch.Groups[2].Value, (1 + [int]$versionMatch.Groups[3].Value)
    $futureMetadata = [regex]::Replace($metadataText, '(?m)^version\s*=.*$', "version=$futureVersion")
    [IO.File]::WriteAllText($metadataPath, $futureMetadata, [Text.UTF8Encoding]::new($false))
    $newExtra = Join-Path $pluginPath 'conservar-tras-desinstalar.txt'
    [IO.File]::WriteAllText($newExtra, "preserve-new-extra-$tag")
    $newerTree = Hash-Tree $pluginPath
    Start-OwnProcess (Join-Path $pluginPath 'uninstall-HidroCAUMAX.exe')
    $refusal = Wait-Control 'Old uninstaller must refuse the later ZIP version' {
        foreach ($win in @(Get-OwnWindows)) {
            $controls = @([HcauWin32]::Children($win.Handle))
            if ((($controls | ForEach-Object Text) -join ' ') -match 'desinstalador corresponde a una copia anterior') {
                $ok = @($controls | Where-Object { $_.ClassName -eq 'Button' -and $_.Id -eq 1 -and $_.Enabled })
                if ($ok.Count -eq 1) { return $ok[0] }
            }
        }
        return $null
    }
    [HcauWin32]::Click($refusal.Handle)
    Wait-Closed
    Assert-True (((Hash-Tree $pluginPath) | ConvertTo-Json -Compress) -ceq ($newerTree | ConvertTo-Json -Compress)) 'Old uninstaller modified the newer ZIP installation.'
    Assert-True (Test-Path -LiteralPath $registryPath) 'Refused uninstaller removed the registry key.'
    Assert-ControlFiles
    Record-Check 'old_uninstaller_refuses_later_zip_without_changes' @{ simulated_zip_version = $futureVersion; files_unchanged = $newerTree.Count }

    # Restore exact fixture metadata, then exercise the normal visible uninstaller.
    [IO.File]::WriteAllBytes($metadataPath, $metadataBytes)
    Start-OwnProcess (Join-Path $pluginPath 'uninstall-HidroCAUMAX.exe')
    $remove = Wait-Control 'Uninstaller: confirm removal' { Find-Button '(?i)desinstalar|uninstall' }
    [HcauWin32]::Click($remove.Handle)
    $finish = Wait-Control 'Uninstaller: Finish' { Find-Button '(?i)terminar|finalizar|finish|cerrar|close' }
    [HcauWin32]::Click($finish.Handle)
    Wait-Closed
    foreach ($item in $expected.GetEnumerator()) {
        Assert-True (-not (Test-Path -LiteralPath (Join-Path $pluginPath $item.Key))) "Uninstall left payload file: $($item.Key)"
    }
    Assert-True (-not (Test-Path -LiteralPath $registryPath)) 'Uninstall retained its registry key.'
    Assert-True ([IO.File]::ReadAllText($newExtra) -eq "preserve-new-extra-$tag") 'Uninstall deleted user data in the plugin directory.'
    Assert-True (((Hash-Tree $backupPath) | ConvertTo-Json -Compress) -ceq ($backupTree | ConvertTo-Json -Compress)) 'Uninstall modified the earlier backup.'
    Assert-ControlFiles
    Record-Check 'uninstall_preserves_user_data_other_plugins_profiles_and_backup' @{ backup = $backupPath; preserved_user_file = $newExtra }
    $report.success = $true
}
catch {
    $report.error = $_.Exception.Message
    $report.error_detail = $_.ScriptStackTrace
    Write-Step "FAIL: $($report.error)"
}
finally {
    if ($null -ne $archive) { $archive.Dispose() }
    # Process identity includes creation time to avoid terminating a reused PID.
    foreach ($processId in @($owned.Keys)) {
        $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -ne $proc) {
            try {
                if ($proc.StartTime.ToUniversalTime().Ticks -eq $owned[$processId]) {
                    Stop-Process -Id $processId -Force -ErrorAction Stop
                }
            } catch { Write-Step "Own process cleanup: $($_.Exception.Message)" }
        }
    }
    # Delete only directories that this script created after asserting absence.
    foreach ($path in $createdProfiles) {
        try { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop }
        catch { Write-Step "Own profile cleanup: $($_.Exception.Message)" }
    }
    if ($registryCleanupAllowed -and $null -ne $registryPath -and (Test-Path -LiteralPath $registryPath)) {
        Remove-Item -LiteralPath $registryPath -Recurse -Force -ErrorAction SilentlyContinue
    }
    $report.checks = @($steps.ToArray())
    $report.duration_seconds = [Math]::Round($clock.Elapsed.TotalSeconds, 2)
    $report.log = $logPath
    $report.finished_utc = [DateTime]::UtcNow.ToString('o')
    [IO.File]::WriteAllText($ReportPath, ($report | ConvertTo-Json -Depth 12) + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    Write-Host "Report: $ReportPath"
}
if (-not $report.success) { exit 1 }
exit 0
