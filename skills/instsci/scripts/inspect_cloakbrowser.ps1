param(
  [string]$OutputDir = "",
  [string]$Publisher = "",
  [string]$BrowserProfile = "",
  [string]$ProfileDir = "",
  [switch]$Json
)

$ErrorActionPreference = "Continue"

function New-InspectionDir {
  param([string]$Requested)
  if ([string]::IsNullOrWhiteSpace($Requested)) {
    $Requested = Join-Path (Get-Location) ("instsci_cloakbrowser_inspection_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
  }
  New-Item -ItemType Directory -Path $Requested -Force | Out-Null
  return (Resolve-Path -LiteralPath $Requested).Path
}

function Get-CommandLineProfile {
  param([string]$CommandLine)
  if ($CommandLine -match '--user-data-dir="?([^"\s]+[^"]*?)"?(?:\s|$)') {
    return $Matches[1]
  }
  return ""
}

function Normalize-PathForCompare {
  param([string]$PathValue)
  if ([string]::IsNullOrWhiteSpace($PathValue)) { return "" }
  try {
    return [System.IO.Path]::GetFullPath($PathValue).TrimEnd("\").ToLowerInvariant()
  } catch {
    return $PathValue.TrimEnd("\").ToLowerInvariant()
  }
}

function Get-WindowElement {
  param([IntPtr]$Handle)
  try {
    Add-Type -AssemblyName UIAutomationClient -ErrorAction SilentlyContinue
    Add-Type -AssemblyName UIAutomationTypes -ErrorAction SilentlyContinue
    return [System.Windows.Automation.AutomationElement]::FromHandle($Handle)
  } catch {
    return $null
  }
}

function Get-InterestingText {
  param($Window)
  $items = New-Object System.Collections.Generic.List[string]
  if (-not $Window) { return @() }
  try {
    $all = $Window.FindAll(
      [System.Windows.Automation.TreeScope]::Descendants,
      [System.Windows.Automation.Condition]::TrueCondition
    )
    for ($i = 0; $i -lt $all.Count; $i++) {
      $e = $all.Item($i)
      $name = [string]$e.Current.Name
      if ([string]::IsNullOrWhiteSpace($name)) { continue }
      if ($name.Length -gt 240) { $name = $name.Substring(0, 240) }
      if ($name -match "about:blank|https?://|Access|organization|institution|PDF|Download|robot|captcha|Cloudflare|Turnstile|verify|verification|human|security|blocked|problem|CPE|Purchase|View PDF|Full text|Sign in|error|not entitled|no access|access denied|rent this article|get access|下载|验证|安全|人机|机构|登录|无权访问|无法访问|购买") {
        if (-not $items.Contains($name)) { $items.Add($name) | Out-Null }
      }
    }
  } catch {}
  return @($items)
}

function Get-AddressBarValue {
  param($Window)
  if (-not $Window) { return "" }
  try {
    $all = $Window.FindAll(
      [System.Windows.Automation.TreeScope]::Descendants,
      [System.Windows.Automation.Condition]::TrueCondition
    )
    for ($i = 0; $i -lt $all.Count; $i++) {
      $e = $all.Item($i)
      $name = [string]$e.Current.Name
      $type = [string]$e.Current.ControlType.ProgrammaticName
      if ($type -notmatch 'Edit|ComboBox') { continue }
      $value = ""
      try {
        $vp = $e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
        $value = [string]$vp.Current.Value
      } catch {}
      if ($value -match '^about:blank$|^https?://') { return $value }
      if ($name -match '^about:blank$|^https?://') { return $name }
    }
  } catch {}
  return ""
}

function Save-WindowScreenshot {
  param(
    [object]$Window,
    [IntPtr]$Handle,
    [string]$Path
  )
  if (-not $Window) { return $false }
  try {
    Add-Type -AssemblyName System.Drawing -ErrorAction SilentlyContinue
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class InstSciDoctorWin32 {
  [DllImport("user32.dll")]
  public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")]
  public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@ -ErrorAction SilentlyContinue
    [InstSciDoctorWin32]::ShowWindow($Handle, 9) | Out-Null
    [InstSciDoctorWin32]::SetForegroundWindow($Handle) | Out-Null
    Start-Sleep -Milliseconds 300
    $rect = $Window.Current.BoundingRectangle
    if ($rect.IsEmpty -or $rect.Width -lt 10 -or $rect.Height -lt 10) { return $false }
    $bmp = New-Object System.Drawing.Bitmap([int]$rect.Width, [int]$rect.Height)
    $graphics = [System.Drawing.Graphics]::FromImage($bmp)
    $graphics.CopyFromScreen([int]$rect.X, [int]$rect.Y, 0, 0, $bmp.Size)
    $bmp.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose()
    $bmp.Dispose()
    return $true
  } catch {
    return $false
  }
}

function Classify-State {
  param(
    [string]$Title,
    [string]$Url,
    [string[]]$Texts,
    [int]$WindowCount
  )
  $joined = (($Texts + @($Title, $Url)) -join "`n")
  if ($WindowCount -eq 0) { return "no_window" }
  if ($Url -eq "about:blank" -or ($Title -match "^about:blank|^Chromium$" -and $joined -notmatch "https?://|PDF|Access|robot|problem|CPE")) { return "blank" }
  if ($joined -match "There was a problem providing the content|CPE\d+|content you requested") { return "publisher_error" }
  if ($joined -match "Ray ID|Attention Required|temporarily blocked|blocked by security|too many requests|unusual traffic") { return "waf_blocked" }
  if ($joined -match "Are you a robot|captcha|Turnstile|verify you are human|human verification|security check|请完成验证|人机验证|安全验证") { return "human_verification_required" }
  if ($joined -match "Access denied|not entitled|no access|your institution does not have access|purchase access|Purchase PDF|get access|rent this article|无权访问|无法访问|购买") { return "access_unavailable" }
  if ($joined -match "Open Access" -and $joined -match "View PDF|Full text|PDF loaded|Download") { return "pdf_or_authorized" }
  if ($joined -match "Access through your organization|Organization name|institution|Sign in|Find your organization|机构|登录|组织") { return "auth_required" }
  if ($joined -match "View PDF|Full text access|PDF loaded|Download") { return "pdf_or_authorized" }
  return "unknown_visible"
}

$resolvedOutputDir = New-InspectionDir -Requested $OutputDir
$profileFilterRaw = if (-not [string]::IsNullOrWhiteSpace($BrowserProfile)) { $BrowserProfile } else { $ProfileDir }
$profileFilter = Normalize-PathForCompare -PathValue $profileFilterRaw

$processes = Get-CimInstance Win32_Process | Where-Object {
  ($_.ExecutablePath -match '[\\/]cloakbrowser[\\/]chromium-[^\\/]+[\\/]chrome\.exe$') -or
  ($_.CommandLine -match '[\\/]cloakbrowser[\\/]chromium-[^\\/]+[\\/]chrome\.exe')
}

$windowRows = @()
$chromeProcesses = @($processes | Sort-Object ProcessId)
$mainWindows = @()

foreach ($proc in $chromeProcesses) {
  $gp = Get-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue
  if (-not $gp -or $gp.MainWindowHandle -eq 0) { continue }
  $mainWindows += [pscustomobject]@{
    Process = $proc
    GetProcess = $gp
  }
}

$index = 0
foreach ($entry in $mainWindows) {
  $index += 1
  $proc = $entry.Process
  $gp = $entry.GetProcess
  $profileDir = Get-CommandLineProfile -CommandLine ([string]$proc.CommandLine)
  $profileMatch = $true
  if (-not [string]::IsNullOrWhiteSpace($profileFilter)) {
    $profileMatch = ((Normalize-PathForCompare -PathValue $profileDir) -eq $profileFilter)
  }
  $window = Get-WindowElement -Handle $gp.MainWindowHandle
  $url = Get-AddressBarValue -Window $window
  $texts = Get-InterestingText -Window $window
  $screenshot = Join-Path $resolvedOutputDir ("window_{0}_{1}.png" -f $index, $proc.ProcessId)
  $screenshotSaved = Save-WindowScreenshot -Window $window -Handle ([IntPtr]$gp.MainWindowHandle) -Path $screenshot
  if (-not $screenshotSaved) { $screenshot = "" }

  $windowRows += [pscustomobject]@{
    index = $index
    pid = $proc.ProcessId
    title = [string]$gp.MainWindowTitle
    url = $url
    profile_dir = $profileDir
    profile_match = $profileMatch
    screenshot = $screenshot
    interesting_text = $texts
  }
}

$overallState = "no_window"
$stateRows = @($windowRows | Where-Object { $_.profile_match })
if ($windowRows.Count -gt 0 -and $stateRows.Count -eq 0) {
  $overallState = "other_windows_present"
} elseif ($stateRows.Count -gt 0) {
  $states = @()
  foreach ($row in $stateRows) {
    $states += Classify-State -Title $row.title -Url $row.url -Texts $row.interesting_text -WindowCount $stateRows.Count
  }
  if ($states -contains "publisher_error") { $overallState = "publisher_error" }
  elseif ($states -contains "waf_blocked") { $overallState = "waf_blocked" }
  elseif ($states -contains "human_verification_required") { $overallState = "human_verification_required" }
  elseif ($states -contains "access_unavailable") { $overallState = "access_unavailable" }
  elseif ($states -contains "auth_required") { $overallState = "auth_required" }
  elseif ($states -contains "pdf_or_authorized") { $overallState = "pdf_or_authorized" }
  elseif (($states | Where-Object { $_ -eq "blank" }).Count -ge 1) { $overallState = "blank" }
  else { $overallState = "unknown_visible" }
}

$recommendation = switch ($overallState) {
  "no_window" { "No CloakBrowser window found. Safe to start a new single-publisher run." }
  "blank" { "Stop before continuing. about:blank usually means startup/profile contention or an uninitialized browser." }
  "publisher_error" { "Record screenshot and diagnostic. Do not mark as unsupported; retry with another DOI or later." }
  "human_verification_required" { "User must complete the visible verification manually, then rerun inspection before continuing." }
  "waf_blocked" { "Stop batch runs. Record screenshot evidence and retry later, another route, or a single DOI after prewarm." }
  "access_unavailable" { "Visible page suggests this route lacks article entitlement. Check access in a regular browser or library subscription before retrying." }
  "auth_required" { "User must complete institution login/selection. Do not enter credentials automatically." }
  "pdf_or_authorized" { "Page appears authorized or PDF-related. Continue capture, then verify manifest/PDF text." }
  "other_windows_present" { "CloakBrowser windows exist, but none match the requested profile. Inspect broker/profile routing before judging this run." }
  default { "Visible state is unknown. Inspect screenshot before continuing." }
}

$standardStatus = switch ($overallState) {
  "auth_required" { "auth_required" }
  "human_verification_required" { "human_verification_required" }
  "waf_blocked" { "waf_blocked" }
  "access_unavailable" { "access_unavailable" }
  "publisher_error" { "publisher_error" }
  default { "" }
}

$report = [pscustomobject]@{
  generated_at = (Get-Date).ToString("o")
  publisher = $Publisher
  output_dir = $resolvedOutputDir
  state = $overallState
  standard_status = $standardStatus
  recommendation = $recommendation
  cloakbrowser_process_count = $chromeProcesses.Count
  window_count = $windowRows.Count
  matching_window_count = $stateRows.Count
  profile_filter = $profileFilterRaw
  windows = $windowRows
}

$reportPath = Join-Path $resolvedOutputDir "inspection.json"
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8

if ($Json) {
  $report | ConvertTo-Json -Depth 8
} else {
  "State: $overallState"
  "Recommendation: $recommendation"
  "Report: $reportPath"
  if ($windowRows.Count -gt 0) {
    $windowRows | Select-Object index,pid,title,url,profile_dir,profile_match,screenshot | Format-Table -AutoSize
  }
}
