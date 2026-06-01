"""
MuhfiDesk Telegram Notifier
============================
Background service that sends:
1. Periodic system monitoring reports every N minutes
2. Smart alerts for CPU/RAM spikes, Docker crashes, brute force, backup events
"""

import os
import json
import time
import threading
import psutil
import platform
import requests
from datetime import datetime, timedelta

# ─── Config File ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
TELEGRAM_CONFIG_FILE = os.path.join(DATA_DIR, 'telegram_config.json')

# ─── Defaults ─────────────────────────────────────────────────
DEFAULT_CONFIG = {
    'enabled': False,
    'bot_token': '',
    'chat_id': '',
    'report_interval': 10,       # minutes
    'alert_cpu_threshold': 90,   # %
    'alert_ram_threshold': 90,   # %
    'alert_docker_crash': True,
    'alert_brute_force': True,
    'alert_backup': True,
    'last_report_time': 0
}

# ─── Network I/O baseline ────────────────────────────────────
_prev_net = None
_prev_net_time = None

# ─── Docker state tracking ────────────────────────────────────
_prev_docker_states = {}

# ─── Alert cooldowns (prevent spam) ──────────────────────────
_alert_cooldowns = {}
COOLDOWN_SECONDS = 300  # 5 min cooldown per alert type


def load_telegram_config():
    """Load Telegram config from disk"""
    try:
        if os.path.exists(TELEGRAM_CONFIG_FILE):
            with open(TELEGRAM_CONFIG_FILE, 'r') as f:
                cfg = json.load(f)
                # Merge with defaults for any missing keys
                merged = DEFAULT_CONFIG.copy()
                merged.update(cfg)
                return merged
    except Exception:
        pass
    return DEFAULT_CONFIG.copy()


def save_telegram_config(config):
    """Save Telegram config to disk"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TELEGRAM_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


def send_telegram(message, parse_mode='HTML'):
    """Send a message via Telegram Bot API"""
    config = load_telegram_config()
    token = config.get('bot_token', '').strip()
    chat_id = config.get('chat_id', '').strip()
    
    if not token or not chat_id:
        return False, 'Bot token or Chat ID not configured'
    
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': parse_mode,
        'disable_web_page_preview': True
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if data.get('ok'):
            return True, 'Message sent'
        else:
            return False, data.get('description', 'Unknown error')
    except Exception as e:
        return False, str(e)


def _can_alert(alert_type):
    """Check if we can send this alert type (cooldown)"""
    now = time.time()
    last = _alert_cooldowns.get(alert_type, 0)
    if now - last < COOLDOWN_SECONDS:
        return False
    _alert_cooldowns[alert_type] = now
    return True


def _format_bytes(b, suffix='B'):
    """Convert bytes to human-readable string"""
    for unit in ['', 'K', 'M', 'G', 'T']:
        if abs(b) < 1024.0:
            return f"{b:.1f} {unit}{suffix}"
        b /= 1024.0
    return f"{b:.1f} P{suffix}"


def _format_duration(seconds):
    """Format seconds into days/hours/minutes"""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    mins = int((seconds % 3600) // 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    parts.append(f"{mins}m")
    return ' '.join(parts)


def get_docker_containers():
    """Get list of Docker containers with status"""
    try:
        import docker as docker_lib
        client = docker_lib.from_env()
        containers = client.containers.list(all=True)
        result = []
        for c in containers:
            result.append({
                'name': c.name,
                'status': c.status,
                'image': str(c.image.tags[0]) if c.image.tags else 'unknown'
            })
        return result
    except Exception:
        return []


def build_monitoring_report():
    """Build the periodic monitoring report message"""
    global _prev_net, _prev_net_time
    
    now = datetime.now()
    
    # ─── CPU ──────────────────────────────
    cpu_percent = psutil.cpu_percent(interval=1)
    cpu_count = psutil.cpu_count()
    
    # ─── RAM ──────────────────────────────
    mem = psutil.virtual_memory()
    ram_used = _format_bytes(mem.used)
    ram_total = _format_bytes(mem.total)
    ram_percent = mem.percent
    
    # ─── Disk / Storage ───────────────────
    disk = psutil.disk_usage('/')
    disk_used = _format_bytes(disk.used)
    disk_total = _format_bytes(disk.total)
    disk_free = _format_bytes(disk.free)
    disk_percent = disk.percent
    
    # ─── Network Bandwidth ────────────────
    net = psutil.net_io_counters()
    net_time = time.time()
    
    if _prev_net and _prev_net_time:
        elapsed = net_time - _prev_net_time
        if elapsed > 0:
            dl_speed = (net.bytes_recv - _prev_net.bytes_recv) / elapsed
            ul_speed = (net.bytes_sent - _prev_net.bytes_sent) / elapsed
        else:
            dl_speed = 0
            ul_speed = 0
    else:
        dl_speed = 0
        ul_speed = 0
    
    _prev_net = net
    _prev_net_time = net_time
    
    total_download = _format_bytes(net.bytes_recv)
    total_upload = _format_bytes(net.bytes_sent)
    
    # ─── Uptime ───────────────────────────
    uptime_seconds = time.time() - psutil.boot_time()
    uptime_str = _format_duration(uptime_seconds)
    
    # ─── Energy ───────────────────────────
    try:
        energy_file = os.path.join(DATA_DIR, 'energy_data.json')
        if os.path.exists(energy_file):
            with open(energy_file, 'r') as f:
                energy_data = json.load(f)
                energy_kwh = energy_data.get('kwh', 0.0)
        else:
            energy_kwh = 0.0
    except Exception:
        energy_kwh = 0.0
    
    # ─── Docker ───────────────────────────
    containers = get_docker_containers()
    running = [c for c in containers if c['status'] == 'running']
    stopped = [c for c in containers if c['status'] != 'running']
    
    # ─── Build Message ────────────────────
    hostname = platform.node()
    
    msg = f"""📊 <b>MuhfiDesk Monitoring Report</b>
