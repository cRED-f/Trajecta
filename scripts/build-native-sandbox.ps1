# Native Win32 launcher, built at source-install time. Requires MSVC and Windows SDK,
# already required by the Tauri Windows native build.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$source = Join-Path $root 'native\windows\trajecta_sandbox.cpp'
$targetDir = Join-Path $root 'native\windows\bin'
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
$target = Join-Path $targetDir 'trajecta-native-sandbox.exe'
$compiler = Get-Command cl.exe -ErrorAction SilentlyContinue
Push-Location $targetDir
try {
  if ($compiler) {
    & $compiler.Source /nologo /std:c++17 /EHsc /O2 $source "/Fe:$target" /link userenv.lib advapi32.lib
  } else {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path $vswhere)) { throw 'MSVC toolchain not found. Install Visual Studio Build Tools with Desktop development with C++.' }
    $installDir = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $installDir) { throw 'Visual Studio C++ compiler workload not found.' }
    $vcvars = Join-Path $installDir 'VC\Auxiliary\Build\vcvars64.bat'
    if (-not (Test-Path $vcvars)) { throw 'vcvars64.bat not found: MSVC installation incomplete.' }
    $command = 'call "' + $vcvars + '" && cl /nologo /std:c++17 /EHsc /O2 "' + $source + '" /Fe:"' + $target + '" /link userenv.lib advapi32.lib'
    & cmd.exe /d /s /c $command
  }
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $target)) { throw 'Native Windows sandbox build failed; installation stopped.' }
} finally { Pop-Location }
Write-Host "Built $target"
