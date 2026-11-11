# TeamTalk Player installer for Windows 10 and 11.
#
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/mad-gamer26/TeamTalk-Player/main/install.ps1 | iex"
#
# Installs for your user account in %LOCALAPPDATA%\TeamTalk Player (no
# administrator needed): its own Python environment with everything it needs
# (ffmpeg, yt-dlp and deno included), BearWare's TeamTalk SDK, your settings
# (config.json), and a task that starts it, without a window, when you sign in.
# Python itself comes from winget if it is not installed yet.
# Run it again to update (settings are kept), add a server, or uninstall.
#
# For testing: $env:TEAMTALK_PLAYER_SOURCE = a checkout folder or a .zip URL;
# $env:TEAMTALK_PLAYER_HOME = another folder (then no task is made).

# Native programs (python, pip, winget) are checked by their exit codes; in
# Windows PowerShell 5.1 "Stop" would turn their messages on stderr into errors.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$Source = if ($env:TEAMTALK_PLAYER_SOURCE) { $env:TEAMTALK_PLAYER_SOURCE } else { "https://github.com/mad-gamer26/TeamTalk-Player/archive/refs/heads/main.zip" }
$Testing = [bool]$env:TEAMTALK_PLAYER_HOME
$Root = if ($Testing) { $env:TEAMTALK_PLAYER_HOME } else { Join-Path $env:LOCALAPPDATA "TeamTalk Player" }
$App = Join-Path $Root "app"
$Venv = Join-Path $Root "venv"
$Config = Join-Path $Root "config.json"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$VenvPythonW = Join-Path $Venv "Scripts\pythonw.exe"
$Entry = Join-Path $App "teamtalk_player.py"
$TaskName = "TeamTalk Player"

function Say($text) { Write-Host $text }
function Fail($text) { Write-Host "Error: $text"; exit 1 }

function Ask($question, $default = "") {
    $prompt = if ($default) { "$question [$default]" } else { $question }
    $answer = Read-Host $prompt
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim()
}

function YesNo($question, [bool]$default) {
    while ($true) {
        $answer = Ask ($question + $(if ($default) { " (Y/n)" } else { " (y/N)" }))
        if ($answer -eq "") { return $default }
        if ($answer -match '^(y|yes)$') { return $true }
        if ($answer -match '^(n|no)$') { return $false }
        Say "Please answer y or n."
    }
}

# ---------------------------------------------------------------- Python

function Test-Python($exe, [string[]]$extra) {
    # $extra: arguments before -c, such as -3.12 for the py launcher.
    $arguments = @($extra) + @("-c", "import sys, venv, ensurepip; sys.exit(0 if sys.version_info >= (3, 10) else 1)")
    try {
        $null = "" | & $exe $arguments 2>&1
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($version in "-3.13", "-3.12", "-3.11", "-3.14", "-3.10", "-3") {
            if (Test-Python "py" @($version)) { return @("py", $version) }
        }
    }
    foreach ($cmd in Get-Command python, python3 -All -ErrorAction SilentlyContinue) {
        # Skip the Microsoft Store placeholder, which only opens the Store.
        if ($cmd.Source -like "*\WindowsApps\*") { continue }
        if (Test-Python $cmd.Source @()) { return @($cmd.Source) }
    }
    return $null
}

function Install-Python {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH') and run this again."
    }
    if (-not (YesNo "Python 3.10 or newer is needed. Install Python 3.13 for your account with winget?" $true)) { Fail "Python is needed." }
    winget install --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    $found = Find-Python
    if (-not $found) {
        $candidate = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
        if (Test-Python $candidate @()) { $found = @($candidate) }
    }
    if (-not $found) { Fail "Python was installed but could not be found. Open a new PowerShell window and run this again." }
    return $found
}

# ---------------------------------------------------------------- files

