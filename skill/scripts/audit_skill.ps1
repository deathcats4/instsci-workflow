param(
  [string]$SkillDir = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($SkillDir)) {
  $SkillDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
$SkillDir = (Resolve-Path -LiteralPath $SkillDir).Path

$allowedStandardStatus = @(
  "success",
  "auth_required",
  "human_verification_required",
  "waf_blocked",
  "access_unavailable",
  "publisher_error",
  "pdf_candidate_conflict",
  "capture_failed",
  "unsupported_publisher"
)

$allowedEvidence = @(
  "oa_direct",
  "publisher_open_pdf",
  "browser_verified",
  "http_preflight",
  "not_verified"
)

$allowedFileStatus = @("success", "unverified", "missing")
$sensitivePatterns = @(
  "password\s*[:=]",
  "token\s*[:=]",
  "cookie\s*[:=]",
  "Authorization\s*:\s*Bearer",
  "Set-Cookie\s*:",
  "C:\\Users\\[^\\]+",
  "[A-Z]:\\[^`r`n""]+",
  "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)

$problems = New-Object System.Collections.Generic.List[string]
$files = Get-ChildItem -LiteralPath $SkillDir -Recurse -File | Where-Object {
  $_.Extension -in @(".md", ".ps1", ".yaml", ".yml", ".json")
}

foreach ($file in $files) {
  $text = Get-Content -LiteralPath $file.FullName -Raw
  $rel = $file.FullName.Substring($SkillDir.Length).TrimStart("\")

  if ($rel -ne "scripts\audit_skill.ps1") {
    foreach ($pattern in $sensitivePatterns) {
      if ($text -match $pattern) {
        $problems.Add("Sensitive-looking pattern '$pattern' in $rel") | Out-Null
      }
    }
  }

  if ($rel -like "references\*.md" -or $rel -eq "SKILL.md") {
    if ($text -match 'Allowed `result` labels') {
      $problems.Add("Legacy result-label section remains in $rel") | Out-Null
    }
    if ($text -match 'Do not mark `unsupported`,') {
      $problems.Add("Legacy unsupported wording remains in $rel") | Out-Null
    }
  }
}

$script = Get-Content -LiteralPath (Join-Path $SkillDir "scripts\inspect_cloakbrowser.ps1") -Raw
foreach ($status in @("human_verification_required", "waf_blocked", "auth_required", "access_unavailable", "publisher_error")) {
  if ($script -notmatch [regex]::Escape($status)) {
    $problems.Add("inspect_cloakbrowser.ps1 does not emit/check $status") | Out-Null
  }
}
foreach ($required in @("BrowserProfile", "ProfileDir", "Normalize-PathForCompare", "profile_match", "matching_window_count", "other_windows_present")) {
  if ($script -notmatch [regex]::Escape($required)) {
    $problems.Add("inspect_cloakbrowser.ps1 is missing profile-aware field '$required'") | Out-Null
  }
}
if ($script -notmatch "standard_status") {
  $problems.Add("inspect_cloakbrowser.ps1 does not write standard_status") | Out-Null
}
if ($script -match 'return "captcha_or_waf"|legacy_state') {
  $problems.Add("inspect_cloakbrowser.ps1 still emits legacy captcha_or_waf") | Out-Null
}
if ($script -match 'waf_blocked"[^\r\n]*Access denied|Access denied[^\r\n]*waf_blocked"') {
  $problems.Add("inspect_cloakbrowser.ps1 classifies Access denied as waf_blocked") | Out-Null
}
if ($script -match 'auth_required"[^\r\n]*Purchase PDF|Purchase PDF[^\r\n]*auth_required"') {
  $problems.Add("inspect_cloakbrowser.ps1 classifies Purchase PDF as auth_required") | Out-Null
}
if ($script -notmatch 'states -contains "access_unavailable"') {
  $problems.Add("inspect_cloakbrowser.ps1 does not aggregate access_unavailable") | Out-Null
}

foreach ($relScript in @("scripts\inspect_cloakbrowser.ps1", "scripts\audit_runtime_contract.ps1")) {
  $parseErrors = $null
  $scriptPath = Join-Path $SkillDir $relScript
  if (-not (Test-Path -LiteralPath $scriptPath)) {
    $problems.Add("Missing PowerShell script $relScript") | Out-Null
    continue
  }
  $null = [System.Management.Automation.PSParser]::Tokenize(
    (Get-Content -LiteralPath $scriptPath -Raw),
    [ref]$parseErrors
  )
  if ($parseErrors -and $parseErrors.Count -gt 0) {
    $problems.Add("PowerShell parse errors in ${relScript}: $($parseErrors[0].Message)") | Out-Null
  }
}

$skill = Get-Content -LiteralPath (Join-Path $SkillDir "SKILL.md") -Raw
foreach ($status in $allowedStandardStatus) {
  if ($skill -notmatch [regex]::Escape($status)) {
    $problems.Add("SKILL.md is missing standard_status $status") | Out-Null
  }
}
foreach ($evidence in $allowedEvidence) {
  if ($skill -notmatch [regex]::Escape($evidence)) {
    $problems.Add("SKILL.md is missing result_evidence $evidence") | Out-Null
  }
}
foreach ($fileStatus in $allowedFileStatus) {
  if ($skill -notmatch [regex]::Escape($fileStatus)) {
    $problems.Add("SKILL.md is missing file_status $fileStatus") | Out-Null
  }
}

[pscustomobject]@{
  skill_dir = $SkillDir
  checked_files = $files.Count
  problem_count = $problems.Count
  problems = @($problems)
} | ConvertTo-Json -Depth 5

if ($problems.Count -gt 0) {
  exit 1
}