🖥️ <b>{hostname}</b> • {now.strftime('%d/%m/%Y %H:%M')}

⏱️ <b>Uptime:</b> {uptime_str}

💻 <b>CPU:</b> {cpu_percent}% ({cpu_count} cores)
{'🔴' if cpu_percent > 90 else '🟢'} {'CRITICAL!' if cpu_percent > 90 else 'Normal'}

🧠 <b>RAM:</b> {ram_used} / {ram_total} ({ram_percent}%)
{'🔴' if ram_percent > 90 else '🟢'} {'CRITICAL!' if ram_percent > 90 else 'Normal'}

💾 <b>Storage:</b>
   Used: {disk_used} / {disk_total} ({disk_percent}%)
   Free: {disk_free}

🌐 <b>Network:</b>
   ⬇️ Down: {_format_bytes(dl_speed)}/s
   ⬆️ Up: {_format_bytes(ul_speed)}/s
   📥 Total DL: {total_download}
   📤 Total UL: {total_upload}

⚡ <b>Energy Used:</b> {energy_kwh:.4f} kWh"""

    # Docker section
    if containers:
        msg += f"""

🐳 <b>Docker ({len(running)}/{len(containers)} running):</b>"""
        for c in running:
            msg += f"\n   ✅ {c['name']}"
        for c in stopped:
            msg += f"\n   ⛔ {c['name']} ({c['status']})"
    
    msg += f"\n\n🔄 <i>Next report in {load_telegram_config().get('report_interval', 10)} min</i>"
    
    return msg


# ══════════════════════════════════════════════════════════════
#  ALERT FUNCTIONS (called from app.py hooks)
# ══════════════════════════════════════════════════════════════

def alert_high_cpu(cpu_percent):
    """Send alert when CPU exceeds threshold"""
    config = load_telegram_config()
    if not config.get('enabled'):
        return
    threshold = config.get('alert_cpu_threshold', 90)
    if cpu_percent >= threshold and _can_alert('cpu'):
        msg = f"""🚨 <b>MuhfiDesk Alert — CPU TINGGI!</b>

💻 CPU saat ini: <b>{cpu_percent}%</b>
⚠️ Threshold: {threshold}%
🖥️ Server: {platform.node()}
🕐 Waktu: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}

<i>Periksa proses berat yang sedang berjalan.</i>"""
        send_telegram(msg)


def alert_high_ram(ram_percent):
    """Send alert when RAM exceeds threshold"""
    config = load_telegram_config()
    if not config.get('enabled'):
        return
    threshold = config.get('alert_ram_threshold', 90)
    if ram_percent >= threshold and _can_alert('ram'):
        mem = psutil.virtual_memory()
        msg = f"""🚨 <b>MuhfiDesk Alert — RAM TINGGI!</b>

