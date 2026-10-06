<#
  Creates the two double-click shortcuts in the main folder:
      "Start TimeTablePro"   and   "Stop TimeTablePro"
  They run tools\start_silent.js / tools\stop_silent.js with wscript.exe (a program that has NO console window),
  so nothing ever flashes on the screen. Run this again if the project folder is ever moved to another place:
      powershell -NoProfile -ExecutionPolicy Bypass -File tools\make_shortcuts.ps1
#>
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$iconDir = Join-Path $root 'tools\icons'
if (-not (Test-Path -LiteralPath $iconDir)) { New-Item -ItemType Directory -Path $iconDir | Out-Null }

# --- draws the TimeTablePro logo (3x3 grid on a violet tile); the Stop icon is red with a white square ---
function New-Icon([string]$path, [bool]$stop) {
  $size = 256
  $bmp = New-Object System.Drawing.Bitmap $size, $size
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = 'AntiAlias'
  $g.Clear([System.Drawing.Color]::Transparent)
  $tile = New-Object System.Drawing.Drawing2D.GraphicsPath
  $r = 56; $d = $r * 2
  $tile.AddArc(0, 0, $d, $d, 180, 90); $tile.AddArc($size - $d, 0, $d, $d, 270, 90)
  $tile.AddArc($size - $d, $size - $d, $d, $d, 0, 90); $tile.AddArc(0, $size - $d, $d, $d, 90, 90); $tile.CloseFigure()
  $c1 = if ($stop) { [System.Drawing.Color]::FromArgb(239, 68, 68) } else { [System.Drawing.Color]::FromArgb(99, 102, 241) }
  $c2 = if ($stop) { [System.Drawing.Color]::FromArgb(185, 28, 28) } else { [System.Drawing.Color]::FromArgb(67, 56, 202) }
  $brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush ((New-Object System.Drawing.Point 0, 0), (New-Object System.Drawing.Point $size, $size), $c1, $c2)
  $g.FillPath($brush, $tile)
  if ($stop) {
    $white = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
    $g.FillRectangle($white, 80, 80, 96, 96)
  } else {
    $cell = 40; $gap = 12; $start = ($size - (3 * $cell + 2 * $gap)) / 2
    $alphas = 255, 140, 230, 140, 255, 140, 230, 140, 255
    for ($i = 0; $i -lt 9; $i++) {
      $x = $start + ($i % 3) * ($cell + $gap); $y = $start + [math]::Floor($i / 3) * ($cell + $gap)
      $b = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb($alphas[$i], 255, 255, 255))
      $g.FillRectangle($b, $x, $y, $cell, $cell)
    }
  }
  $g.Dispose()
  # .ico file that holds one PNG picture
  $ms = New-Object System.IO.MemoryStream; $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png); $png = $ms.ToArray(); $bmp.Dispose()
  $fs = [System.IO.File]::Create($path); $w = New-Object System.IO.BinaryWriter $fs
  $w.Write([uint16]0); $w.Write([uint16]1); $w.Write([uint16]1)                       # ICONDIR: reserved, type = icon, 1 picture
  $w.Write([byte]0); $w.Write([byte]0); $w.Write([byte]0); $w.Write([byte]0)         # 256 x 256, no palette
  $w.Write([uint16]1); $w.Write([uint16]32); $w.Write([uint32]$png.Length); $w.Write([uint32]22)
  $w.Write($png); $w.Close(); $fs.Close()
}

New-Icon (Join-Path $iconDir 'start.ico') $false
New-Icon (Join-Path $iconDir 'stop.ico') $true

# --- the shortcuts ---
$wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
$shell = New-Object -ComObject WScript.Shell
function New-Shortcut([string]$name, [string]$script, [string]$icon, [string]$text) {
  $lnk = $shell.CreateShortcut((Join-Path $root "$name.lnk"))
  $lnk.TargetPath = $wscript
  $lnk.Arguments = '//nologo "' + (Join-Path $root "tools\$script") + '"'
  $lnk.WorkingDirectory = $root
  $lnk.WindowStyle = 7                       # minimized (the program has no window anyway)
  $lnk.IconLocation = (Join-Path $iconDir $icon) + ',0'
  $lnk.Description = $text
  $lnk.Save()
}
New-Shortcut 'Start TimeTablePro' 'start_silent.js' 'start.ico' 'Starts TimeTablePro (no window) and opens it in the browser'
New-Shortcut 'Stop TimeTablePro'  'stop_silent.js'  'stop.ico'  'Stops TimeTablePro (no window)'
Write-Output 'Shortcuts created: "Start TimeTablePro" and "Stop TimeTablePro"'