function Get-Source($destination) {
    New-Item -ItemType Directory -Force $destination | Out-Null
    if ($Source -match '^https?://') {
        $zip = Join-Path ([IO.Path]::GetTempPath()) ("teamtalk-player-" + [guid]::NewGuid() + ".zip")
        $unpacked = $zip + ".d"
        Invoke-WebRequest -UseBasicParsing $Source -OutFile $zip -ErrorAction Stop
        Expand-Archive $zip $unpacked -Force -ErrorAction Stop
        $inner = Get-ChildItem $unpacked -Directory | Select-Object -First 1
        Get-ChildItem $inner.FullName -Force | Move-Item -Destination $destination
        Remove-Item $zip, $unpacked -Recurse -Force
    } else {
        Get-ChildItem $Source -Force | Where-Object { $_.Name -notin ".git", ".venv", "__pycache__" } |
            Copy-Item -Destination $destination -Recurse -Force
    }
    if (-not (Test-Path (Join-Path $destination "teamtalk_player.py"))) { Fail "the download does not contain TeamTalk Player." }
}

function Invoke-Player {
    & $VenvPython $Entry @args
    return $LASTEXITCODE
}

function Install-Files($python) {
    Say "Downloading TeamTalk Player..."
    $new = Join-Path $Root "app.new"
    if (Test-Path $new) { Remove-Item $new -Recurse -Force }
    Get-Source $new
    if (Test-Path $App) { Remove-Item $App -Recurse -Force }
    Move-Item $new $App
    if (-not (Test-Path $VenvPython) -or -not (Test-Python $VenvPython @())) {
        Say "Making its Python environment..."
        if (Test-Path $Venv) { Remove-Item $Venv -Recurse -Force }
        "" | & $python[0] (@($python | Select-Object -Skip 1) + @("-m", "venv", $Venv))
        if ($LASTEXITCODE -ne 0) { Fail "could not make a Python environment in $Venv." }
    }
    Say "Installing its Python packages (ffmpeg, yt-dlp and deno included)..."
    & $VenvPython -m pip install --quiet --disable-pip-version-check --upgrade pip 2>$null | Out-Null
    & $VenvPython -m pip install --quiet --disable-pip-version-check --upgrade -r (Join-Path $App "requirements.txt")
    if ($LASTEXITCODE -ne 0) { Fail "the Python packages could not be installed (see the messages above)." }
}

function Install-Sdk {
    # Empty input: if the SDK is missing it is not downloaded without asking.
    $null = "" | & $VenvPython $Entry --install-sdk 2>&1
    if ($LASTEXITCODE -eq 0) { return }
    Say ""
    Say "TeamTalk Player needs BearWare's TeamTalk 5 SDK, which it downloads from bearware.dk."
    Say "BearWare offers it as a 30-day trial; an SDK license is required for use beyond that"
    Say "(https://bearware.dk/?page_id=316). It is installed for your user account only."
    if (-not (YesNo "Download the TeamTalk SDK now?" $true)) { Fail "the TeamTalk SDK is needed. Run this again to install it." }
    & $VenvPython $Entry --install-sdk --yes
    if ($LASTEXITCODE -ne 0) { Fail "the TeamTalk SDK could not be installed (see above)." }
    Say ""
    Say "Next, the TeamTalk SDK starts once. If BearWare shows a window called 'Activating TeamTalk SDK Trial',"
    Say "press OK in it (it may open behind this window: use Alt+Tab). TeamTalk Player cannot connect until then."
    & $VenvPython $Entry --activate-sdk
    if ($LASTEXITCODE -ne 0) { Fail "the TeamTalk SDK did not start (see above)." }
}

function Install-Command {
    $cmd = Join-Path $Root "teamtalk-player.cmd"
    Set-Content -Encoding ASCII $cmd "@`"$VenvPython`" `"$Entry`" -c `"$Config`" %*"
    if ($Testing) { return }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (($userPath -split ";") -notcontains $Root) {
        [Environment]::SetEnvironmentVariable("Path", ($userPath.TrimEnd(";") + ";" + $Root), "User")
    }
}

