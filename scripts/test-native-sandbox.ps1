# Native Windows smoke tests, intentionally opt-in (requires compiled helper).
# Run from repository root: powershell -File scripts/test-native-sandbox.ps1
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$exe = Join-Path $root 'native\windows\bin\trajecta-native-sandbox.exe'
if (-not (Test-Path $exe)) { throw 'Build the native helper first.' }
$base = Join-Path ([System.IO.Path]::GetTempPath()) ('trajecta-sandbox-test-' + [Guid]::NewGuid().ToString('N'))
$workspace = Join-Path $base 'workspace'
$private = Join-Path $base 'private'
New-Item -ItemType Directory -Force -Path $workspace, $private | Out-Null
Set-Content -Encoding utf8 -Path (Join-Path $private 'secret.txt') -Value 'TRJ_PRIVATE_CANARY'
$profile = 'trajecta_test_' + [Guid]::NewGuid().ToString('N').Substring(0, 24)
$sid = (& $exe --profile $profile --prepare).Trim()
if ($LASTEXITCODE -ne 0 -or $sid -notmatch '^S-1-15-2-') { throw 'AppContainer profile failed.' }
try {
  & icacls.exe $workspace /grant ('*' + $sid + ':(OI)(CI)M') /T | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'Grant AppContainer folder permissions failed.' }
  $exec = @('--profile', $profile, '--execute', '--workspace', $workspace,
            '--timeout-ms', '5000', '--memory-bytes', '536870912', '--cpu-cores', '1')
  $output = (& $exe @exec --command "Set-Content -Path result.txt -Value 'sandboxed'; Write-Output 'TRJ_OK'" 2>&1 | Out-String)
  if ($LASTEXITCODE -ne 0 -or $output -notmatch 'TRJ_OK' -or -not (Test-Path (Join-Path $workspace 'result.txt'))) {
    throw "Native execution failed: $output"
  }
  Write-Host '[PASS] PowerShell execution and workspace write'

  $secret = Join-Path $private 'secret.txt'
  $output = (& $exe @exec --command "Get-Content -Path '$secret'" 2>&1 | Out-String)
  if ($output -match 'TRJ_PRIVATE_CANARY') { throw 'SECURITY FAILURE: readable private file outside workspace' }
  Write-Host '[PASS] Private folder outside workspace inaccessible'

  $timeoutExec = @('--profile', $profile, '--execute', '--workspace', $workspace,
            '--timeout-ms', '1000', '--memory-bytes', '536870912', '--cpu-cores', '1')
  $null = & $exe @timeoutExec --command 'Start-Sleep -Seconds 30' 2>&1
  if ($LASTEXITCODE -ne 124) { throw "Timeout expected exit 124, received $LASTEXITCODE" }
  Write-Host '[PASS] Timeout terminates process'

  Write-Host 'Network and descendant-process containment still require manual inspection.'
} finally {
  & icacls.exe $workspace /remove:g ('*' + $sid) /T | Out-Null
  Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue
}