🧠 RAM saat ini: <b>{ram_percent}%</b> ({_format_bytes(mem.used)} / {_format_bytes(mem.total)})
⚠️ Threshold: {threshold}%
🖥️ Server: {platform.node()}
🕐 Waktu: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}

<i>Pertimbangkan untuk menutup aplikasi yang tidak terpakai.</i>"""
        send_telegram(msg)


def alert_docker_crash(container_name, old_status, new_status):
    """Send alert when a Docker container crashes or stops unexpectedly"""
    config = load_telegram_config()
    if not config.get('enabled') or not config.get('alert_docker_crash'):
        return
    if _can_alert(f'docker_{container_name}'):
        msg = f"""🐳 <b>MuhfiDesk Alert — Container DOWN!</b>

📦 Container: <b>{container_name}</b>
🔴 Status: {old_status} → {new_status}
🖥️ Server: {platform.node()}
🕐 Waktu: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}

<i>Container mungkin crash atau dihentikan secara tiba-tiba.</i>"""
        send_telegram(msg)


def alert_brute_force(ip, attempt_count, max_attempts):
    """Send alert when brute force login detected"""
    config = load_telegram_config()
    if not config.get('enabled') or not config.get('alert_brute_force'):
        return
    if _can_alert(f'brute_{ip}'):
        msg = f"""🔐 <b>MuhfiDesk Alert — BRUTE FORCE DETECTED!</b>

🌐 IP Address: <b>{ip}</b>
🔑 Login gagal: <b>{attempt_count}x</b> (max: {max_attempts})
🛡️ IP telah di-lockout otomatis
🖥️ Server: {platform.node()}
🕐 Waktu: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}

<i>Pertimbangkan untuk memblokir IP ini secara permanen di Firewall.</i>"""
        send_telegram(msg)


def alert_backup(backup_name, success=True, error_msg=''):
    """Send alert for backup completion/failure"""
    config = load_telegram_config()
    if not config.get('enabled') or not config.get('alert_backup'):
        return
    
    if success:
        msg = f"""✅ <b>MuhfiDesk — Backup Berhasil!</b>

📦 File: <b>{backup_name}</b>
🖥️ Server: {platform.node()}
🕐 Waktu: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"""
    else:
        msg = f"""❌ <b>MuhfiDesk — Backup GAGAL!</b>

📦 File: <b>{backup_name}</b>
⚠️ Error: {error_msg}
🖥️ Server: {platform.node()}
🕐 Waktu: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}

<i>Periksa disk space dan permissions.</i>"""
    
    send_telegram(msg)


# ══════════════════════════════════════════════════════════════
#  BACKGROUND MONITORING LOOP
# ══════════════════════════════════════════════════════════════

def _monitoring_loop():
    """Background thread: sends periodic reports and checks for alerts"""
    global _prev_docker_states
    
    # Wait a few seconds for app to fully start
    time.sleep(10)
    
    while True:
        try:
            config = load_telegram_config()
            
            if not config.get('enabled') or not config.get('bot_token') or not config.get('chat_id'):
                time.sleep(30)
                continue
            
            interval_min = config.get('report_interval', 10)
            
            # ─── Check CPU/RAM alerts ─────────
            cpu = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory().percent
            
            alert_high_cpu(cpu)
            alert_high_ram(ram)
            
            # ─── Check Docker state changes ──
            try:
                containers = get_docker_containers()
                current_states = {c['name']: c['status'] for c in containers}
                
                for name, old_status in _prev_docker_states.items():
                    new_status = current_states.get(name)
                    if old_status == 'running' and new_status and new_status != 'running':
                        alert_docker_crash(name, old_status, new_status)
                
                _prev_docker_states = current_states
            except Exception:
                pass
            
            # ─── Periodic Report ──────────────
            now = time.time()
            last_report = config.get('last_report_time', 0)
            
            if now - last_report >= interval_min * 60:
                report = build_monitoring_report()
                success, _ = send_telegram(report)
                
                if success:
                    config['last_report_time'] = now
                    save_telegram_config(config)
            
            # Sleep 30 seconds between checks
            time.sleep(30)
            
        except Exception as e:
            # Silently continue on errors
            time.sleep(30)


def start_monitoring_thread():
    """Start the background monitoring thread"""
    t = threading.Thread(target=_monitoring_loop, daemon=True, name='TelegramMonitor')
    t.start()
    return t
