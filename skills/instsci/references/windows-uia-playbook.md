# InstSci Windows UIA Playbook

Use this only for the already visible InstSci CloakBrowser window when normal browser automation stalls. Prefer text-visible controls over raw coordinates. Do not use this to enter account passwords, OTPs, recovery codes, or other account secrets.

CAPTCHA or Cloudflare checkbox clicks require explicit user confirmation, or the user should click them manually.

## Workflow

1. Find the real CloakBrowser window by process path and title.
   - Prefer `Get-Process chrome | Where-Object { $_.Path -match 'cloakbrowser' -and $_.MainWindowTitle }`.
   - If the target title is missing, enumerate all CloakBrowser windows before deciding the browser closed.
2. Restore and foreground the window before reading controls.
   - `Bounds=Empty` usually means minimized, hidden, or not foregroundable yet.
   - Use `ShowWindowAsync(..., 9)` and `SetForegroundWindow(...)`, then wait briefly.
3. Enumerate descendants and inspect names, types, enabled state, and bounds.
   - Capture a compact table before clicking when the page state is uncertain.
   - Match by visible `Name`, `ControlType`, and `IsEnabled`.
4. Operate by UIA pattern.
   - Buttons: `InvokePattern`.
   - Search boxes and comboboxes: `ValuePattern.SetValue(...)`.
   - If a combo result appears, enumerate list items and invoke the exact user-selected institution result.
5. Re-read page state after each click.
   - A successful click should change visible controls, title, loaded-PDF markers, run logs, or output files.
   - Do not conclude from click success alone.
6. Keep the InstSci listener state in mind.
   - UIA can click a viewer download button, but `publisher-batch` only captures the file if the Python listener is still waiting for a download event.

## Elsevier Sequence

Typical sequence for Elsevier:

1. Confirm the `publisher-batch` process is still alive and the window title belongs to the target article or Elsevier page.
2. Use UI Automation to click public controls:
   - `Access through your organization`
   - institution search input such as `Organization name or email`
   - the user-selected institution result only when that institution was explicitly selected for this run
   - `Submit and continue`
   - Chrome PDF viewer toolbar `Download`
3. Hand off to the user for institution account login, password, 2FA, and CAPTCHA unless the user explicitly confirms a CAPTCHA checkbox click.
4. After the user completes verification, continue watching the run directory for `summary_partial.json`, `attempts.jsonl`, and `primary/pdfs/*.pdf`.

## PowerShell Snippets

Find the browser window:

```powershell
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$p = Get-Process chrome | Where-Object { $_.Path -match 'cloakbrowser' -and $_.MainWindowTitle -match 'ScienceDirect|Chromium|Find your organization' } | Select-Object -First 1
$w = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
$all = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
```

Find by visible text and invoke:

```powershell
$target = $null
for ($i = 0; $i -lt $all.Count; $i++) {
  $e = $all.Item($i)
  if ([string]$e.Current.Name -eq 'Access through your organization' -and $e.Current.IsEnabled) {
    $target = $e
    break
  }
}
$target.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
```

Set text through `ValuePattern`:

```powershell
$comboCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
  [System.Windows.Automation.ControlType]::ComboBox
)
$combo = $w.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $comboCond)
$combo.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue('Institution Name')
```

Restore the browser:

```powershell
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class Win32Show {
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}
'@
$p = Get-Process chrome | Where-Object { $_.Path -match 'cloakbrowser' -and $_.MainWindowTitle -match 'ScienceDirect|Chromium' } | Select-Object -First 1
[Win32Show]::ShowWindowAsync($p.MainWindowHandle, 9) | Out-Null
[Win32Show]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 700
```

Inventory interesting controls:

```powershell
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$w = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
$all = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$rows = @()
for ($i = 0; $i -lt $all.Count; $i++) {
  $e = $all.Item($i)
  $n = [string]$e.Current.Name
  if ($n -match 'Access|organization|Institution|PDF|Download|Sign|ScienceDirect|Institution Name') {
    $rows += [pscustomobject]@{
      Index = $i
      Name = $n
      Type = $e.Current.ControlType.ProgrammaticName
      Enabled = $e.Current.IsEnabled
      Bounds = $e.Current.BoundingRectangle.ToString()
    }
  }
}
$rows | Format-Table -AutoSize
```

Localized labels can be built with `[char]` codes to avoid mojibake in non-UTF-8 consoles. For example, the Chinese `Download` label in Chrome PDF viewer is:

```powershell
$downloadLabel = -join ([char]0x4e0b, [char]0x8f7d)
```

Do not fall back to hard-coded coordinates until text-based UIA has failed. If coordinates are necessary, first read the window bounds and take a visual checkpoint.

