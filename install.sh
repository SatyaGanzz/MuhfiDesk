#!/bin/bash
# MuhfiDesk - Bare-metal Installer for Linux
# This script installs MuhfiDesk as a systemd service.

set -e

# Configuration
INSTALL_DIR="/opt/muhfidesk"
REPO_URL="https://github.com/SatyaGanzz/MuhfiDesk.git"
PORT=5000

# 1. Check for root privileges
if [ "$EUID" -ne 0 ]; then
  echo "❌ Please run as root (e.g. sudo bash install.sh)"
  exit 1
fi

echo "🚀 Starting MuhfiDesk Installation..."

# 2. Update and install dependencies
echo "📦 Installing system dependencies..."
if [ -x "$(command -v apt-get)" ]; then
    apt-get update -y
    apt-get install -y python3 python3-pip python3-venv git curl
    apt-get install -y fastfetch || echo "⚠️ Failed to install fastfetch, skipping..."
elif [ -x "$(command -v dnf)" ]; then
    dnf install -y python3 python3-pip git curl
    dnf install -y fastfetch || echo "⚠️ Failed to install fastfetch, skipping..."
elif [ -x "$(command -v pacman)" ]; then
    pacman -Sy --noconfirm python python-pip git curl
    pacman -S --noconfirm fastfetch || echo "⚠️ Failed to install fastfetch, skipping..."
else
    echo "⚠️ Unsupported package manager. Please install python3, pip, git, and fastfetch manually."
fi

if ! command -v fastfetch >/dev/null 2>&1; then
    echo "==========================================================="
    echo "⚠️  FASTFETCH TIDAK DITEMUKAN!"
    echo "Aplikasi ini merekomendasikan fastfetch untuk menampilkan info sistem."
    echo "Silakan install manual, misalnya: sudo apt install fastfetch"
    echo "==========================================================="
    sleep 3
fi

# 3. Clone repository
echo "📂 Setting up installation directory at $INSTALL_DIR..."
if [ -d "$INSTALL_DIR" ]; then
    echo "⚠️ Directory $INSTALL_DIR already exists. Updating..."
    cd $INSTALL_DIR
    git pull origin main || echo "⚠️ Failed to git pull, continuing anyway..."
else
    git clone $REPO_URL $INSTALL_DIR
    cd $INSTALL_DIR
fi

# 4. Set up Virtual Environment
echo "🐍 Creating Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

# 5. Install Python requirements
echo "📚 Installing Python dependencies..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo "❌ requirements.txt not found! Installation may be incomplete."
fi

# 6. Create systemd service
SERVICE_FILE="/etc/systemd/system/muhfidesk.service"
echo "⚙️ Creating systemd service at $SERVICE_FILE..."

cat <<EOF > $SERVICE_FILE
[Unit]
Description=MuhfiDesk Dashboard Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python $INSTALL_DIR/app.py
Restart=always
RestartSec=3
Environment="PATH=$INSTALL_DIR/.venv/bin:/usr/bin"
Environment="FLASK_ENV=production"

[Install]
WantedBy=multi-user.target
EOF

# 7. Install CLI Tool
echo "🛠️ Installing CLI tool..."
if [ -f "$INSTALL_DIR/scripts/muhfidesk" ]; then
    cp $INSTALL_DIR/scripts/muhfidesk /usr/local/bin/muhfidesk
    chmod +x /usr/local/bin/muhfidesk
    echo "✅ CLI command 'muhfidesk' installed."
fi

# 8. Enable and start the service
echo "🔄 Starting MuhfiDesk service..."
if command -v systemctl >/dev/null 2>&1 && pidof systemd >/dev/null 2>&1; then
    systemctl daemon-reload
    systemctl enable muhfidesk.service
    systemctl restart muhfidesk.service
    echo ""
    echo "==========================================================="
    echo "✅ MuhfiDesk successfully installed and running as a background service!"
    echo "🌐 Access your dashboard at: http://localhost:$PORT"
    echo "📄 To view logs, run: journalctl -u muhfidesk -f"
    echo "==========================================================="
else
    echo "⚠️ 'systemd' is not running (likely inside a container or WSL)."
    echo "✅ MuhfiDesk successfully installed!"
    echo "🔄 Starting MuhfiDesk dashboard in the background..."
    nohup $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/app.py > $INSTALL_DIR/app.log 2>&1 &
    
    # Add cron job for auto-start on boot for non-systemd environments
    echo "⚙️ Setting up @reboot cron job for auto-start..."
    if command -v crontab >/dev/null 2>&1; then
        (crontab -l 2>/dev/null | grep -v "$INSTALL_DIR/app.py"; echo "@reboot nohup $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/app.py > $INSTALL_DIR/app.log 2>&1 &") | crontab -
    else
        echo "⚠️ crontab not found. Auto-start on reboot may not work. You can manually start with: muhfidesk start"
    fi

    echo "🌐 Dashboard is now running! Access it at: http://localhost:$PORT"
    echo "==========================================================="
fi
