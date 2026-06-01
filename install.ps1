<#
.SYNOPSIS
MuhfiDesk - Bare-metal Installer for Windows

.DESCRIPTION
This script installs MuhfiDesk on Windows without Docker.
It creates a virtual environment, installs dependencies, and sets up a Scheduled Task
so the app runs silently in the background on startup.
#>

Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

# Configuration
$INSTALL_DIR = "C:\MuhfiDesk"
$REPO_URL = "https://github.com/SatyaGanzz/MuhfiDesk.git"
$PORT = 5000
$TASK_NAME = "MuhfiDesk_Service"

Write-Host "🚀 Starting MuhfiDesk Windows Installation..." -ForegroundColor Cyan

# 1. Check for Python
Write-Host "🔍 Checking prerequisites..."
if (!(Get-Command "python" -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Python is not installed or not in PATH! Please install Python 3 from python.org and try again." -ForegroundColor Red
    exit 1
}

if (!(Get-Command "git" -ErrorAction SilentlyContinue)) {
    Write-Host "⚠️ Git is not installed. You may need to download the source manually if not using Git." -ForegroundColor Yellow
}

# 2. Clone / Copy Repository
Write-Host "📂 Setting up directory at $INSTALL_DIR..."
if (Test-Path $INSTALL_DIR) {
    Write-Host "⚠️ Directory $INSTALL_DIR already exists." -ForegroundColor Yellow
    # Optionally update via git pull if it's a git repo
    if (Test-Path "$INSTALL_DIR\.git") {
        Set-Location $INSTALL_DIR
        git pull origin main
    }
} else {
    git clone $REPO_URL $INSTALL_DIR
    if (-not $?) {
        Write-Host "❌ Failed to clone repository. Make sure git is installed and REPO_URL is correct." -ForegroundColor Red
        exit 1
    }
}

Set-Location $INSTALL_DIR

# 3. Create Virtual Environment
Write-Host "🐍 Creating Python virtual environment..."
python -m venv .venv
if (-not $?) {
    Write-Host "❌ Failed to create virtual environment." -ForegroundColor Red
    exit 1
}

# 4. Install Dependencies
Write-Host "📚 Installing Python dependencies..."
$pipPath = Join-Path $INSTALL_DIR ".venv\Scripts\pip.exe"
if (Test-Path "requirements.txt") {
    & $pipPath install -r requirements.txt
} else {
    Write-Host "❌ requirements.txt not found!" -ForegroundColor Red
}

# 5. Create a VBS script to run it silently (hide terminal window)
$vbsScriptPath = Join-Path $INSTALL_DIR "start_hidden.vbs"
$vbsContent = @"
Set objShell = WScript.CreateObject("WScript.Shell")
objShell.Run "cmd /c `"$INSTALL_DIR\.venv\Scripts\python.exe`" `"$INSTALL_DIR\app.py`"", 0, False
"@
Set-Content -Path $vbsScriptPath -Value $vbsContent

# 6. Set up Scheduled Task to run on boot
Write-Host "⚙️ Creating Scheduled Task for automatic startup..."
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$vbsScriptPath`"" -WorkingDirectory $INSTALL_DIR
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

# Unregister if already exists
if (Get-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false
}

Register-ScheduledTask -TaskName $TASK_NAME -Action $action -Trigger $trigger -Principal $principal | Out-Null
Write-Host "✅ Scheduled Task '$TASK_NAME' created." -ForegroundColor Green

# 7. Start the Service Now
Write-Host "🔄 Starting MuhfiDesk service..."
Start-ScheduledTask -TaskName $TASK_NAME

Write-Host "`n===========================================================" -ForegroundColor Cyan
Write-Host "✅ MuhfiDesk successfully installed and running in the background!" -ForegroundColor Green
Write-Host "🌐 Access your dashboard at: http://localhost:$PORT" -ForegroundColor White
Write-Host "🛑 To stop the app, open Task Manager and end the 'python.exe' process, or run: Stop-ScheduledTask -TaskName $TASK_NAME" -ForegroundColor Yellow
Write-Host "===========================================================" -ForegroundColor Cyan
