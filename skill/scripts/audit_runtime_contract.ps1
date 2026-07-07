param(
  [string]$Python = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Python)) {
  $cmd = Get-Command instsci -ErrorAction SilentlyContinue
  if (-not $cmd) {
    throw "instsci command is not on PATH. Pass -Python <path-to-installs-python>."
  }
  $launcher = Get-Content -LiteralPath $cmd.Path -Raw -Encoding Byte -ErrorAction SilentlyContinue
  $strings = [System.Text.Encoding]::ASCII.GetString($launcher)
  if ($strings -match '#!([^\r\n]+python\.exe)') {
    $Python = $Matches[1]
  }
}

if ([string]::IsNullOrWhiteSpace($Python) -or -not (Test-Path -LiteralPath $Python)) {
  throw "Could not locate InstSci runtime Python. Pass -Python <path-to-installs-python>."
}

$script = @'
import importlib.util
import pathlib
import subprocess
import sys

mods = ["instsci.cli", "instsci.browser_doctor", "instsci.publisher_matrix", "instsci.publisher_batch"]
paths = []
for name in mods:
    spec = importlib.util.find_spec(name)
    if not spec or not spec.origin:
        raise SystemExit(f"missing module: {name}")
    paths.append(spec.origin)
for test_name in [
    "instsci.tests.test_status_contract",
    "instsci.tests.test_contract_fixtures",
]:
    test_spec = importlib.util.find_spec(test_name)
    if not test_spec or not test_spec.origin:
        raise SystemExit(f"missing runtime test module: {test_name}")
    paths.append(test_spec.origin)

compile_result = subprocess.run([sys.executable, "-m", "py_compile", *paths], text=True)
if compile_result.returncode:
    raise SystemExit(compile_result.returncode)
test_result = subprocess.run([sys.executable, "-m", "unittest", "instsci.tests.test_status_contract", "instsci.tests.test_contract_fixtures", "-v"], text=True)
if test_result.returncode:
    raise SystemExit(test_result.returncode)

print("runtime contract OK")
for path in paths:
    print(pathlib.Path(path))
'@

$script | & $Python -
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
