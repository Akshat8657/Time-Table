<#
  Starts ONE program completely hidden (no window at all) and sends everything it prints to a log file.
  start.bat uses this so that the two servers do not show black windows.

    -WorkDir   folder the program runs in
    -Log       file that receives everything the program prints (its old content is replaced)
    -Cmd       the command line to run, for example:  npm run dev
#>
param(
  [Parameter(Mandatory = $true)][string]$WorkDir,
  [Parameter(Mandatory = $true)][string]$Log,
  [Parameter(Mandatory = $true)][string]$Cmd
)

$ErrorActionPreference = 'Stop'

$logDir = Split-Path -Parent $Log
if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

$env:PYTHONUNBUFFERED = '1'   # log lines appear immediately (the started program inherits this)

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $env:ComSpec
# "/s /c" plus ONE outer pair of quotes: cmd removes that outer pair and runs the rest exactly as written
$psi.Arguments = '/d /s /c "' + $Cmd + ' > "' + $Log + '" 2>&1"'
$psi.WorkingDirectory = $WorkDir
# Started through the Windows shell: no window (hidden), and - unlike a plain CreateProcess - the shell does NOT hand
# our open files over to the new program. (Otherwise the servers would keep the caller's log file locked.)
$psi.UseShellExecute = $true
$psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden

[void][System.Diagnostics.Process]::Start($psi)
