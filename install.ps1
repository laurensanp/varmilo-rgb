# Varmilo RGB installer. Run it with:
#   irm https://raw.githubusercontent.com/laurensanp/varmilo-rgb/main/install.ps1 | iex
# Running it again offers update and removal. Wrapped in a block so "return" can't close your window.
& {
  $ErrorActionPreference = "Stop"
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

  $base = "https://github.com/laurensanp/varmilo-rgb/releases/latest/download"
  $dir = "$env:LOCALAPPDATA\varmilo-rgb"
  $files = "varmilo.py", "varmilo_overlay.py", "Varmilo_Minilo75_Pro.js"
  $startupDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
  $autostart = "$startupDir\Varmilo Overlay.vbs"
  $legacyAutostart = "$startupDir\Varmilo Overlay.cmd"
  $shortcut = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Varmilo RGB.lnk"
  $settings = "$env:APPDATA\VarmiloOverlay"

  function Ask($question, $default) {
    $hint = if ($default) { "Y/n" } else { "y/N" }
    while ($true) {
      $answer = (Read-Host "$question [$hint]").Trim().ToLower()
      if ($answer -eq "") { return $default }
      if ($answer -in "y", "yes") { return $true }
      if ($answer -in "n", "no") { return $false }
    }
  }

  function Stop-Overlay {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
      Where-Object { $_.CommandLine -match "varmilo_overlay" } |
      ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  }

  function Get-PluginDir {
    $userDir = (Get-ItemProperty "HKCU:\Software\WhirlwindFX\SignalRgb" -ErrorAction SilentlyContinue).UserDirectory
    if ($userDir) { return Join-Path $userDir "Plugins" }
    return $null
  }

  function Find-Python {
    # The python.exe in WindowsApps is only a Store shortcut, so ask each candidate for its real path.
    foreach ($candidate in @("py", "python", "python3")) {
      if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) { continue }
      $extra = if ($candidate -eq "py") { @("-3") } else { @() }
      try {
        $exe = & $candidate @extra -c "import sys, tkinter; assert sys.version_info >= (3, 9); print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe)) { return $exe.Trim() }
      } catch {}
    }
    return $null
  }

  Write-Host ""
  Write-Host "Varmilo RGB" -ForegroundColor Yellow
  Write-Host "Per-key lighting for the Varmilo Minilo75 Pro: a small overlay, a tray icon and a SignalRGB plugin."
  Write-Host ""

  $pluginDir = Get-PluginDir
  $isUpdate = $false

  if (Test-Path "$dir\varmilo_overlay.py") {
    Write-Host "It's already installed."
    $choice = (Read-Host "[U]pdate, [R]emove or [C]ancel? [U]").Trim().ToLower()
    if ($choice -in "c", "cancel") { return }
    if ($choice -in "r", "remove") {
      Stop-Overlay
      Remove-Item $autostart, $legacyAutostart, $shortcut -Force -ErrorAction SilentlyContinue
      Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
      if ($pluginDir -and (Test-Path "$pluginDir\Varmilo_Minilo75_Pro.js") -and (Ask "Also remove the SignalRGB plugin?" $true)) {
        Remove-Item "$pluginDir\Varmilo_Minilo75_Pro.js" -Force
        Write-Host "Restart SignalRGB to unload the plugin."
      }
      if ((Test-Path $settings) -and (Ask "Also delete your saved colours and profiles?" $false)) {
        Remove-Item $settings -Recurse -Force
      }
      Write-Host "Removed." -ForegroundColor Green
      return
    }
    $isUpdate = $true
  }

  $python = Find-Python
  if (-not $python) {
    Write-Host "Python 3.9 or newer is required. Install it with:" -ForegroundColor Red
    Write-Host "  winget install Python.Python.3.12"
    Write-Host "then open a new PowerShell window and run this again."
    return
  }
  $pythonw = Join-Path (Split-Path $python) "pythonw.exe"

  if ($isUpdate) {
    # An update keeps every earlier choice; colours, profiles and the key map live in $settings and stay.
    $useAutostart = (Test-Path $autostart) -or (Test-Path $legacyAutostart)
    $startNow = $true
    $usePlugin = $pluginDir -and (Test-Path "$pluginDir\Varmilo_Minilo75_Pro.js")
    Write-Host "Updating. Your colours, profiles and settings are kept."
  } else {
    $useAutostart = Ask "Start it automatically with Windows?" $true
    $startNow = Ask "Start it now?" $true
    $usePlugin = $pluginDir -and (Ask "SignalRGB found. Install the plugin too? (SignalRGB effects on the keyboard)" $true)
  }

  Write-Host ""
  Write-Host "Downloading..."
  New-Item -ItemType Directory -Force $dir | Out-Null
  foreach ($file in $files) {
    Invoke-WebRequest "$base/$file" -OutFile "$dir\$file.download" -UseBasicParsing
  }
  Stop-Overlay
  foreach ($file in $files) { Move-Item "$dir\$file.download" "$dir\$file" -Force }
  Write-Host "Installed to $dir"

  Write-Host "Installing Python packages (hidapi, pystray, pillow)..."
  & $python -m pip install --user --quiet --disable-pip-version-check --upgrade hidapi pystray pillow
  if ($LASTEXITCODE -ne 0) { throw "pip could not install the packages" }

  $shell = New-Object -ComObject WScript.Shell
  $link = $shell.CreateShortcut($shortcut)
  $link.TargetPath = $pythonw
  $link.Arguments = "`"$dir\varmilo_overlay.py`""
  $link.WorkingDirectory = $dir
  $link.Description = "Varmilo RGB overlay"
  $link.Save()
  Write-Host "Added 'Varmilo RGB' to the Start menu"

  Remove-Item $legacyAutostart -Force -ErrorAction SilentlyContinue
  if ($useAutostart) {
    # UTF-16 so a user folder with umlauts survives
    Set-Content $autostart ('CreateObject("WScript.Shell").Run """{0}"" ""{1}"" --hidden", 0, False' -f $pythonw, "$dir\varmilo_overlay.py") -Encoding Unicode
    Write-Host "Autostart is on (starts hidden in the tray)"
  } else {
    Remove-Item $autostart -Force -ErrorAction SilentlyContinue
    Write-Host "Autostart is off"
  }

  if ($usePlugin) {
    New-Item -ItemType Directory -Force $pluginDir | Out-Null
    Copy-Item "$dir\Varmilo_Minilo75_Pro.js" "$pluginDir\Varmilo_Minilo75_Pro.js" -Force
    Write-Host "Installed the SignalRGB plugin. Restart SignalRGB once to load it."
  }

  if ($startNow) {
    Start-Process $pythonw -ArgumentList "`"$dir\varmilo_overlay.py`""
    Write-Host "The overlay is running"
  }

  Write-Host ""
  Write-Host "Done!" -ForegroundColor Green
  Write-Host "Connect the keyboard with the USB cable: lighting can't be controlled over the wireless dongle."
  Write-Host "Closing the window keeps the lighting running; quit from the tray icon."
  Write-Host "To update or remove it, run this installer again."
}