# ---------------------------------------------------------------- task

function Stop-Player {
    if (-not $Testing) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    # Only TeamTalk Player's own process (its python running this app folder).
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($Entry) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Start-Player {
    if ($Testing) { return }
    $action = New-ScheduledTaskAction -Execute $VenvPythonW -Argument "`"$Entry`" -c `"$Config`"" -WorkingDirectory $App
    # The signed-in account as Windows names it (USERDOMAIN can be WORKGROUP, e.g. over SSH).
    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
    $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
            -Description "Plays music into TeamTalk channels (installed by install.ps1)" -Force -ErrorAction Stop | Out-Null
        Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    } catch {
        Fail ("the task that starts TeamTalk Player could not be made: " + $_.Exception.Message)
    }
    Say "TeamTalk Player is running and starts when you sign in (Task Scheduler: $TaskName)."
}

function Show-WebAddress {
    if (-not (Test-Path $Config)) { return }
    $web = (Get-Content $Config -Raw | ConvertFrom-Json).web
    if ($web.enabled) {
        $hostName = if ($web.host -in "0.0.0.0", "::") { "127.0.0.1" } else { $web.host }
        Say "Web page: http://$($hostName):$($web.port)/"
    }
}

function Uninstall {
    if (-not (YesNo "Uninstall TeamTalk Player?" $false)) { Say "Nothing was changed."; exit 0 }
    Stop-Player
    if (-not $Testing) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue }
    foreach ($path in $App, $Venv, (Join-Path $Root "teamtalk-player.cmd")) {
        if (Test-Path $path) { Remove-Item $path -Recurse -Force }
    }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $Testing -and $userPath) {
        [Environment]::SetEnvironmentVariable("Path", (($userPath -split ";") | Where-Object { $_ -and $_ -ne $Root }) -join ";", "User")
    }
    if (YesNo "Also remove its settings, saved lists and the TeamTalk SDK?" $false) {
        Remove-Item $Root -Recurse -Force -ErrorAction SilentlyContinue
        Say "TeamTalk Player and its settings were removed."
    } else {
        Say "TeamTalk Player was removed. Its settings are kept in $Root."
    }
    exit 0
}

# ---------------------------------------------------------------- main

Say "TeamTalk Player installer"
$mode = "install"
if (Test-Path $Entry) {
    Say "TeamTalk Player is installed in $Root."
    Say "1. Update it (settings are kept)"
    Say "2. Add a TeamTalk server"
    Say "3. Uninstall"
    Say "4. Quit"
    while ($true) {
        $choice = Ask "Choice, 1 to 4" "1"
        switch ($choice) {
            "1" { $mode = "update" }
            "2" { $mode = "add" }
            "3" { Uninstall }
            "4" { exit 0 }
            default { Say "Please choose 1 to 4."; continue }
        }
        break
    }
}

$python = Find-Python
if (-not $python) { $python = Install-Python }
$pythonExe = $python[0]
$pythonArgs = @($python | Select-Object -Skip 1)
Say ("Using " + ("" | & $pythonExe ($pythonArgs + @("-c", "import sys; print('Python', sys.version.split()[0])"))) + ".")

if ($mode -eq "add") {
    & $VenvPython $Entry --setup -c $Config
    Stop-Player; Start-Player; Show-WebAddress
    exit 0
}

New-Item -ItemType Directory -Force $Root | Out-Null
Stop-Player
Install-Files $python
Install-Sdk
Install-Command
if (-not (Test-Path $Config)) {
    Say ""
    & $VenvPython $Entry --setup -c $Config
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Config)) { Fail "setup did not finish; run this installer again." }
}
Start-Player
Say ""
Say "TeamTalk Player is installed."
Show-WebAddress
Say "Settings: $Config"
Say "Command: teamtalk-player (in a new window), for example: teamtalk-player --setup to add a server"
