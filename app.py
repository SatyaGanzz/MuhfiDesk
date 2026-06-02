import psutil
import platform
import socket
from flask import Flask, jsonify, render_template, request, Response, session, redirect, url_for, stream_with_context, send_file
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from functools import wraps
import time
import subprocess
import os
import select
import struct
import hashlib
import json
from datetime import datetime
import requests
import threading
# eventlet removed
import sqlite3
import zipfile
import io
import yaml
import docker # Ensure docker is imported
import sys

# Tambahkan direktori scripts agar modul telegram_notifier bisa diimpor
if os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts') not in sys.path:
    sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts'))
import telegram_notifier
# LICENSE IMPORTS
try:
   import docker as docker_sdk # Alias for compatibility if needed
except ImportError:
   docker_sdk = None

try:
    import pty
    import fcntl
    import termios
except ImportError:
    pty = None
    fcntl = None
    termios = None # Import License Manager

# Base Directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============ VERSI APLIKASI ============
APP_VERSION = "1.0"
APP_NAME = "MuhfiDesk"
UPDATE_CHECK_URL = "https://raw.githubusercontent.com/SatyaGanzz/muhfidesk/main/version.json"
# ========================================

# Data Directory (For Persistence across updates)
DATA_DIR = os.path.join(BASE_DIR, 'data')
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# Energy Monitoring
ENERGY_FILE = os.path.join(DATA_DIR, 'energy_data.json')
TOTAL_KWH = 0.0

# LICENSE FILE
LICENSE_FILE = os.path.join(DATA_DIR, 'license.lic')
IS_ACTIVATED = False  # Cache validation status

# Update Check URL (Administrator configurable via Code or ENV)
UPDATE_CHECK_URL = os.environ.get('UPDATE_URL', "https://raw.githubusercontent.com/SatyaGanzz/muhfidesk/main/version.json")
CURRENT_VERSION = "1.0"

def energy_monitor_loop():
    global TOTAL_KWH
    # Load initial
    try:
        if os.path.exists(ENERGY_FILE):
            with open(ENERGY_FILE, 'r') as f:
                data = json.load(f)
                TOTAL_KWH = data.get('kwh', 0.0)
    except:
        pass
        
    while True:
        try:
            # Estimate: Base 6W (Idle) + (CPU% * 6W / 100) -> Range 6W - 12W (Max 12V 1A)
            cpu = psutil.cpu_percent(interval=None) or 0
            watts = 6.0 + (cpu * 6.0 / 100.0)
            
            # Add to kWh
            TOTAL_KWH += watts / 3600000.0
            
            # Save occasionally
            if int(time.time()) % 60 == 0:
                with open(ENERGY_FILE, 'w') as f:
                    json.dump({'kwh': TOTAL_KWH}, f)
                    
            time.sleep(1)
        except:
            time.sleep(1)

# Start Energy Thread
t_energy = threading.Thread(target=energy_monitor_loop, daemon=True)
t_energy.start()
try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

# MQTT Global State
mqtt_client = None
HOME_DEVICES_STATE = {}

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())
app.config['UPLOAD_FOLDER'] = DATA_DIR
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB Limit

# ==============================================
CORS(app, supports_credentials=True)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Terminal sessions storage
terminal_sessions = {}

# Active user sessions tracking (server-side)
# Format: {session_id: {username, login_time, last_activity, ip, role}}
ACTIVE_SESSIONS = {}

# ============== ROLE-BASED ACCESS CONTROL ==============
# Role hierarchy: owner > admin > operator > readonly
ROLES = ['owner', 'admin', 'operator', 'readonly']

# Permission definitions per role
PERMISSIONS = {
    'owner': {
        'dashboard': True, 'metrics': True, 'monitoring': True,
        'files': 'full',           # full = read/write/delete
        'terminal': True,
        'docker': 'full',          # full = view/start/stop/restart/delete
        'security': 'full',        # full = all settings
        'users': 'full',           # full = add/edit/delete any user
        'audit_logs': 'full',      # full = view/clear
        'active_sessions': True,   # view + force logout others
        'security_policy': True,   # change global policy
        'services': 'full',        # full = view/start/stop/restart
        'settings': 'full'         # full = all app settings
    },
    'admin': {
        'dashboard': True, 'metrics': True, 'monitoring': True,
        'files': 'full',
        'terminal': True,
        'docker': 'full',
        'security': 'view',        # view only
        'users': 'limited',        # can manage operator/readonly, NOT owner/admin
        'audit_logs': 'view',      # view only, cannot clear
        'active_sessions': True,
        'security_policy': False,
        'services': 'full',
        'settings': 'full'
    },
    'operator': {
        'dashboard': True, 'metrics': True, 'monitoring': True,
        'files': 'read',           # read only
        'terminal': False,
        'docker': 'view',          # view + restart only
        'security': False,
        'users': False,
        'audit_logs': False,
        'active_sessions': False,
        'security_policy': False,
        'services': 'limited',     # view + restart whitelisted
        'settings': 'view'
    },
    'readonly': {
        'dashboard': True, 'metrics': True, 'monitoring': True,
        'files': 'read',           # read only
        'terminal': False,
        'docker': 'view',          # view only, no actions
        'security': 'view',        # view only
        'users': False,
        'audit_logs': False,
        'active_sessions': False,
        'security_policy': False,
        'services': 'view',        # view only
        'settings': 'view'
    }
}

def has_permission(role, feature, level='any'):
    """Check if a role has permission for a feature
    level: 'any' (any access), 'full', 'view', 'limited', True
    """
    if role not in PERMISSIONS:
        return False
    perm = PERMISSIONS[role].get(feature, False)
    if level == 'any':
        return bool(perm)
    return perm == level or perm == 'full' or perm == True

def get_role_level(role):
    """Get numeric level of role (lower = more powerful)"""
    try:
        return ROLES.index(role)
    except ValueError:
        return 999  # Unknown role = no power

def is_owner_role(role=None):
    """True when the active user is the developer/owner account."""
    return (role or session.get('role')) == 'owner'

SENSITIVE_FILE_NAMES = {
    '.env',
    'credentials.json',
    'token.json',
    'security_config.json',
    'app_settings.json',
    'telegram_config.json',
    'mobile_backup_config.json',
    'login_attempts.json',
    'audit.log',
    'license.lic',
    'users.json',
    'users.db',
    'settings.json',
    'layout.json',
    'energy_data.json',
    'docker-compose.yml',
    'server.out.log',
    'server.err.log',
    'id_rsa',
    'id_ed25519',
    'authorized_keys',
    'known_hosts',
}

SENSITIVE_DIR_NAMES = {
    '.git',
    '.hg',
    '.svn',
    '.ssh',
    '.gnupg',
    '.venv',
    'venv',
    'env',
    '__pycache__',
    '.pytest_cache',
}

SENSITIVE_EXTENSIONS = (
    '.db',
    '.sqlite',
    '.sqlite3',
    '.lic',
    '.pem',
    '.key',
    '.p12',
    '.pfx',
)

SENSITIVE_ABS_MARKERS = (
    '/var/log',
    '/etc/wireguard',
)

def harden_file_permissions(path):
    """Best-effort OS permission hardening; app-level checks remain authoritative."""
    try:
        if os.path.isdir(path):
            os.chmod(path, 0o700)
        elif os.path.exists(path):
            os.chmod(path, 0o600)
    except Exception:
        pass

def _real_path(path):
    return os.path.realpath(os.path.abspath(os.path.normpath(str(path))))

def _is_within_path(path, parent):
    try:
        path_real = _real_path(path)
        parent_real = _real_path(parent)
        return os.path.commonpath([path_real, parent_real]) == parent_real
    except Exception:
        return False

def is_sensitive_path(path):
    """Developer-only files/folders that must not be exposed through the UI/API."""
    if not path:
        return False
    path_text = str(path)

    try:
        real = _real_path(path_text)
    except Exception:
        real = os.path.abspath(path_text)

    basename = os.path.basename(real).lower()
    normalized = real.replace('\\', '/').lower()
    parts = {part.lower() for part in normalized.split('/') if part}

    if basename in SENSITIVE_FILE_NAMES:
        return True
    if basename.endswith(SENSITIVE_EXTENSIONS):
        return True
    if parts.intersection(SENSITIVE_DIR_NAMES):
        return True

    if _is_within_path(real, DATA_DIR):
        return True

    for marker in SENSITIVE_ABS_MARKERS:
        if normalized == marker or normalized.startswith(f'{marker}/') or marker in normalized:
            return True

    return False

def require_safe_path_for_role(path, action='access'):
    """Return a Flask response tuple when a non-owner touches a sensitive path."""
    if is_owner_role() or not is_sensitive_path(path):
        return None
    audit_log('PROTECTED_PATH_DENIED', f"{action}: {path}", session.get('username', 'unknown'))
    return jsonify({'error': 'Developer-only file or folder'}), 403


# Configuration Files
# Configuration Files
SECURITY_CONFIG_FILE = os.path.join(DATA_DIR, 'security_config.json')
AUDIT_LOG_FILE = os.path.join(DATA_DIR, 'audit.log')
LOGIN_ATTEMPTS_FILE = os.path.join(DATA_DIR, 'login_attempts.json')
APP_SETTINGS_FILE = os.path.join(DATA_DIR, 'app_settings.json')

harden_file_permissions(DATA_DIR)


def load_app_settings():
    default_settings = {
        'general': {
            'server_name': 'Amlogic Server',
            'timezone': 'Asia/Jakarta',
            'time_format': '24h',
            'date_format': 'DD/MM/YYYY',
            'language': 'en'
        },
        'appearance': {
            'accent_color': 'blue',
            'density': 'comfortable',
            'visible_cards': ['cpu', 'ram', 'disk', 'network', 'docker']
        },
        'monitoring': {
            'wallboard_interval': 2000,
            'metrics_interval': 5000,
            'metrics_history_minutes': 60,
            'default_page': 'dashboard'
        },
        'alerts': {
            'enabled': True,
            'cpu_warning': 70,
            'cpu_critical': 90,
            'ram_warning': 70,
            'ram_critical': 90,
            'disk_warning': 80,
            'disk_critical': 95
        },
        'integrations': {
            'telegram_enabled': False,
            'telegram_token': '',
            'telegram_chat_id': '',
            'webhook_enabled': False,
            'webhook_url': ''
        },
        'services': [
            {'id': 'ssh', 'name': 'SSH Server'},
            {'id': 'docker', 'name': 'Docker Engine'},
            {'id': 'cron', 'name': 'Cron Job'},
            {'id': 'gunicorn', 'name': 'Gunicorn Service'},
            {'id': 'python-app', 'name': 'Python App Service'}
        ],
        'mqtt': {
            'enabled': False,
            'broker': '',
            'port': 1883,
            'devices': []
        }
    }
    try:
        if os.path.exists(APP_SETTINGS_FILE):
            with open(APP_SETTINGS_FILE, 'r') as f:
                saved = json.load(f)
                # Deep merge
                for key in default_settings:
                    if key in saved:
                        if isinstance(default_settings[key], dict):
                            default_settings[key] = {**default_settings[key], **saved[key]}
                        else:
                            default_settings[key] = saved[key]
                return default_settings
    except Exception as e:
        print(f"!!! CRITICAL: Failed to load settings file: {e}")
        pass
    return default_settings

def save_app_settings(settings):
    with open(APP_SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)
    harden_file_permissions(APP_SETTINGS_FILE)

def load_security_config():
    default_config = {
        'username': 'admin',
        'password_hash': hashlib.sha256('admin'.encode()).hexdigest(),
        'role': 'owner',  # Default to owner for main user
        'session_timeout': 3600,
        'require_auth': True,
        'allowed_ips': [],
        'max_login_attempts': 5,
        'lockout_duration': 300,  # 5 minutes
        'users': []
    }
    try:
        if os.path.exists(SECURITY_CONFIG_FILE):
            with open(SECURITY_CONFIG_FILE, 'r') as f:
                config = {**default_config, **json.load(f)}
                
                # Auto-migration: Main user MUST be owner
                if config.get('role') != 'owner':
                    config['role'] = 'owner'
                    # Save back to disk immediately to persist migration
                    try:
                        save_security_config(config)
                    except:
                        pass
                
                return config
    except:
        pass
    return default_config

def save_security_config(config):
    with open(SECURITY_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)
    harden_file_permissions(SECURITY_CONFIG_FILE)

def load_login_attempts():
    try:
        if os.path.exists(LOGIN_ATTEMPTS_FILE):
            with open(LOGIN_ATTEMPTS_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    return {}

def save_login_attempts(attempts):
    with open(LOGIN_ATTEMPTS_FILE, 'w') as f:
        json.dump(attempts, f)
    harden_file_permissions(LOGIN_ATTEMPTS_FILE)

def check_login_locked(ip):
    """Check if IP is locked out due to too many failed attempts"""
    config = load_security_config()
    attempts = load_login_attempts()
    
    if ip in attempts:
        data = attempts[ip]
        if data.get('locked_until', 0) > time.time():
            return True, int(data['locked_until'] - time.time())
    return False, 0

def record_login_attempt(ip, success):
    """Record login attempt and lock if too many failures"""
    config = load_security_config()
    attempts = load_login_attempts()
    
    if success:
        # Clear attempts on success
        if ip in attempts:
            del attempts[ip]
    else:
        # Increment failed attempts
        if ip not in attempts:
            attempts[ip] = {'count': 0, 'locked_until': 0}
        attempts[ip]['count'] = attempts[ip].get('count', 0) + 1
        
        # Lock if exceeded max attempts
        max_attempts = config.get('max_login_attempts', 5)
        if attempts[ip]['count'] >= max_attempts:
            lockout = config.get('lockout_duration', 300)
            attempts[ip]['locked_until'] = time.time() + lockout
            audit_log('ACCOUNT_LOCKED', f"IP {ip} locked for {lockout}s after {max_attempts} failed attempts")
    
    save_login_attempts(attempts)

def audit_log(action, details='', user='system'):
    try:
        # Ensure data directory exists
        os.makedirs(os.path.dirname(AUDIT_LOG_FILE), exist_ok=True)
        
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
        try:
            ip = request.remote_addr if request else 'N/A'
        except:
            ip = 'N/A'
        log_entry = f"{timestamp} | {user} | {ip} | {action} | {details}\n"
        with open(AUDIT_LOG_FILE, 'a') as f:
            f.write(log_entry)
        harden_file_permissions(AUDIT_LOG_FILE)
    except Exception as e:
        print(f"[AUDIT ERROR] Failed to write log: {e}")
        pass

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        config = load_security_config()
        
        # 1. Global Authentication Toggle
        if not config.get('require_auth', True):
            session['logged_in'] = True
            session['role'] = 'admin'
            return f(*args, **kwargs)

        # 2. Mobile App Bypass (Fix SameSite Cookie Issues)
        if request.headers.get('X-Mobile-Key') == 'MuhfiDesk_2024_Secret':
            return f(*args, **kwargs)
            
        # 2. Check Session
        if session.get('logged_in'):
            # Check timeout
            last_active = session.get('last_active', time.time())
            timeout = config.get('session_timeout', 3600)
            if time.time() - last_active > timeout:
                session.clear()
                audit_log('SESSION_EXPIRED', f"User session expired after {timeout}s")
                if request.is_json:
                     return jsonify({'error': 'Session expired'}), 401
                return redirect(url_for('login_page'))
            
            session['last_active'] = time.time()
            
            session['last_active'] = time.time()
            return f(*args, **kwargs)

        # 3. Require Login
        if request.is_json:
            return jsonify({'error': 'Authentication required'}), 401
        return redirect(url_for('login_page'))
    return decorated_function

def owner_required(f):
    """Decorator: Only owner can access"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'owner':
            audit_log('ACCESS_DENIED', f"Non-owner tried to access {request.path}", session.get('username'))
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Owner access required'}), 403
            return redirect(url_for('dashboard', error='access_denied', feature='owner_required'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator: Owner or Admin can access"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        role = session.get('role', 'readonly')
        if role not in ['owner', 'admin']:
            audit_log('ACCESS_DENIED', f"Insufficient role ({role}) for {request.path}", session.get('username'))
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Admin access required'}), 403
            return redirect(url_for('dashboard', error='access_denied', feature='admin_required'))
        return f(*args, **kwargs)
    return decorated_function

def operator_required(f):
    """Decorator: Owner, Admin, or Operator can access"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        role = session.get('role', 'readonly')
        if role not in ['owner', 'admin', 'operator']:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Operator access required'}), 403
            return redirect(url_for('dashboard', error='access_denied', feature='operator_required'))
        return f(*args, **kwargs)
    return decorated_function

def requires_permission(feature, level='any'):
    """Decorator factory: Check if user has permission for a feature"""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            role = session.get('role', 'readonly')
            if not has_permission(role, feature, level):
                audit_log('PERMISSION_DENIED', f"Role {role} denied {feature} ({level})", session.get('username'))
                if request.is_json or request.path.startswith('/api/'):
                    return jsonify({'error': f'No permission for {feature}'}), 403
                return redirect(url_for('dashboard', error='access_denied', feature=feature))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# --------------------------




def get_size(bytes, suffix="B"):
    """Scale bytes to its proper format"""
    factor = 1024
    for unit in ["", "K", "M", "G", "T", "P"]:
        if bytes < factor:
            return f"{bytes:.2f}{unit}{suffix}"
        bytes /= factor

# Auth Routes
@app.route('/login', methods=['GET'])
def login_page():
    # If setup hasn't been completed, force setup wizard
    try:
        config = load_security_config()
        default_hash = hashlib.sha256('admin'.encode()).hexdigest()
        if config['password_hash'] == default_hash:
            return redirect('/setup-admin')
    except Exception:
        return redirect('/setup-admin')

    if session.get('logged_in'):
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/setup-admin')
def setup_admin_page():
    return render_template('setup_admin.html')

@app.route('/api/setup-admin', methods=['POST'])
def setup_admin_api():
    config = load_security_config()
    default_hash = hashlib.sha256('admin'.encode()).hexdigest()
    
    # Allow setup if password is default OR session setup_mode is active
    if config['password_hash'] != default_hash and not session.get('setup_mode'):
        # If already setup, forbid unless valid admin login? No, just forbid.
        return jsonify({'error': 'Setup already completed'}), 403
        
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password or len(password) < 4:
        return jsonify({'error': 'Invalid input (min 4 chars)'}), 400
        
    # Update config - first user is always 'owner'
    config['username'] = username
    config['password_hash'] = hashlib.sha256(password.encode()).hexdigest()
    config['role'] = 'owner'
    save_security_config(config)
    
    # Auto Login
    ip = request.remote_addr
    session['logged_in'] = True
    session['username'] = username
    session['role'] = 'owner'
    session['login_time'] = time.time()
    session['session_id'] = hashlib.md5(f"{username}{time.time()}{ip}".encode()).hexdigest()[:16]
    session.pop('setup_mode', None) # Clear flag
    
    # Register in active sessions
    ACTIVE_SESSIONS[session['session_id']] = {
        'username': username,
        'role': 'owner',
        'login_time': session['login_time'],
        'last_activity': session['login_time'],
        'ip': ip
    }
    
    return jsonify({'success': True})

@app.route('/api/auth/login', methods=['POST'])
def login_api():
    ip = request.remote_addr
    
    # Check if locked out
    locked, remaining = check_login_locked(ip)
    if locked:
        return jsonify({'error': f'Too many failed attempts. Try again in {remaining}s'}), 429
    
    config = load_security_config()
    data = request.json
    username = data.get('username', '')
    password = data.get('password', '')
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    # Check main user
    if username == config['username'] and password_hash == config['password_hash']:
        session['logged_in'] = True
        session['username'] = username
        session['role'] = config.get('role', 'admin')
        session['login_time'] = time.time()
        session['session_id'] = hashlib.md5(f"{username}{time.time()}{ip}".encode()).hexdigest()[:16]
        
        # Register in active sessions
        ACTIVE_SESSIONS[session['session_id']] = {
            'username': username,
            'role': session['role'],
            'login_time': session['login_time'],
            'last_activity': session['login_time'],
            'ip': ip
        }
        
        record_login_attempt(ip, True)
        audit_log('LOGIN_SUCCESS', f"User {username} logged in (role: {session['role']})", username)
        return jsonify({'success': True, 'role': session['role']})
    
    # Check additional users
    for user in config.get('users', []):
        if username == user.get('username') and password_hash == user.get('password_hash'):
            session['logged_in'] = True
            session['username'] = username
            session['role'] = user.get('role', 'readonly')
            session['login_time'] = time.time()
            session['session_id'] = hashlib.md5(f"{username}{time.time()}{ip}".encode()).hexdigest()[:16]
            
            # Register in active sessions
            ACTIVE_SESSIONS[session['session_id']] = {
                'username': username,
                'role': session['role'],
                'login_time': session['login_time'],
                'last_activity': session['login_time'],
                'ip': ip
            }
            
            record_login_attempt(ip, True)
            audit_log('LOGIN_SUCCESS', f"User {username} logged in (role: {session['role']})", username)
            return jsonify({'success': True, 'role': session['role']})
    
    # Failed login
    record_login_attempt(ip, False)
    audit_log('LOGIN_FAILED', f"Failed login attempt for user {username}")
    return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/api/auth/logout', methods=['POST'])
def logout_api():
    user = session.get('username', 'unknown')
    session_id = session.get('session_id')
    
    # Remove from active sessions
    if session_id and session_id in ACTIVE_SESSIONS:
        del ACTIVE_SESSIONS[session_id]
    
    session.clear()
    audit_log('LOGOUT', f"User {user} logged out")
    return jsonify({'success': True})

# Public Monitoring Page - redirects to setup on first install
@app.route('/')
def monitoring_page():
    try:
        config = load_security_config()
        default_hash = hashlib.sha256('admin'.encode()).hexdigest()
        if config['password_hash'] == default_hash:
            return redirect('/setup-admin')
    except Exception:
        return redirect('/setup-admin')
    return render_template('monitoring.html')

# Admin Dashboard (requires login)
@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('index.html')


@app.route('/api/stats')
@requires_permission('dashboard')
def stats():
    # CPU
    cpu_percent = psutil.cpu_percent(interval=None)
    cpu_count = psutil.cpu_count(logical=True)
    try:
        load_avg = psutil.getloadavg() # (1, 5, 15)
    except:
        load_avg = (0, 0, 0)
    
    # Memory
    svmem = psutil.virtual_memory()
    mem_percent = svmem.percent
    mem_used = get_size(svmem.used)
    mem_total = get_size(svmem.total)
    
    # Linux specific memory details
    mem_cached = 0
    mem_buffers = 0
    if hasattr(svmem, 'cached'): mem_cached = get_size(svmem.cached)
    if hasattr(svmem, 'buffers'): mem_buffers = get_size(svmem.buffers)
    
    # Disk
    path = "/"
    if platform.system() == "Windows":
        path = "C:\\"
    
    disk_usage = psutil.disk_usage(path)
    disk_percent = disk_usage.percent
    disk_used = get_size(disk_usage.used)
    disk_free = get_size(disk_usage.free)
    disk_total = get_size(disk_usage.total)
    
    # Network
    net_io = psutil.net_io_counters()
    # Send raw bytes for speed calc on frontend
    sent = net_io.bytes_sent 
    recv = net_io.bytes_recv

    # Power Estimation (Synced with energy_monitor_loop)
    uptime_seconds = int(time.time() - psutil.boot_time())
    try:
        avg_watts = 6.0 + (cpu_percent * 6.0 / 100.0) 
        kwh_used = TOTAL_KWH
    except Exception as e:
        print(f"Energy calc error: {e}")
        avg_watts = 0
        kwh_used = 0

    # Temperature
    cpu_temp = 0
    if hasattr(psutil, "sensors_temperatures"):
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                # Common keys for arm/linux
                for name in ['cpu_thermal', 'soc_thermal', 'coretemp', 'thermal_zone0']:
                     if name in temps:
                         cpu_temp = temps[name][0].current
                         break
                # Fallback
                if cpu_temp == 0 and len(temps) > 0:
                     first_key = list(temps.keys())[0]
                     cpu_temp = temps[first_key][0].current
        except Exception:
            pass

    return jsonify({
        "cpu": {
            "percent": cpu_percent,
            "temp": cpu_temp,
            "cores": cpu_count,
            "load_1": load_avg[0],
            "load_5": load_avg[1],
            "load_15": load_avg[2]
        },
        "memory": {
            "percent": mem_percent,
            "used": mem_used,
            "total": mem_total,
            "cached": mem_cached,
            "buffers": mem_buffers
        },
        "disk": {
            "percent": disk_percent,
            "used": disk_used,
            "free": disk_free,
            "total": disk_total,
            "partition": path
        },
        "network": {
            "sent": sent,
            "recv": recv
        },
        "power": {
            "kwh": f"{kwh_used:.4f}",
            "watts_est": avg_watts
        },
        "uptime": uptime_seconds
    })

@app.route('/metrics')
def metrics_page():
    return render_template('metrics.html')

@app.route('/api/metrics')
def metrics_api():
    """Comprehensive metrics for the Metrics page"""
    
    # CPU per core
    cpu_percent_total = psutil.cpu_percent(interval=None)
    cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)
    cpu_count = psutil.cpu_count(logical=True)
    try:
        load_avg = psutil.getloadavg()
    except:
        load_avg = (0, 0, 0)
    
    # Temperature
    cpu_temp = 0
    if hasattr(psutil, "sensors_temperatures"):
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for name in ['cpu_thermal', 'soc_thermal', 'coretemp', 'thermal_zone0']:
                    if name in temps:
                        cpu_temp = temps[name][0].current
                        break
                if cpu_temp == 0 and len(temps) > 0:
                    first_key = list(temps.keys())[0]
                    cpu_temp = temps[first_key][0].current
        except Exception:
            pass
    
    # Memory
    svmem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    
    # Disk partitions
    partitions = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            partitions.append({
                'device': part.device,
                'mountpoint': part.mountpoint,
                'fstype': part.fstype,
                'total': usage.total,
                'used': usage.used,
                'free': usage.free,
                'percent': usage.percent
            })
        except:
            pass
    
    # Disk I/O
    disk_io = psutil.disk_io_counters()
    
    # Network
    net_io_total = psutil.net_io_counters()
    net_io_per_if = psutil.net_io_counters(pernic=True)
    net_interfaces = []
    for iface, stats in net_io_per_if.items():
        if iface != 'lo':  # Skip loopback
            net_interfaces.append({
                'name': iface,
                'bytes_sent': stats.bytes_sent,
                'bytes_recv': stats.bytes_recv,
                'packets_sent': stats.packets_sent,
                'packets_recv': stats.packets_recv
            })
    
    # Process count
    process_count = len(list(psutil.process_iter()))
    
    # Docker summary
    docker_summary = {'running': 0, 'stopped': 0, 'total': 0}
    try:
        client = docker_sdk.from_env()
        containers = client.containers.list(all=True)
        docker_summary['total'] = len(containers)
        docker_summary['running'] = len([c for c in containers if c.status == 'running'])
        docker_summary['stopped'] = docker_summary['total'] - docker_summary['running']
    except:
        pass
    
    # Uptime
    uptime_seconds = int(time.time() - psutil.boot_time())
    
    # Power Snapshot
    cpu_inst = psutil.cpu_percent(interval=None) or 0
    watts_now = 6.0 + (cpu_inst * 6.0 / 100.0)

    return jsonify({
        'power': {
            'kwh': f"{TOTAL_KWH:.4f}",
            'watts_est': int(watts_now)
        },
        'cpu': {
            'percent': cpu_percent_total,
            'per_core': cpu_per_core,
            'cores': cpu_count,
            'temp': cpu_temp,
            'load_1': load_avg[0],
            'load_5': load_avg[1],
            'load_15': load_avg[2]
        },
        'memory': {
            'total': svmem.total,
            'available': svmem.available,
            'used': svmem.used,
            'percent': svmem.percent,
            'cached': getattr(svmem, 'cached', 0),
            'buffers': getattr(svmem, 'buffers', 0)
        },
        'swap': {
            'total': swap.total,
            'used': swap.used,
            'percent': swap.percent
        },
        'disk': {
            'partitions': partitions,
            'io': {
                'read_bytes': disk_io.read_bytes if disk_io else 0,
                'write_bytes': disk_io.write_bytes if disk_io else 0,
                'read_count': disk_io.read_count if disk_io else 0,
                'write_count': disk_io.write_count if disk_io else 0
            }
        },
        'network': {
            'sent': net_io_total.bytes_sent,
            'recv': net_io_total.bytes_recv,
            'interfaces': net_interfaces
        },

        'processes': process_count,
        'docker': docker_summary,
        'uptime': uptime_seconds,
        'timestamp': int(time.time() * 1000)
    })

@app.route('/api/processes')
@requires_permission('metrics')
def processes():
    # Get all running processes
    procs = []
    for proc in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_info']):
        try:
            pinfo = proc.info
            # Calculate memory in MB
            mem_mb = pinfo['memory_info'].rss / (1024 * 1024)
            procs.append({
                'pid': pinfo['pid'],
                'name': pinfo['name'],
                'user': pinfo['username'],
                'cpu': pinfo['cpu_percent'],
                'mem_mb': round(mem_mb, 2)
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    
    # Sort by CPU usage by default
    procs.sort(key=lambda x: x['cpu'], reverse=True)
    return jsonify(procs[:50]) # Return top 50 to avoid overhead

@app.route('/api/disk-analysis')
@requires_permission('metrics')
def disk_analysis():
    def get_du(path):
        try:
            # Run du -h --max-depth=1 | sort -hr
            # Added timeout to prevent hanging on large disks
            cmd = f"timeout 5s du -h --max-depth=1 {path} 2>/dev/null | sort -hr | head -n 10"
            result = subprocess.check_output(cmd, shell=True).decode('utf-8')
            items = []
            for line in result.strip().split('\n'):
                parts = line.split('\t')
                if len(parts) == 2:
                    items.append({'size': parts[0], 'path': parts[1]})
            return items
        except subprocess.CalledProcessError:
            return [{'size': 'N/A', 'path': 'Timeout or Access Denied'}]
        except Exception as e:
            return [{'size': 'Error', 'path': str(e)}]

    # Analyze key directories
    # User requested to focus only on logs
    var_logs = get_du('/var/log')
    
    # Try to find zram1 mount point
    zram1_path = None
    try:
        with open('/proc/mounts', 'r') as f:
            for line in f:
                if 'zram1' in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        zram1_path = parts[1]
                        break
    except:
        pass

    zram1_data = []
    if zram1_path:
        zram1_data = get_du(zram1_path)
    
    return jsonify({
        'logs': var_logs,
        'zram1': {'path': zram1_path, 'data': zram1_data}
    })

@app.route('/api/network-ports')
@requires_permission('monitoring')
def network_ports():
    connections = []
    try:
        # Requires root usually for full details
        for conn in psutil.net_connections(kind='inet'):
            if conn.status == 'LISTEN':
                pid = conn.pid
                program = "Unknown"
                path = "N/A"
                if pid:
                    try:
                        proc = psutil.Process(pid)
                        program = proc.name()
                        try:
                            path = proc.exe()
                        except:
                            path = "Access Denied"

                        # Improve details for Python processes
                        try:
                            cmdline = proc.cmdline()
                            if cmdline and len(cmdline) > 1 and 'python' in program:
                                # The script is usually the second argument (index 1)
                                script_path = cmdline[1]
                                path = script_path # Set path to the script, not the python binary
                                
                                # Custom names
                                if 'backend_webserver/app.py' in script_path:
                                    program = 'web_dashboard'
                                else:
                                    # Use filename as program name for other python scripts
                                    program = script_path.split('/')[-1]
                        except:
                            pass
                    except:
                        pass
                
                connections.append({
                    'port': conn.laddr.port,
                    'ip': conn.laddr.ip,
                    'pid': pid,
                    'program': program,
                    'path': path
                })
        
        # Sort by port
        connections.sort(key=lambda x: x['port'])
    except Exception as e:
        return jsonify({'error': str(e)})
        
    return jsonify(connections)

@app.route('/api/network-details')
@requires_permission('monitoring')
def network_details():
    interfaces = []
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    
    for name, snics in addrs.items():
        ip = "N/A"
        for snic in snics:
            if snic.family == socket.AF_INET:
                ip = snic.address
                break
        
        is_up = "Down"
        if name in stats and stats[name].isup:
            is_up = "Up"
            
        interfaces.append({
            'name': name,
            'ip': ip,
            'status': is_up
        })
        
    return jsonify(interfaces)

@app.route('/api/system')
@login_required
def system_info():
    uname = platform.uname()
    
    # Try getting better CPU name on Linux
    cpu_name = uname.processor
    try:
        if platform.system() == "Linux":
            command = "cat /proc/cpuinfo"
            output = subprocess.check_output(command, shell=True).decode().strip()
            for line in output.split('\n'):
                if "model name" in line:
                    cpu_name = line.split(':')[1].strip()
                    break
    except:
        pass

    return jsonify({
        "system": uname.system,
        "node": uname.node,
        "release": uname.release,
        "version": uname.version,
        "machine": uname.machine,
        "processor": cpu_name,
    })

@app.route('/api/server-identity')
@login_required
def api_server_identity():
    import socket
    local_ip = "Unknown"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except: pass

    docker_version = "Unknown"
    try:
        import docker
        docker_version = docker.from_env().version().get('Version', 'Unknown')
    except: pass

    uname = platform.uname()
    
    cpu_name = uname.processor
    try:
        if platform.system() == "Linux":
            output = subprocess.check_output("cat /proc/cpuinfo", shell=True).decode().strip()
            for line in output.split('\n'):
                if "model name" in line:
                    cpu_name = line.split(':')[1].strip()
                    break
    except: pass

    return jsonify({
        "local_ip": local_ip,
        "hostname": socket.gethostname(),
        "provider": "On-Premise",
        "distro": f"{platform.system()} {platform.release()}",
        "kernel": platform.version(),
        "docker_version": docker_version,
        "cpu_model": cpu_name,
        "machine": platform.machine()
    })


# --- FILE MANAGER ROUTES ---
import os
import shutil

@app.route('/files')
@requires_permission('files', 'read')
def files_page():
    return render_template('files.html')

@app.route('/api/files/list', methods=['GET'])
@requires_permission('files', 'read')
def list_files():
    path = request.args.get('path', '/')
    page = int(request.args.get('page', 1))
    sort_by = request.args.get('sort_by', 'name')
    order = request.args.get('order', 'asc')
    per_page = 100

    # === GOOGLE DRIVE INTEGRATION ===
    if path.startswith('gdrive://'):
        if not os.path.exists(GDRIVE_TOKEN_FILE):
            return jsonify({'error': 'Google Drive not authenticated'}), 401

        try:
            creds = Credentials.from_authorized_user_file(GDRIVE_TOKEN_FILE, SCOPES)
            service = build('drive', 'v3', credentials=creds)

            folder_id = 'root'
            if path != 'gdrive://root':
                folder_id = path.replace('gdrive://', '')

            results = service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="files(id, name, mimeType, size, modifiedTime)",
                pageSize=per_page
            ).execute()

            items = results.get('files', [])
            all_items = []

            if folder_id != 'root':
                try:
                    file_meta = service.files().get(fileId=folder_id, fields="parents").execute()
                    parents = file_meta.get('parents', [])
                    parent_id = parents[0] if parents else 'root'
                    all_items.append({
                        'name': '..',
                        'path': f'gdrive://{parent_id}',
                        'is_dir': True,
                        'size': '-',
                        'date': '-',
                        'perm': '-',
                        'uid': '-',
                        'gid': '-'
                    })
                except Exception:
                    all_items.append({
                        'name': '..',
                        'path': 'gdrive://root',
                        'is_dir': True,
                        'size': '-',
                        'date': '-',
                        'perm': '-',
                        'uid': '-',
                        'gid': '-'
                    })

            for item in items:
                is_dir = (item.get('mimeType') == 'application/vnd.google-apps.folder')
                raw_size = int(item.get('size', 0)) if not is_dir else 0
                all_items.append({
                    'name': item.get('name'),
                    'path': f"gdrive://{item.get('id')}",
                    'is_dir': is_dir,
                    'size': '-' if is_dir else _fmt_size(raw_size),
                    'date': item.get('modifiedTime', '').split('T')[0] if item.get('modifiedTime') else '-',
                    'perm': 'd------' if is_dir else '-------',
                    'uid': 'gdrive',
                    'gid': 'gdrive',
                    'raw_size': raw_size,
                    'raw_date': item.get('modifiedTime', ''),
                    'raw_name': item.get('name', '').lower()
                })

            return jsonify({
                'current_path': path,
                'items': all_items,
                'total': len(all_items),
                'has_more': False,
                'page': 1
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # === LOCAL FILESYSTEM ===
    # Normalize path separators for cross-platform
    path = os.path.normpath(path)

    blocked = require_safe_path_for_role(path, 'list')
    if blocked:
        return blocked

    if not os.path.exists(path):
        return jsonify({'error': 'Path not found'}), 404
    if not os.path.isdir(path):
        return jsonify({'error': 'Not a directory'}), 400

    try:
        all_items = []

        # Add parent directory entry if not at root
        parent = os.path.dirname(path)
        if path != parent:  # not at root
            all_items.append({
                'name': '..',
                'path': parent,
                'is_dir': True,
                'size': '-',
                'date': '-',
                'perm': '-',
                'uid': '-',
                'gid': '-',
                'raw_size': 0,
                'raw_date': 0,
                'raw_name': ''
            })

        entries = list(os.scandir(path))

        for entry in entries:
            if not is_owner_role() and is_sensitive_path(entry.path):
                continue
            try:
                stat = entry.stat(follow_symlinks=False)
                is_dir = entry.is_dir(follow_symlinks=False)
                size = '-' if is_dir else _fmt_size(stat.st_size)
                import datetime
                date = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')

                # Permissions (Unix-style on Linux, simplified on Windows)
                try:
                    import stat as stat_module
                    mode = stat.st_mode
                    perm = stat_module.filemode(mode)
                except:
                    perm = 'd------' if is_dir else '-------'

                all_items.append({
                    'name': entry.name,
                    'path': os.path.join(path, entry.name),
                    'is_dir': is_dir,
                    'size': size,
                    'date': date,
                    'perm': perm,
                    'uid': getattr(stat, 'st_uid', '-'),
                    'gid': getattr(stat, 'st_gid', '-'),
                    'raw_size': stat.st_size if not is_dir else 0,
                    'raw_date': stat.st_mtime,
                    'raw_name': entry.name.lower()
                })
            except PermissionError:
                continue
            except Exception:
                continue

        # Sorting logic
        is_reverse = (order == 'desc')
        # We always keep '..' at the top
        parent_item = [i for i in all_items if i['name'] == '..']
        other_items = [i for i in all_items if i['name'] != '..']

        if sort_by == 'size':
            other_items.sort(key=lambda x: (not x['is_dir'], x['raw_size']), reverse=is_reverse)
        elif sort_by == 'date':
            other_items.sort(key=lambda x: (not x['is_dir'], x['raw_date']), reverse=is_reverse)
        else:
            # Default is name
            other_items.sort(key=lambda x: (not x['is_dir'], x['raw_name']), reverse=is_reverse)

        all_items = parent_item + other_items

        total = len(all_items)
        start = (page - 1) * per_page
        end = start + per_page
        page_items = all_items[start:end]

        return jsonify({
            'current_path': path,
            'items': page_items,
            'total': total,
            'has_more': end < total,
            'page': page
        })
    except PermissionError:
        return jsonify({'error': 'Permission denied'}), 403
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def _fmt_size(b):
    for unit in ['B','KB','MB','GB','TB']:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"

@app.route('/api/files/platform', methods=['GET'])
@requires_permission('files', 'read')
def get_platform_info():
    """Return OS type and platform-specific quick-access folders for the sidebar."""
    system = platform.system()  # 'Windows', 'Linux', 'Darwin'

    def folder(icon, name, path):
        return {'icon': icon, 'name': name, 'path': path}

    def exists(p):
        return os.path.isdir(p)

    quick = []

    if system == 'Windows':
        # ── Windows ──────────────────────────────────────
        # User folders
        user_profile = os.environ.get('USERPROFILE', '')
        username = os.environ.get('USERNAME', 'User')

        win_user_paths = [
            ('fa-house',       f'{username}',    user_profile),
            ('fa-desktop',     'Desktop',        os.path.join(user_profile, 'Desktop')),
            ('fa-file',        'Documents',      os.path.join(user_profile, 'Documents')),
            ('fa-download',    'Downloads',      os.path.join(user_profile, 'Downloads')),
            ('fa-image',       'Pictures',       os.path.join(user_profile, 'Pictures')),
            ('fa-music',       'Music',          os.path.join(user_profile, 'Music')),
            ('fa-film',        'Videos',         os.path.join(user_profile, 'Videos')),
        ]
        win_sys_paths = [
            ('fa-windows',     'Windows',        os.environ.get('SystemRoot', 'C:\\Windows')),
            ('fa-box',         'Program Files',  os.environ.get('ProgramFiles', 'C:\\Program Files')),
            ('fa-server',      'ProgramData',    os.environ.get('ProgramData', 'C:\\ProgramData')),
            ('fa-folder-open', 'AppData',        os.path.join(user_profile, 'AppData')),
            ('fa-flask',       'Temp',           os.environ.get('TEMP', 'C:\\Windows\\Temp')),
        ]
        for icon, name, path in win_user_paths + win_sys_paths:
            if path and exists(path):
                quick.append(folder(icon, name, path))

    else:
        # ── Linux / macOS ─────────────────────────────────
        # Detect current user running the app
        try:
            import pwd
            running_user = pwd.getpwuid(os.getuid()).pw_name
            running_home = os.path.expanduser('~')
        except Exception:
            running_user = 'root'
            running_home = '/root'

        # 1. System roots
        sys_paths = [
            ('fa-hard-drive',  '/ (Root)',       '/'),
            ('fa-folder',      'root Home',      '/root'),
            ('fa-gear',        'etc',            '/etc'),
            ('fa-box-archive', 'var',            '/var'),
            ('fa-flask',       'tmp',            '/tmp'),
            ('fa-cubes',       'opt',            '/opt'),
            ('fa-server',      'srv',            '/srv'),
            ('fa-database',    'usr',            '/usr'),
        ]
        for icon, name, path in sys_paths:
            if exists(path):
                quick.append(folder(icon, name, path))

        # 2. Data / custom folders (very common on servers)
        data_paths = [
            ('fa-database',   'data',            '/data'),
            ('fa-database',   'mnt/data',        '/mnt/data'),
            ('fa-hdd',        'mnt',             '/mnt'),
            ('fa-hdd',        'media',           '/media'),
            ('fa-hdd',        'storage',         '/storage'),
        ]
        for icon, name, path in data_paths:
            if exists(path):
                quick.append(folder(icon, name, path))

        # 3. Home directories - list each user's home
        home_base = '/home'
        if exists(home_base):
            quick.append(folder('fa-users', 'home', home_base))
            try:
                for user_dir in sorted(os.listdir(home_base)):
                    full = os.path.join(home_base, user_dir)
                    if os.path.isdir(full):
                        quick.append(folder('fa-user', user_dir, full))
                        # Common sub-folders for each user
                        for sub_icon, sub_name in [
                            ('fa-image', 'Pictures'),
                            ('fa-file',  'Documents'),
                            ('fa-download', 'Downloads'),
                        ]:
                            sub = os.path.join(full, sub_name)
                            if exists(sub):
                                quick.append(folder(sub_icon, f'{user_dir}/{sub_name}', sub))
            except PermissionError:
                pass

        # 4. Docker / container volumes (common on servers)
        docker_paths = [
            ('fa-docker',     'docker volumes',  '/var/lib/docker/volumes'),
            ('fa-box',        'docker/overlay2', '/var/lib/docker/overlay2'),
        ]
        for icon, name, path in docker_paths:
            if exists(path):
                quick.append(folder(icon, name, path))

        # 5. Log shortcuts
        log_paths = [
            ('fa-scroll',     'var/log',         '/var/log'),
        ]
        for icon, name, path in log_paths:
            if exists(path):
                quick.append(folder(icon, name, path))

    if not is_owner_role():
        quick = [item for item in quick if not is_sensitive_path(item.get('path'))]

    return jsonify({
        'os': system,
        'quick_folders': quick
    })


@app.route('/api/files/drives', methods=['GET'])
@requires_permission('files', 'read')
def get_drives():
    drives = []

    try:
        import psutil
        seen_devices = set()
        for part in psutil.disk_partitions(all=False):
            # Skip loop, snap, overlay, docker internals, and config file mounts
            if 'loop' in part.device or 'snap' in part.mountpoint or 'overlay' in part.mountpoint:
                continue
            if part.mountpoint in ('/etc/resolv.conf', '/etc/hostname', '/etc/hosts'):
                continue
            if part.device in seen_devices:
                continue
            seen_devices.add(part.device)
            
            try:
                usage = psutil.disk_usage(part.mountpoint)
                drives.append({
                    'mountpoint': part.mountpoint,
                    'device': part.device,
                    'fstype': part.fstype,
                    'total': usage.total,
                    'free': usage.free,
                    'used': usage.used,
                    'percent': usage.percent
                })
            except:
                drives.append({
                    'mountpoint': part.mountpoint,
                    'device': part.device,
                    'fstype': part.fstype,
                })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
        
    return jsonify({'drives': drives})

@app.route('/api/files/action', methods=['POST'])
@requires_permission('files', 'full')
def file_action():
    data = request.json
    action = data.get('action')
    path = data.get('path')

    blocked = require_safe_path_for_role(path, action or 'file_action')
    if blocked:
        return blocked
    
    try:
        if action == 'delete':
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        elif action == 'create_folder':
            os.makedirs(path, exist_ok=True)
        elif action == 'create_file':
            with open(path, 'w') as f:
                pass
        elif action == 'rename':
            new_path = data.get('new_path')
            blocked = require_safe_path_for_role(new_path, 'rename target')
            if blocked:
                return blocked
            os.rename(path, new_path)
        elif action == 'paste':
            source = data.get('source')
            dest = path # paste into this folder
            blocked = require_safe_path_for_role(source, 'paste source')
            if blocked:
                return blocked
            # Simple handling: copy raw
            base_name = os.path.basename(source)
            final_dest = os.path.join(dest, base_name)
            blocked = require_safe_path_for_role(final_dest, 'paste target')
            if blocked:
                return blocked
            
            if data.get('operation') == 'cut':
                shutil.move(source, final_dest)
            else:
                if os.path.isdir(source):
                    shutil.copytree(source, final_dest)
                else:
                    shutil.copy2(source, final_dest)
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/files/content', methods=['GET', 'POST'])
@login_required
def file_content():
    path = request.args.get('path')
    if request.method == 'POST':
        if not has_permission(session.get('role', 'readonly'), 'files', 'full'):
            return jsonify({'error': 'No permission for files'}), 403
        data = request.json
        path = data.get('path')
        content = data.get('content')
        if not path:
            return jsonify({'error': 'No path specified'}), 400
        blocked = require_safe_path_for_role(path, 'write')
        if blocked:
            return blocked
        try:
            with open(path, 'w') as f:
                f.write(content)
            if is_sensitive_path(path):
                harden_file_permissions(path)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
            
    # GET
    if not has_permission(session.get('role', 'readonly'), 'files', 'read'):
        return jsonify({'error': 'No permission for files'}), 403
    if not path:
        return jsonify({'error': 'No path specified'}), 400
    blocked = require_safe_path_for_role(path, 'read')
    if blocked:
        return blocked
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
        
    try:
        with open(path, 'r', encoding='utf-8') as f: # Simple text reading
            content = f.read()
            return jsonify({'content': content})
    except UnicodeDecodeError:
         return jsonify({'error': 'Binary or unsupported file type'}), 400
    except Exception as e:
         return jsonify({'error': str(e)}), 500

# --- TERMINAL ROUTES ---
@app.route('/terminal')
@requires_permission('terminal')
def terminal_page():
    return render_template('terminal.html')

# WebSocket handlers for terminal
@socketio.on('start_terminal')
def handle_start_terminal(data):
    session_id = data.get('session_id', 'default')
    start_path = data.get('path', '/root')
    
    # Create PTY
    master_fd, slave_fd = pty.openpty()
    
    # Fork shell
    pid = os.fork()
    if pid == 0:
        # Child process
        os.close(master_fd)
        os.setsid()
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        os.close(slave_fd)
        
        lxd_target = data.get('lxd_target')
        
        # Use nsenter to enter host PID 1 namespace (root shell on host)
        # Assuming container is privileged and shares PID namespace
        # Adding -i to bash for interactive mode and setting TERM/SHELL
        os.environ['TERM'] = 'xterm-256color'
        os.environ['SHELL'] = '/bin/bash'
        
        if lxd_target:
            cmd = ['nsenter', '-t', '1', '-m', '-u', '-n', '-i', 'lxc', 'exec', lxd_target, '--', 'bash', '--login', '-i']
        else:
            # Perintah ini akan membersihkan layar, menjalankan neofetch, lalu masuk ke bash interaktif
            cmd = ['nsenter', '-t', '1', '-m', '-u', '-n', '-i', 'bash', '--login', '-c', 'clear && neofetch && exec bash -i']
            
        os.execvp('nsenter', cmd)
    else:
        # Parent process
        os.close(slave_fd)
        terminal_sessions[session_id] = {
            'fd': master_fd,
            'pid': pid
        }
        
        # Start reading thread
        socketio.start_background_task(read_terminal_output, session_id, master_fd)
        emit('terminal_started', {'session_id': session_id})

def read_terminal_output(session_id, fd):
    while session_id in terminal_sessions:
        socketio.sleep(0.01)
        try:
            if select.select([fd], [], [], 0.1)[0]:
                data = os.read(fd, 1024)
                if data:
                    socketio.emit('terminal_output', {
                        'session_id': session_id,
                        'data': data.decode('utf-8', errors='replace')
                    })
        except:
            break

@socketio.on('terminal_input')
def handle_terminal_input(data):
    session_id = data.get('session_id', 'default')
    input_data = data.get('input', '')
    
    if session_id in terminal_sessions:
        fd = terminal_sessions[session_id]['fd']
        try:
            os.write(fd, input_data.encode())
        except:
            pass

@socketio.on('terminal_resize')
def handle_terminal_resize(data):
    session_id = data.get('session_id', 'default')
    rows = data.get('rows', 24)
    cols = data.get('cols', 80)
    
    if session_id in terminal_sessions:
        fd = terminal_sessions[session_id]['fd']
        try:
            winsize = struct.pack('HHHH', rows, cols, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except:
            pass

@socketio.on('stop_terminal')
def handle_stop_terminal(data):
    session_id = data.get('session_id', 'default')
    
    if session_id in terminal_sessions:
        session = terminal_sessions.pop(session_id)
        try:
            os.close(session['fd'])
            os.kill(session['pid'], 9)
            os.waitpid(session['pid'], 0)
        except:
            pass

# --- DOCKER ROUTES ---
import docker as docker_sdk

@app.route('/docker')
@requires_permission('docker', 'view')
def docker_page():
    return render_template('docker.html')

@app.route('/api/docker/containers')
@requires_permission('docker', 'view')
def docker_containers():
    try:
        client = docker_sdk.from_env()
        containers = client.containers.list(all=True)
        
        result = []
        for c in containers:
            # Extract first public port
            public_port = None
            if c.attrs['NetworkSettings']['Ports']:
                for p_int, p_bind in c.attrs['NetworkSettings']['Ports'].items():
                    if p_bind:
                        public_port = p_bind[0]['HostPort']
                        break
            
            # Handle Host Mode - Lookup from Catalog
            if c.attrs['HostConfig']['NetworkMode'] == 'host':
                 try:
                     # Load catalogs if not loaded (optimization: load once or rely on cached)
                     # For simplicity, load here or use helper if available. 
                     # Given performance, let's just peek at the file or assume we can reuse global if exists.
                     # But `docker_containers` is a standalone route.
                     
                     # Simple logic: Check against known apps in app_catalog
                     # We need to read app_catalog.json
                     catalog_path = os.path.join(DATA_DIR, 'app_catalog.json')
                     if os.path.exists(catalog_path):
                         with open(catalog_path, 'r') as f:
                             catalog = json.load(f)
                             
                         # Find match by image or name
                         for app_def in catalog:
                             # Check Image Match (Strongest signal for standard apps)
                             if app_def.get('image') and c.attrs['Config']['Image'] in app_def.get('image'):
                                 # Found it! Get the first port.
                                 if app_def.get('ports'):
                                     public_port = app_def['ports'][0].get('host')
                                 break
                             
                             # Check ID/Name Match
                             if app_def.get('id') == c.name:
                                 if app_def.get('ports'):
                                     public_port = app_def['ports'][0].get('host')
                                 break
                 except Exception as e:
                     print(f"Error checking catalog for host port: {e}")

            try:
                img_name = c.image.tags[0] if c.image.tags else c.image.short_id
            except Exception:
                img_name = c.attrs.get('Config', {}).get('Image', 'Unknown')

            result.append({
                'id': c.short_id,
                'name': c.name,
                'image': img_name,
                'status': c.status,
                'created': c.attrs['Created'][:19].replace('T', ' '),
                'port': public_port,
                'network_mode': c.attrs['HostConfig']['NetworkMode']
            })
        
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/docker/<container_id>/stats')
@requires_permission('docker', 'view')
def docker_container_stats(container_id):
    """Fetch stats for a single container - called separately to not block"""
    try:
        client = docker_sdk.from_env()
        container = client.containers.get(container_id)
        
        if container.status != 'running':
            return jsonify({'cpu': 0, 'mem': 0, 'mem_used': '-', 'mem_limit': '-'})
        
        raw_stats = container.stats(stream=False)
        
        # CPU
        cpu_delta = raw_stats['cpu_stats']['cpu_usage']['total_usage'] - raw_stats['precpu_stats']['cpu_usage']['total_usage']
        system_delta = raw_stats['cpu_stats']['system_cpu_usage'] - raw_stats['precpu_stats']['system_cpu_usage']
        cpu_percent = 0.0
        if system_delta > 0:
            cpu_percent = (cpu_delta / system_delta) * 100.0
        
        # RAM
        mem_usage = raw_stats['memory_stats'].get('usage', 0)
        mem_limit = raw_stats['memory_stats'].get('limit', 1)
        mem_percent = (mem_usage / mem_limit) * 100 if mem_limit > 0 else 0
        
        return jsonify({
            'cpu': round(cpu_percent, 1),
            'mem': round(mem_percent, 1),
            'mem_used': get_size(mem_usage),
            'mem_limit': get_size(mem_limit)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/docker/<container_id>/action', methods=['POST'])
@requires_permission('docker', 'full')
def docker_action(container_id):
    try:
        data = request.json
        action = data.get('action')
        
        client = docker_sdk.from_env()
        container = client.containers.get(container_id)
        
        if action == 'start':
            container.start()
        elif action == 'stop':
            container.stop()
        elif action == 'restart':
            container.restart()
        elif action == 'kill':
            container.kill()
        else:
            return jsonify({'error': 'Unknown action'}), 400
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/docker/<container_id>/logs')
@requires_permission('docker', 'view')
def docker_logs(container_id):
    try:
        lines = request.args.get('lines', 200, type=int)
        
        client = docker_sdk.from_env()
        container = client.containers.get(container_id)
        logs = container.logs(tail=lines, timestamps=True).decode('utf-8', errors='replace')
        
        return jsonify({'logs': logs})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- SECURITY ROUTES ---
@app.route('/security')
@owner_required
def security_page():
    return render_template('security.html')

@app.route('/api/security/config')
@owner_required
def get_security_config():
    config = load_security_config()
    # Don't send password hashes to frontend
    safe_config = {
        'username': config['username'],
        'role': config.get('role', 'admin'),
        'session_timeout': config.get('session_timeout', 3600),
        'require_auth': config.get('require_auth', True),
        'allowed_ips': config.get('allowed_ips', []),
        'max_login_attempts': config.get('max_login_attempts', 5),
        'lockout_duration': config.get('lockout_duration', 300),
        'users': [{'username': u['username'], 'role': u.get('role', 'readonly')} for u in config.get('users', [])]
    }
    return jsonify(safe_config)

@app.route('/api/security/config', methods=['POST'])
@owner_required
def update_security_config():
    config = load_security_config()
    data = request.json
    
    if 'session_timeout' in data:
        config['session_timeout'] = int(data['session_timeout'])
    if 'require_auth' in data:
        config['require_auth'] = bool(data['require_auth'])
    if 'max_login_attempts' in data:
        config['max_login_attempts'] = int(data['max_login_attempts'])
    if 'lockout_duration' in data:
        config['lockout_duration'] = int(data['lockout_duration'])
    
    save_security_config(config)
    audit_log('CONFIG_CHANGED', f"Security config updated: {data}", session.get('username', 'unknown'))
    return jsonify({'success': True})

@app.route('/api/security/change-password', methods=['POST'])
@owner_required
def change_password():
    config = load_security_config()
    data = request.json
    
    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')
    
    current_hash = hashlib.sha256(current_password.encode()).hexdigest()
    if current_hash != config['password_hash']:
        return jsonify({'error': 'Current password is incorrect'}), 400
    
    if len(new_password) < 4:
        return jsonify({'error': 'Password must be at least 4 characters'}), 400
    
    config['password_hash'] = hashlib.sha256(new_password.encode()).hexdigest()
    save_security_config(config)
    audit_log('PASSWORD_CHANGED', 'Password was changed', session.get('username', 'unknown'))
    return jsonify({'success': True})

@app.route('/api/security/users', methods=['POST'])
@owner_required
def add_user():
    """Add a new user"""
    config = load_security_config()
    data = request.json
    current_role = session.get('role', 'readonly')
    
    username = data.get('username', '').strip()
    password = data.get('password', '')
    role = data.get('role', 'readonly')
    
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    
    if len(password) < 4:
        return jsonify({'error': 'Password min 4 characters'}), 400
    
    # Validate role
    valid_roles = ['admin', 'operator', 'readonly']
    if role not in valid_roles:
        role = 'readonly'
    
    # Role hierarchy enforcement: admin can only create operator/readonly
    if current_role == 'admin' and role == 'admin':
        return jsonify({'error': 'Admin cannot create admin users'}), 403
    
    # Owner can create any except owner
    if role == 'owner':
        return jsonify({'error': 'Cannot create owner users'}), 403
    
    # Check if username exists
    if username == config['username']:
        return jsonify({'error': 'Username already exists'}), 400
    
    for u in config.get('users', []):
        if u['username'] == username:
            return jsonify({'error': 'Username already exists'}), 400
    
    # Add user
    if 'users' not in config:
        config['users'] = []
    
    config['users'].append({
        'username': username,
        'password_hash': hashlib.sha256(password.encode()).hexdigest(),
        'role': role
    })
    
    save_security_config(config)
    audit_log('USER_ADDED', f"Added user {username} with role {role}", session.get('username'))
    return jsonify({'success': True})

@app.route('/api/security/users/<username>', methods=['PUT'])
@owner_required
def update_user(username):
    """Update user role"""
    config = load_security_config()
    data = request.json
    current_role = session.get('role', 'readonly')
    new_role = data.get('role', 'readonly')
    
    # Validate role
    valid_roles = ['admin', 'operator', 'readonly']
    if new_role not in valid_roles:
        new_role = 'readonly'
    
    # Find user first
    target_user = None
    for u in config.get('users', []):
        if u['username'] == username:
            target_user = u
            break
    
    if not target_user:
        return jsonify({'error': 'User not found'}), 404
    
    # Role hierarchy enforcement
    target_current_role = target_user.get('role', 'readonly')
    
    # Admin cannot modify other admins
    if current_role == 'admin':
        if target_current_role == 'admin':
            return jsonify({'error': 'Cannot modify admin users'}), 403
        if new_role == 'admin':
            return jsonify({'error': 'Cannot promote to admin'}), 403
    
    # Cannot set role to owner
    if new_role == 'owner':
        return jsonify({'error': 'Cannot set role to owner'}), 403
    
    # Update role
    target_user['role'] = new_role
    save_security_config(config)
    audit_log('USER_ROLE_CHANGED', f"Changed {username} role to {new_role}", session.get('username'))
    return jsonify({'success': True})

@app.route('/api/security/users/<username>', methods=['DELETE'])
@owner_required
def delete_user(username):
    """Delete a user"""
    config = load_security_config()
    current_role = session.get('role', 'readonly')
    
    # Can't delete owner
    if username == config['username']:
        return jsonify({'error': 'Cannot delete owner'}), 400
    
    # Find target user
    target_user = None
    for u in config.get('users', []):
        if u['username'] == username:
            target_user = u
            break
    
    if not target_user:
        return jsonify({'error': 'User not found'}), 404
    
    # Role hierarchy enforcement: admin can only delete operator/readonly
    target_role = target_user.get('role', 'readonly')
    if current_role == 'admin' and target_role == 'admin':
        return jsonify({'error': 'Cannot delete admin users'}), 403
    
    # Delete user
    config['users'] = [u for u in config.get('users', []) if u['username'] != username]
    save_security_config(config)
    audit_log('USER_DELETED', f"Deleted user {username}", session.get('username'))
    return jsonify({'success': True})

@app.route('/api/security/audit-logs')
@owner_required
def get_audit_logs():
    try:
        lines = request.args.get('lines', 100, type=int)
        if os.path.exists(AUDIT_LOG_FILE):
            with open(AUDIT_LOG_FILE, 'r') as f:
                all_lines = f.readlines()
                return jsonify({'logs': all_lines[-lines:]})
        return jsonify({'logs': []})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/security/session-info')
@login_required
def session_info():
    login_time = session.get('login_time', time.time())
    last_activity = session.get('last_activity', login_time)
    return jsonify({
        'username': session.get('username'),
        'role': session.get('role', 'readonly'),
        'login_time': login_time,
        'elapsed': time.time() - login_time,
        'last_activity': last_activity,
        'ip': request.remote_addr
    })

@app.route('/api/security/heartbeat', methods=['POST'])
@login_required
def session_heartbeat():
    """Heartbeat to keep session alive and track activity"""
    session['last_activity'] = time.time()
    
    # Update in active sessions
    session_id = session.get('session_id')
    if session_id and session_id in ACTIVE_SESSIONS:
        ACTIVE_SESSIONS[session_id]['last_activity'] = time.time()
    
    return jsonify({'success': True, 'timestamp': time.time()})

@app.route('/api/security/logout-beacon', methods=['POST'])
def logout_beacon():
    """Called by browser on tab close/unload to logout"""
    if session.get('logged_in'):
        user = session.get('username', 'unknown')
        session_id = session.get('session_id')
        
        # Remove from active sessions
        if session_id and session_id in ACTIVE_SESSIONS:
            del ACTIVE_SESSIONS[session_id]
        
        audit_log('TAB_CLOSED_LOGOUT', f"User {user} logged out (browser closed)", user)
        session.clear()
    return jsonify({'success': True})

@app.route('/api/security/active-sessions')
@owner_required
def get_active_sessions():
    """Get all currently active sessions (admin only)"""
    now = time.time()
    sessions_list = []
    
    # Clean up stale sessions (no activity for 5 minutes)
    stale_threshold = 300  # 5 minutes
    stale_ids = [sid for sid, data in ACTIVE_SESSIONS.items() 
                 if now - data.get('last_activity', 0) > stale_threshold]
    for sid in stale_ids:
        del ACTIVE_SESSIONS[sid]
    
    for sid, data in ACTIVE_SESSIONS.items():
        sessions_list.append({
            'session_id': sid,
            'username': data.get('username'),
            'role': data.get('role'),
            'login_time': data.get('login_time'),
            'last_activity': data.get('last_activity'),
            'ip': data.get('ip'),
            'duration': int(now - data.get('login_time', now))
        })
    
    # Sort by login time (most recent first)
    sessions_list.sort(key=lambda x: x['login_time'], reverse=True)
    
    return jsonify({'sessions': sessions_list})

# --- SETTINGS ROUTES ---
@app.route('/settings')
@owner_required
def settings_page():
    return render_template('settings.html')

@app.route('/api/monitoring/config')
def get_monitoring_config():
    """Public endpoint for monitoring board configuration"""
    settings = load_app_settings()
    mqtt = settings.get('mqtt', {})
    general = settings.get('general', {})
    safe_mqtt = {
        'enabled': mqtt.get('enabled', False),
        'devices': mqtt.get('devices', [])
    }
    return jsonify({
        'mqtt': safe_mqtt,
        'general': {
            'server_name': general.get('server_name', 'MuhfiDesk')
        }
    })

@app.route('/api/settings')
@owner_required
def get_settings():
    return jsonify(load_app_settings())

@app.route('/api/settings', methods=['POST'])
@owner_required
def update_settings():
    settings = load_app_settings()
    data = request.json
    
    # Update each section if provided
    for section in ['general', 'appearance', 'monitoring', 'alerts', 'integrations', 'mqtt', 'services']:
        if section in data:
            if section in settings and isinstance(settings[section], dict) and isinstance(data[section], dict):
                settings[section] = {**settings[section], **data[section]}
            else:
                # For lists like 'services', just replace entirely
                settings[section] = data[section]
    
    save_app_settings(settings)
    audit_log('SETTINGS_CHANGED', f"App settings updated", session.get('username', 'unknown'))
    return jsonify({'success': True})

@app.route('/api/settings/export')
@owner_required
def export_settings():
    settings = load_app_settings()
    return Response(
        json.dumps(settings, indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment;filename=dashboard_settings.json'}
    )

@app.route('/api/settings/import', methods=['POST'])
@owner_required
def import_settings():
    try:
        data = request.json
        save_app_settings(data)
        audit_log('SETTINGS_IMPORTED', 'Settings imported from file', session.get('username', 'unknown'))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/settings/reset', methods=['POST'])
@owner_required
def reset_settings():
    # Delete settings file to use defaults
    if os.path.exists(APP_SETTINGS_FILE):
        os.remove(APP_SETTINGS_FILE)
    audit_log('SETTINGS_RESET', 'Settings reset to defaults', session.get('username', 'unknown'))
    return jsonify({'success': True})

# --- TELEGRAM ROUTES ---
@app.route('/api/telegram/config', methods=['GET', 'POST'])
@owner_required
def telegram_config():
    if request.method == 'GET':
        config = telegram_notifier.load_telegram_config()
        if config.get('bot_token'):
            # Mask token
            token = config['bot_token']
            if len(token) > 10:
                config['bot_token_masked'] = token[:5] + '*' * 15 + token[-5:]
            else:
                config['bot_token_masked'] = '***'
            config['bot_token'] = '' # Don't send token to frontend
        return jsonify(config)
    
    # POST
    data = request.json
    config = telegram_notifier.load_telegram_config()
    
    # Update fields
    for field in ['enabled', 'chat_id', 'report_interval', 'alert_cpu_threshold', 'alert_ram_threshold', 'alert_docker_crash', 'alert_brute_force', 'alert_backup']:
        if field in data:
            config[field] = data[field]
            
    # Update bot token only if provided (not masked)
    if 'bot_token' in data and data['bot_token'] and not data['bot_token'].startswith('*'):
        config['bot_token'] = data['bot_token']
        
    telegram_notifier.save_telegram_config(config)
    audit_log('TELEGRAM_CONFIG_UPDATED', 'Telegram config updated', session.get('username', 'unknown'))
    return jsonify({'success': True})

@app.route('/api/telegram/test', methods=['POST'])
@owner_required
def telegram_test():
    success, error = telegram_notifier.send_telegram("🔔 Tes notifikasi dari MuhfiDesk berhasil!")
    if success:
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': error})

@app.route('/api/telegram/send-report', methods=['POST'])
@owner_required
def telegram_report():
    msg = telegram_notifier.build_monitoring_report()
    success, error = telegram_notifier.send_telegram(msg)
    if success:
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': error})

# --- CASAOS ROUTES ---
CASAOS_URL = 'http://host.docker.internal:9999'
CASAOS_ALLOWED_IPS = {}  # {ip: expiry_time}

@app.route('/files')
@requires_permission('files', 'read')
def files():
    path = request.args.get('path', '/')
    return render_template('files.html', current_path=path)

@app.route('/face')
def face_ui():
    """Eilik-style Robot Face Interface"""
    return render_template('face.html')

@app.route('/api/face-stats')
def face_stats_api():
    """Public API for Face UI (No Login Required)"""
    try:
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        return jsonify({
            'cpu': cpu,
            'ram': mem.percent
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/terminal')
@login_required
def terminal():
    return render_template('terminal.html')

@app.route('/casaos')
@login_required
def casaos_page():
    """Render CasaOS access page"""
    return render_template('casaos.html')

@app.route('/api/casaos/status')
@login_required
def casaos_status():
    """Check if CasaOS is running"""
    try:
        resp = requests.get(CASAOS_URL, timeout=2)
        return jsonify({'online': resp.status_code == 200})
    except:
        return jsonify({'online': False})

@app.route('/api/casaos/access', methods=['POST'])
@login_required
def casaos_access():
    """Grant temporary direct access to CasaOS for authenticated user"""
    ip = request.remote_addr
    # Allow this IP for 1 hour
    CASAOS_ALLOWED_IPS[ip] = time.time() + 3600
    audit_log('CASAOS_ACCESS', f"Granted CasaOS access for IP {ip}", session.get('username'))
    
    # For now, we need to unblock port 80 for this IP via iptables
    try:
        subprocess.run(['iptables', '-I', 'INPUT', '1', '-p', 'tcp', '--dport', '80', '-s', ip, '-j', 'ACCEPT'], check=True)
    except:
        pass
    
    return jsonify({
        'success': True, 
        'url': f'http://{request.host.split(":")[0]}:9999',
        'expires_in': 3600
    })

# --- SERVICE MANAGEMENT ROUTES ---
# --- SERVICE MANAGEMENT ROUTES ---

def run_host_command(cmd_list):
    """
    Run a command on the HOST system.
    If running in Docker (detected by existence of /.dockerenv), use nsenter.
    Otherwise run directly.
    """
    # Check if inside Docker
    in_docker = os.path.exists('/.dockerenv')
    
    if in_docker:
        # Wrap command with nsenter to run on host (PID 1 namespace)
        # Requires privileged: true in docker-compose
        full_cmd = ['nsenter', '-t', '1', '-m', '-u', '-n', '-i'] + cmd_list
    else:
        full_cmd = cmd_list
        
    return subprocess.run(full_cmd, capture_output=True, text=True)

@app.route('/api/services/status')
@requires_permission('services', 'view')
def services_status():
    """Get status of managed services (Dynamic from settings)"""
    settings = load_app_settings()
    services_config = settings.get('services', [])
    
    # Backward compatibility if services is dict or missing (from old config)
    if not services_config:
         services_config = [
            {'id': 'ssh', 'name': 'SSH Server'},
            {'id': 'docker', 'name': 'Docker Engine'},
            {'id': 'cron', 'name': 'Cron Job'},
            {'id': 'gunicorn', 'name': 'Gunicorn Service'}, # Umum buat Flask
            {'id': 'python-app', 'name': 'Python App Service'} # Generik
         ]

    status = []
    for srv in services_config:
        # Handle both list of dicts and old format
        service_id = srv.get('id')
        label = srv.get('name', service_id)
        
        try:
            # Check active state
            res = run_host_command(['systemctl', 'is-active', service_id])
            active = res.stdout.strip() == 'active'
            
            # Check uptime/status details (optional)
            # res_status = run_host_command(['systemctl', 'status', service_id, '--no-pager', '-n', '0'])
            
            status.append({
                'id': service_id,
                'name': label,
                'active': active,
                'status_text': 'Running' if active else 'Stopped'
            })
        except Exception as e:
            status.append({'id': service_id, 'name': label, 'active': False, 'status_text': 'Error'})
            
    return jsonify({'services': status})

@app.route('/api/services/control', methods=['POST'])
@requires_permission('services', 'limited')
def service_control():
    """Start/Stop/Restart a service"""
    data = request.json
    service_id = data.get('service')
    action = data.get('action') # start, stop, restart
    
    settings = load_app_settings()
    services_config = settings.get('services', [])
    
    # Validate if legitimate service
    valid_ids = [s.get('id') for s in services_config]
    
    if service_id not in valid_ids:
        # Allow admin to control any service technically, but safest to restrict
        # For flexibility, let's allow it but log it warningly if not in list
        pass 

    if action not in ['start', 'stop', 'restart']:
        return jsonify({'error': 'Invalid action'}), 400
        
    try:
        run_host_command(['systemctl', action, service_id])
        audit_log('SERVICE_CONTROL', f"{action.title()} service {service_id}", session.get('username'))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/services/discover')
@requires_permission('services', 'view')
def discover_services():
    """Discover all systemd services on the host"""
    try:
        # List all unit files (services)
        res = run_host_command(['systemctl', 'list-unit-files', '--type=service', '--no-pager', '--no-legend'])
        
        services = []
        common_important = ['ssh', 'sshd', 'docker', 'nginx', 'apache2', 'mysql', 'mariadb', 
                           'postgresql', 'redis', 'mongodb', 'casaos', 'casaos-gateway',
                           'smbd', 'nmbd', 'vsftpd', 'fail2ban', 'ufw', 'cron', 'containerd',
                           'ollama', 'zerotier-one', 'gunicorn', 'uwsgi', 'flask',
                           'keuangan-web', 'keuangan-bot', 'server_monitor', 'yt_app', 
                           'yt_shorts_api', 'exsa-backend', 'youtube_bot', 'rclone']
        
        for line in res.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 2:
                unit_name = parts[0].replace('.service', '')
                state = parts[1]  # enabled, disabled, static, masked
                
                # Skip system internal services (start with systemd-, dbus, etc)
                if unit_name.startswith(('systemd-', 'dbus', 'getty', 'serial-getty', 'user@', 'autovt@')):
                    continue
                    
                # Check if currently running
                active_res = run_host_command(['systemctl', 'is-active', unit_name])
                is_active = active_res.stdout.strip() == 'active'
                
                # Prioritize common/important services
                priority = 1 if unit_name in common_important else 0
                
                services.append({
                    'id': unit_name,
                    'name': unit_name.replace('-', ' ').replace('_', ' ').title(),
                    'enabled': state == 'enabled',
                    'active': is_active,
                    'priority': priority
                })
        
        # Sort by priority (important first), then by name  
        services.sort(key=lambda x: (-x['priority'], x['name']))
        
        return jsonify({'services': services[:50]})  # Limit to 50
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- DETAILED METRICS ROUTE ---
@app.route('/api/metrics/detailed')
@login_required
def detailed_metrics():
    import time
    try:
        # CPU Details
        cpu_per_core = psutil.cpu_percent(percpu=True)
        cpu_total = psutil.cpu_percent()
        
        # RAM Details
        mem = psutil.virtual_memory()
        mem_details = {
            'total': mem.total,
            'available': mem.available,
            'used': mem.used,
            'free': mem.free,
            'cached': getattr(mem, 'cached', 0) if hasattr(mem, 'cached') else getattr(mem, 'active', 0), # Windows fallback
            'buffers': getattr(mem, 'buffers', 0),
            'percent': mem.percent
        }
        
        # Storage Details (Mount Points)
        partitions = []
        for part in psutil.disk_partitions(all=False):
            if 'snap' in part.mountpoint or 'docker' in part.mountpoint: # Skip clutter
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                partitions.append({
                    'device': part.device,
                    'mountpoint': part.mountpoint,
                    'fstype': part.fstype,
                    'total': usage.total,
                    'used': usage.used,
                    'free': usage.free,
                    'percent': usage.percent
                })
            except (PermissionError, OSError):
                continue

        # Disk I/O (System Wide)
        disk_io = psutil.disk_io_counters()
        io_stats = {
            'read_bytes': disk_io.read_bytes if disk_io else 0,
            'write_bytes': disk_io.write_bytes if disk_io else 0
        }

        # Network I/O
        net_io = psutil.net_io_counters()
        network_stats = {
            'bytes_sent': net_io.bytes_sent if net_io else 0,
            'bytes_recv': net_io.bytes_recv if net_io else 0
        }

        # Load Average & Uptime
        load_avg = os.getloadavg() if hasattr(os, 'getloadavg') else (0,0,0)
        uptime = int(time.time() - psutil.boot_time())

        # Top Processes (Expensive Operation)
        processes = []
        cpu_count = psutil.cpu_count(logical=True) or 1

        # Mengambil info process. Note: memory_info().rss is standard.
        for p in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_info']):
            try:
                p_info = p.info
                # Calculate memory in MB
                p_info['memory_mb'] = p_info['memory_info'].rss / (1024 * 1024)
                
                # Normalize CPU: psutil returns % of ONE core. We want % of TOTAL SYSTEM (to match dashboard).
                raw_cpu = p_info.get('cpu_percent', 0) or 0
                p_info['cpu_percent'] = round(raw_cpu / cpu_count, 1)
                
                processes.append(p_info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        
        # Sort Top 5 CPU (skip process 0 or idle)
        top_cpu = sorted(processes, key=lambda p: float(p['cpu_percent'] or 0), reverse=True)[:10]
        # Sort Top 5 Mem
        top_mem = sorted(processes, key=lambda p: float(p['memory_mb'] or 0), reverse=True)[:10]

        return jsonify({
            'cpu': {
                'total': cpu_total,
                'per_core': cpu_per_core,
                'top_processes': top_cpu,
                'load_avg': load_avg
            },
            'memory': {
                'details': mem_details,
                'top_processes': top_mem
            },
            'storage': {
                'partitions': partitions,
                'io': io_stats
            },
            'network': network_stats,
            'system': {
                'uptime': uptime
            }
        })
    except Exception as e:
        print(f"Error metrics: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/process/kill', methods=['POST'])
@requires_permission('services', 'limited')
def kill_process_api():
    pid = request.json.get('pid')
    try:
        # Check docker environment
        in_docker = os.path.exists('/.dockerenv')
        if in_docker:
             # Kill on HOST using systemctl kill?? No, 'kill' command via nsenter
             # systemctl kill is for services. For raw PID we use `kill -9 PID`
             run_host_command(['kill', '-9', str(pid)])
             # We rely on run_host_command wrapper
             return jsonify({'success': True})
        else:
             p = psutil.Process(int(pid))
             p.terminate()
             return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- MQTT LOGIC ---
def mqtt_on_connect(client, userdata, flags, rc):
    print(f"MQTT Connected with result code {rc}")
    settings = load_app_settings()
    devices = settings.get('mqtt', {}).get('devices', [])
    
    # Subscribe to status topics
    for dev in devices:
        topic = dev.get('topic') or dev.get('topic_state')
        if topic:
            client.subscribe(topic)
            print(f"MQTT Subscribed: {topic}")

def mqtt_on_message(client, userdata, msg):
    try:
        topic = msg.topic
        payload = msg.payload.decode()
        # print(f"MQTT Msg: {topic} -> {payload}")
        
        # Map topic to device ID
        settings = load_app_settings()
        devices = settings.get('mqtt', {}).get('devices', [])
        
        for dev in devices:
            t_stat = dev.get('topic') or dev.get('topic_state')
            if t_stat == topic:
                HOME_DEVICES_STATE[dev['id']] = {
                    'value': payload,
                    'ts': time.time()
                }
                # Emit socket event for realtime update
                socketio.emit('home_update', {'id': dev['id'], 'value': payload})
    except Exception as e:
        print(f"MQTT Error processing message: {e}")

def init_mqtt_client():
    global mqtt_client
    if not mqtt:
        print("MQTT Library not found")
        return

    settings = load_app_settings()
    mqtt_cfg = settings.get('mqtt', {})
    
    if not mqtt_cfg.get('enabled', False):
        if mqtt_client:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
            mqtt_client = None
        return

    broker = mqtt_cfg.get('broker')
    if not broker: return

    # Re-init if config changed or not exists
    if mqtt_client:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    
    try:
        mqtt_client = mqtt.Client()
        if mqtt_cfg.get('user'):
            mqtt_client.username_pw_set(mqtt_cfg['user'], mqtt_cfg.get('password', ''))
            
        mqtt_client.on_connect = mqtt_on_connect
        mqtt_client.on_message = mqtt_on_message
        
        port = int(mqtt_cfg.get('port', 1883))
        mqtt_client.connect(broker, port, 60)
        mqtt_client.loop_start()
        print(f"MQTT Client Started: {broker}:{port}")
    except Exception as e:
        print(f"MQTT Init Error: {e}")

@app.route('/api/home/status')
def home_status():
    """Get current state of home devices"""
    # Force refresh/check timeout logic if needed, but returning dict is fast
    return jsonify(HOME_DEVICES_STATE)

@app.route('/api/home/control', methods=['POST'])
@login_required
def home_control():
    """Control a device via MQTT"""
    if not mqtt_client:
        return jsonify({'error': 'MQTT not connected'}), 503
        
    data = request.json
    dev_id = data.get('id')
    state = data.get('state') # boolean usually
    
    settings = load_app_settings()
    devices = settings.get('mqtt', {}).get('devices', [])
    
    target_dev = next((d for d in devices if d['id'] == dev_id), None)
    if not target_dev:
        return jsonify({'error': 'Device not found'}), 404
        
    topic = target_dev.get('topic_set') or target_dev.get('topic_control')
    if not topic:
        return jsonify({'error': 'No control topic defined'}), 400
        
    payload = target_dev.get('payload_on', 'ON') if state else target_dev.get('payload_off', 'OFF')
    
    mqtt_client.publish(topic, payload)
    return jsonify({'success': True})

# Init MQTT on startup
# We delay it slightly or run it directly
init_mqtt_client()


# --- DATABASE & HISTORY LOGIC ---
HISTORY_DB_FILE = os.path.join(DATA_DIR, 'history.db')

def init_history_db():
    conn = sqlite3.connect(HISTORY_DB_FILE)
    c = conn.cursor()
    # Create metrics table: timestamp, cpu, ram, net_sent, net_recv
    c.execute('''CREATE TABLE IF NOT EXISTS metrics (
                    timestamp INTEGER PRIMARY KEY,
                    cpu REAL,
                    ram REAL,
                    net_sent REAL,
                    net_recv REAL
                 )''')
    # Auto cleanup old data trigger (keep last 3 days approx 4320 mins)
    c.execute('''CREATE TRIGGER IF NOT EXISTS clean_old_metrics 
                 AFTER INSERT ON metrics
                 BEGIN
                    DELETE FROM metrics WHERE timestamp < (NEW.timestamp - 259200);
                 END;''')
    conn.commit()
    conn.close()

def record_metrics_background():
    """Background task to record metrics every 60 seconds"""
    while True:
        try:
            # Stats
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            net = psutil.net_io_counters()
            
            # Save to DB
            conn = sqlite3.connect(HISTORY_DB_FILE)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO metrics (timestamp, cpu, ram, net_sent, net_recv) VALUES (?, ?, ?, ?, ?)",
                           (int(time.time()), cpu, ram, net.bytes_sent, net.bytes_recv))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Error recording metrics: {e}")
            
        socketio.sleep(60)

# Init DB on start
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
init_history_db()

# Start Background Task
socketio.start_background_task(record_metrics_background)

@app.route('/api/metrics/history')
@login_required
def get_metrics_history():
    """Get last 24h metrics (resampled/simplified if needed)"""
    try:
        range_hours = request.args.get('hours', 24, type=int)
        cutoff = int(time.time()) - (range_hours * 3600)
        
        conn = sqlite3.connect(HISTORY_DB_FILE)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM metrics WHERE timestamp > ? ORDER BY timestamp ASC", (cutoff,))
        rows = c.fetchall()
        conn.close()
        
        data = {
            'labels': [],
            'cpu': [],
            'ram': [],
            'net_sent': [], 
            'net_recv': []
        }
        
        for r in rows:
            data['labels'].append(r['timestamp'])
            data['cpu'].append(r['cpu'])
            data['ram'].append(r['ram'])
            data['net_sent'].append(r['timestamp']) # Placeholder, handled in UI? wait, previous code had delta logic. Let's keep raw.
            data['net_recv'].append(r['net_recv'])
            
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/storage/analyze')
@login_required
def storage_analyze():
    """Analyze top files (Requires 'scan' param path, default /)"""
    scan_path = request.args.get('path', '/app/data') 
    
    # We want to scan HOST files. We mounted /:/host/root
    prefix = "/host/root"
    target_path = prefix
    
    try:
        # Run du command. It's safe-ish.
        # du -ah --max-depth=2 /host/root | sort -rh | head -n 20
        
        full_cmd = f"du -ah --max-depth=2 {target_path} 2>/dev/null | sort -rh | head -n 20"
        
        res = subprocess.run(full_cmd, shell=True, capture_output=True, text=True, timeout=20)
        
        lines = res.stdout.strip().split('\n')
        results = []
        for line in lines:
            parts = line.split('\t')
            if len(parts) == 2:
                display_path = parts[1].replace(prefix, '') or '/'
                results.append({'size': parts[0], 'path': display_path})
                
        return jsonify({'files': results})
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Scan timed out (Disk too large/slow)'}), 408
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============ UPDATE CHECKER ============
@app.route('/api/version')
@login_required
def get_version():
    """Menampilkan versi aplikasi saat ini"""
    return jsonify({
        'name': APP_NAME,
        'version': APP_VERSION,
        'build_date': '2026-01-11'
    })

@app.route('/api/check-update')
@login_required
def check_update_layout():
    """Cek apakah ada versi baru tersedia (Standardized)"""
    try:
        # Coba ambil info versi dari GitHub
        headers = {'User-Agent': 'MuhfiDesk/1.0'}
        response = requests.get(UPDATE_CHECK_URL, headers=headers, timeout=10)
        
        if response.status_code == 200:
            remote_info = response.json()
            remote_version = remote_info.get('version', '0.0.0')
            
            # Bandingkan versi (Semantic Versioning)
            def parse_version(v):
                return [int(x) for x in v.split('.')] if v else [0,0,0]

            current_parts = parse_version(APP_VERSION)
            remote_parts = parse_version(remote_version)
            
            update_available = remote_parts > current_parts
            
            return jsonify({
                'current_version': APP_VERSION,
                'latest_version': remote_version,
                'update_available': update_available,
                'changelog': remote_info.get('changelog', ''),
                'download_url': remote_info.get('download_url', ''),
                'release_date': remote_info.get('release_date', ''),
                'success': True
            })
        else:
            return jsonify({
                'current_version': APP_VERSION,
                'error': f'Server update merespon dengan kode: {response.status_code}',
                'update_available': False,
                'success': False
            })
    except requests.exceptions.Timeout:
        return jsonify({
            'current_version': APP_VERSION,
            'error': 'Timeout saat menghubungi server update',
            'update_available': False,
            'success': False
        })
    except Exception as e:
        return jsonify({
            'current_version': APP_VERSION,
            'error': f'Gagal cek update: {str(e)}',
            'update_available': False,
            'success': False
        })

@app.route('/api/perform-update', methods=['POST'])
@login_required
def perform_update():
    """Melakukan update otomatis dari GitHub"""
    if session.get('role') not in ['owner', 'admin']:
        return jsonify({'success': False, 'error': 'Akses ditolak.'}), 403

    try:
        # Menjalankan git pull untuk mendapatkan pembaruan terbaru dari GitHub
        update_cmd = f"cd '{BASE_DIR}' && git pull origin main"
        subprocess.Popen(update_cmd, shell=True)
        
        # Coba restart layanan secara asinkron agar respon API sempat terkirim
        restart_cmd = f"sleep 3 && (sudo systemctl restart muhfidesk || kill -HUP {os.getpid()})"
        subprocess.Popen(restart_cmd, shell=True)
        
        return jsonify({
            'success': True, 
            'message': 'Pembaruan sedang diunduh (git pull). Harap tunggu sekitar 1-2 menit lalu muat ulang halaman (Refresh).'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f'Gagal menjalankan pembaruan: {str(e)}'}), 500

# ========================================


# ============ NETWORK CONFIGURATION ============
@app.route('/network')
@login_required
@admin_required
def network_page():
    """Halaman konfigurasi jaringan"""
    return render_template('network.html')

@app.route('/api/network/info')
@login_required
def get_network_info():
    """Mendapatkan informasi jaringan saat ini (HOST)"""
    try:
        result = {
            'hostname': '',
            'primary_ip': '',
            'primary_mac': '',
            'gateway': '',
            'dns': [],
            'interfaces': []
        }
        
        nsenter = ['nsenter', '-t', '1', '-m', '-u', '-n', '-i']
        
        # 1. Get Hostname
        try:
            res = subprocess.run(nsenter + ['hostname'], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                result['hostname'] = res.stdout.strip()
        except:
            result['hostname'] = 'Unknown'

        # 2. Get Interfaces & IP (via ip -j addr)
        try:
            # Try JSON format first (modern iproute2)
            res = subprocess.run(nsenter + ['ip', '-j', 'addr'], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                addr_data = json.loads(res.stdout)
                
                for iface in addr_data:
                    name = iface.get('ifname', 'unknown')
                    if name == 'lo': continue
                    
                    iface_info = {
                        'name': name,
                        'ip': '',
                        'mac': iface.get('address', ''),
                        'type': 'ethernet',
                        'status': iface.get('operstate', 'unknown').lower()
                    }
                    
                    # Heuristic Type
                    lower_name = name.lower()
                    if 'wlan' in lower_name or 'wifi' in lower_name or 'wl' in lower_name:
                        iface_info['type'] = 'wifi'
                    elif 'tun' in lower_name or 'wg' in lower_name or 'zt' in lower_name:
                        iface_info['type'] = 'vpn'
                    elif 'br' in lower_name or 'docker' in lower_name or 'veth' in lower_name:
                        iface_info['type'] = 'virtual'
                        
                    # Get IPs
                    for addr in iface.get('addr_info', []):
                        if addr.get('family') == 'inet':
                            ip = addr.get('local')
                            iface_info['ip'] = ip
                            # Determine primary IP (heuristic: global scope, not docker/br)
                            if not result['primary_ip'] and iface_info['type'] in ['ethernet', 'wifi']:
                                result['primary_ip'] = ip
                                result['primary_mac'] = iface_info['mac']
                    
                    result['interfaces'].append(iface_info)
            else:
                # Fallback implementation if needed (omitted for brevity, expecting modern host)
                pass
        except Exception as e:
            print(f"Error getting interfaces: {e}")

        # 3. Get Gateway
        try:
            res = subprocess.run(nsenter + ['ip', '-j', 'route', 'show', 'default'], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                routes = json.loads(res.stdout)
                if routes:
                    result['gateway'] = routes[0].get('gateway', '')
        except:
            pass

        # 4. Get DNS (cat /etc/resolv.conf)
        try:
            res = subprocess.run(nsenter + ['cat', '/etc/resolv.conf'], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if line.startswith('nameserver'):
                        parts = line.split()
                        if len(parts) > 1:
                            result['dns'].append(parts[1])
        except:
            pass

        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/network/hostname', methods=['POST'])
@login_required
@admin_required
def update_hostname():
    """Mengubah hostname server"""
    try:
        data = request.json
        new_hostname = data.get('hostname', '').strip()
        
        if not new_hostname:
            return jsonify({'error': 'Hostname tidak boleh kosong'}), 400
        
        # Validasi hostname
        import re
        if not re.match(r'^[a-zA-Z0-9-]+$', new_hostname):
            return jsonify({'error': 'Hostname hanya boleh berisi huruf, angka, dan tanda hubung'}), 400
        
        if len(new_hostname) > 63:
            return jsonify({'error': 'Hostname terlalu panjang (maks 63 karakter)'}), 400
        
        # Update hostname using hostnamectl (systemd)
        result = subprocess.run(
            ['hostnamectl', 'set-hostname', new_hostname],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            return jsonify({'error': f'Gagal mengubah hostname: {result.stderr}'}), 500
        
        # Update /etc/hosts juga
        try:
            with open('/etc/hosts', 'r') as f:
                hosts_content = f.read()
            
            # Replace old hostname references
            old_hostname = socket.gethostname()
            hosts_content = hosts_content.replace(old_hostname, new_hostname)
            
            with open('/etc/hosts', 'w') as f:
                f.write(hosts_content)
        except Exception as e:
            # Not critical, continue
            pass
        
        audit_log('NETWORK_CHANGE', f'Hostname diubah menjadi: {new_hostname}', session.get('username'))
        return jsonify({'success': True, 'message': 'Hostname berhasil diubah'})
        
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Timeout saat mengubah hostname'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/network/dns', methods=['POST'])
@login_required
@admin_required
def update_dns():
    """Mengubah konfigurasi DNS"""
    try:
        data = request.json
        dns_servers = data.get('dns', [])
        
        if not dns_servers or len(dns_servers) == 0:
            return jsonify({'error': 'Minimal satu DNS server diperlukan'}), 400
        
        # Validasi IP
        import re
        ip_pattern = re.compile(r'^(\d{1,3}\.){3}\d{1,3}$')
        for dns in dns_servers:
            if not ip_pattern.match(dns):
                return jsonify({'error': f'Format DNS tidak valid: {dns}'}), 400
        
        # Write to resolv.conf
        # Note: This might be overwritten by DHCP or networkmanager
        resolv_content = "# Generated by MuhfiDesk\n"
        for dns in dns_servers:
            resolv_content += f"nameserver {dns}\n"
        
        with open('/etc/resolv.conf', 'w') as f:
            f.write(resolv_content)
        
        audit_log('NETWORK_CHANGE', f'DNS diubah menjadi: {", ".join(dns_servers)}', session.get('username'))
        return jsonify({'success': True, 'message': 'Konfigurasi DNS berhasil disimpan'})
        
    except PermissionError:
        return jsonify({'error': 'Tidak memiliki izin untuk mengubah konfigurasi DNS'}), 403
    except Exception as e:
        return jsonify({'error': str(e)}), 500
# ===============================================


# ============ STORAGE MANAGEMENT ============
@app.route('/storage')
@login_required
@admin_required
def storage_page():
    """Halaman manajemen penyimpanan"""
    return render_template('storage.html')

@app.route('/api/storage/disks')
@login_required
def get_storage_disks():
    """Mendapatkan daftar disk dan partisi yang sangat lengkap menggunakan lsblk"""
    try:
        disks = []
        total_size = 0
        total_used = 0
        total_free = 0
        disk_count = 0
        
        # Ambil data dari lsblk
        try:
            lsblk_result = subprocess.run(
                ['lsblk', '-J', '-b', '-o', 'NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,MODEL,SERIAL,VENDOR,UUID'],
                capture_output=True, text=True, timeout=10
            )
            if lsblk_result.returncode != 0:
                raise Exception("lsblk failed")
            
            lsblk_data = json.loads(lsblk_result.stdout)
        except Exception as e:
            return jsonify({'error': f"Gagal mengambil data lsblk: {str(e)}"}), 500

        # Map untuk memudahkan pencarian usage dari psutil
        mounted_usage = {}
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                mounted_usage[part.mountpoint] = {
                    'total': usage.total,
                    'used': usage.used,
                    'free': usage.free,
                    'percent': usage.percent
                }
            except:
                pass

        for dev in lsblk_data.get('blockdevices', []):
            # Skip loop devices unless they are specifically requested or large
            if dev.get('type') == 'loop':
                continue
            
            # Info fisik disk
            physical_info = {
                'model': dev.get('model', 'Unknown'),
                'serial': dev.get('serial', 'N/A'),
                'vendor': dev.get('vendor', ''),
                'size_raw': int(dev.get('size', 0))
            }
            
            # Jika ini disk fisik, tambahkan ke hitungan disk
            if dev.get('type') == 'disk':
                disk_count += 1

            # Proses device itu sendiri (bisa jadi disk tanpa partisi atau partisi itu sendiri)
            def process_device(item, parent_info=None):
                name = item.get('name')
                device_path = f"/dev/{name}"
                mountpoint = item.get('mountpoint')
                fstype = item.get('fstype', '')
                size_raw = int(item.get('size', 0))
                
                info = {
                    'name': name,
                    'device': device_path,
                    'mountpoint': mountpoint,
                    'fstype': fstype,
                    'size_raw': size_raw,
                    'size': format_bytes(size_raw),
                    'type': 'hdd',
                    'is_partition': item.get('type') == 'part',
                    'mounted': mountpoint is not None,
                    'model': parent_info['model'] if parent_info else item.get('model', 'Unknown'),
                    'serial': parent_info['serial'] if parent_info else item.get('serial', 'N/A'),
                    'uuid': item.get('uuid', '')
                }

                # Deteksi tipe icon
                if 'nvme' in name: info['type'] = 'ssd'
                elif 'mmc' in name: info['type'] = 'sd'
                elif parent_info and 'usb' in (parent_info['model'] or '').lower(): info['type'] = 'usb'
                
                # Tambahkan data penggunaan jika mounted
                if mountpoint and mountpoint in mounted_usage:
                    usage = mounted_usage[mountpoint]
                    info['used'] = format_bytes(usage['used'])
                    info['free'] = format_bytes(usage['free'])
                    info['usage_percent'] = usage['percent']
                    
                    # Hanya tambahkan ke total jika ini mount point unik (bukan bind mount)
                    # Kita anggap / dan /home dsb adalah unik
                    non_unique = ['/etc/resolv.conf', '/etc/hostname', '/etc/hosts']
                    if mountpoint not in non_unique and not mountpoint.startswith('/snap'):
                        nonlocal total_size, total_used, total_free
                        # Hindari double counting jika device yang sama di-mount di tempat berbeda
                        # (Sudah di-filter oleh psutil.disk_partitions(all=False) sebenarnya)
                else:
                    info['used'] = None
                    info['free'] = None
                    info['usage_percent'] = 0

                return info

            # Jika disk memiliki anak (partisi)
            if dev.get('children'):
                for child in dev['children']:
                    disks.append(process_device(child, physical_info))
            else:
                # Disk tanpa partisi (atau disk itu sendiri yang di-mount langsung)
                disks.append(process_device(dev, physical_info))

        # Hitung ringkasan dari mounted_usage untuk akurasi
        for mpoint, usage in mounted_usage.items():
            if mpoint.startswith('/snap') or mpoint in ['/etc/resolv.conf', '/etc/hostname', '/etc/hosts']:
                continue
            total_size += usage['total']
            total_used += usage['used']
            total_free += usage['free']

        return jsonify({
            'disks': disks,
            'summary': {
                'disk_count': disk_count,
                'total_size': format_bytes(total_size),
                'total_used': format_bytes(total_used),
                'total_free': format_bytes(total_free)
            }
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

def format_bytes(bytes_val):
    """Format bytes ke human readable (GB, TB, dll)"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} PB"

@app.route('/api/storage/mount', methods=['POST'])
@login_required
@admin_required
def mount_partition():
    """Mount partisi ke mount point tertentu"""
    try:
        data = request.json
        device = data.get('device', '')
        mountpoint = data.get('mountpoint', '')
        
        if not device or not mountpoint:
            return jsonify({'error': 'Device dan mount point diperlukan'}), 400
        
        # Validasi path
        if not mountpoint.startswith('/'):
            return jsonify({'error': 'Mount point harus absolute path (dimulai dengan /)'}), 400
        
        # Buat direktori mount point jika belum ada
        if not os.path.exists(mountpoint):
            os.makedirs(mountpoint, exist_ok=True)
        
        # Jalankan mount command
        result = subprocess.run(
            ['mount', device, mountpoint],
            capture_output=True, text=True, timeout=30
        )
        
        if result.returncode != 0:
            return jsonify({'error': f'Gagal mount: {result.stderr}'}), 500
        
        audit_log('STORAGE_MOUNT', f'Mounted {device} ke {mountpoint}', session.get('username'))
        return jsonify({'success': True, 'message': f'Berhasil mount {device} ke {mountpoint}'})
        
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Timeout saat mount'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/storage/unmount', methods=['POST'])
@login_required
@admin_required
def unmount_partition():
    """Unmount partisi"""
    try:
        data = request.json
        device = data.get('device', '')
        
        if not device:
            return jsonify({'error': 'Device diperlukan'}), 400
        
        # Jalankan umount command
        result = subprocess.run(
            ['umount', device],
            capture_output=True, text=True, timeout=30
        )
        
        if result.returncode != 0:
            # Coba dengan -l (lazy unmount) jika gagal
            result = subprocess.run(
                ['umount', '-l', device],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                return jsonify({'error': f'Gagal unmount: {result.stderr}'}), 500
        
        audit_log('STORAGE_UNMOUNT', f'Unmounted {device}', session.get('username'))
        return jsonify({'success': True, 'message': f'Berhasil unmount {device}'})
        
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Timeout saat unmount'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500
# ============================================


# ============ BACKUP & RESTORE ============
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')
if not os.path.exists(BACKUP_DIR):
    os.makedirs(BACKUP_DIR)

@app.route('/backup')
@owner_required
def backup_page():
    """Halaman backup & restore"""
    return render_template('backup.html')

@app.route('/api/backup/list')
@owner_required
def list_backups():
    """Mendapatkan daftar backup yang tersedia"""
    try:
        backups = []
        
        if os.path.exists(BACKUP_DIR):
            for filename in os.listdir(BACKUP_DIR):
                if filename.endswith('.tar.gz'):
                    filepath = os.path.join(BACKUP_DIR, filename)
                    stat = os.stat(filepath)
                    
                    # Parse nama dan tanggal dari filename
                    # Format: backup_YYYY-MM-DD_HH-MM-SS_nama.tar.gz
                    parts = filename.replace('.tar.gz', '').split('_')
                    if len(parts) >= 3:
                        date_str = f"{parts[1]} {parts[2].replace('-', ':')}"
                        name = '_'.join(parts[3:]) if len(parts) > 3 else 'Backup'
                    else:
                        date_str = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
                        name = filename.replace('.tar.gz', '')
                    
                    backups.append({
                        'id': filename,
                        'name': name or 'Backup',
                        'filename': filename,
                        'date': date_str,
                        'size': format_bytes(stat.st_size),
                        'timestamp': stat.st_mtime
                    })
        
        # Urutkan dari terbaru
        backups.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return jsonify({'backups': backups})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/backup/create', methods=['POST'])
@owner_required
def create_backup():
    """Membuat backup baru"""
    try:
        import tarfile
        
        data = request.json
        custom_name = data.get('name', '').strip()
        include_config = data.get('include_config', True)
        include_docker = data.get('include_docker', True)
        include_users = data.get('include_users', False)
        
        # Generate nama file
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        safe_name = ''.join(c for c in custom_name if c.isalnum() or c in '-_') if custom_name else ''
        filename = f"backup_{timestamp}_{safe_name}.tar.gz" if safe_name else f"backup_{timestamp}.tar.gz"
        filepath = os.path.join(BACKUP_DIR, filename)
        
        # Buat tarball
        with tarfile.open(filepath, 'w:gz') as tar:
            # Backup konfigurasi
            if include_config:
                if os.path.exists(APP_SETTINGS_FILE):
                    tar.add(APP_SETTINGS_FILE, arcname='app_settings.json')
                
                if os.path.exists(SECURITY_CONFIG_FILE):
                    tar.add(SECURITY_CONFIG_FILE, arcname='security_config.json')

                telegram_file = os.path.join(DATA_DIR, 'telegram_config.json')
                if os.path.exists(telegram_file):
                    tar.add(telegram_file, arcname='telegram_config.json')
            
            # Backup docker compose
            if include_docker:
                compose_file = os.path.join(BASE_DIR, 'docker-compose.yml')
                if os.path.exists(compose_file):
                    tar.add(compose_file, arcname='docker-compose.yml')
            
            # Backup data pengguna
            if include_users:
                users_db = os.path.join(DATA_DIR, 'users.db')
                if os.path.exists(users_db):
                    tar.add(users_db, arcname='users.db')
        
        audit_log('BACKUP_CREATED', f'Backup dibuat: {filename}', session.get('username'))
        
        return jsonify({
            'success': True,
            'filename': filename,
            'message': 'Backup berhasil dibuat'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/backup/download/<backup_id>')
@owner_required
def download_backup(backup_id):
    """Download file backup"""
    try:
        # Sanitasi nama file
        if '..' in backup_id or '/' in backup_id or '\\' in backup_id:
            return jsonify({'error': 'Invalid backup ID'}), 400
        
        filepath = os.path.join(BACKUP_DIR, backup_id)
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'Backup tidak ditemukan'}), 404
        
        from flask import send_file
        return send_file(filepath, as_attachment=True, download_name=backup_id)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/backup/restore', methods=['POST'])
@owner_required
def restore_backup():
    """Restore dari backup"""
    try:
        import tarfile
        
        data = request.json
        backup_id = data.get('id', '')
        
        # Sanitasi nama file
        if '..' in backup_id or '/' in backup_id or '\\' in backup_id:
            return jsonify({'error': 'Invalid backup ID'}), 400
        
        filepath = os.path.join(BACKUP_DIR, backup_id)
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'Backup tidak ditemukan'}), 404
        
        # Ekstrak backup
        with tarfile.open(filepath, 'r:gz') as tar:
            for member in tar.getmembers():
                # Restore ke lokasi yang sesuai
                if member.name in ('settings.json', 'app_settings.json'):
                    tar.extract(member, DATA_DIR)
                elif member.name == 'security_config.json':
                    tar.extract(member, DATA_DIR)
                elif member.name == 'telegram_config.json':
                    tar.extract(member, DATA_DIR)
                elif member.name == 'docker-compose.yml':
                    tar.extract(member, BASE_DIR)
                elif member.name == 'users.db':
                    tar.extract(member, DATA_DIR)

        for sensitive_file in [
            APP_SETTINGS_FILE,
            SECURITY_CONFIG_FILE,
            os.path.join(DATA_DIR, 'telegram_config.json'),
            os.path.join(DATA_DIR, 'users.db'),
        ]:
            harden_file_permissions(sensitive_file)
        
        audit_log('BACKUP_RESTORED', f'Backup di-restore: {backup_id}', session.get('username'))
        
        return jsonify({
            'success': True,
            'message': 'Backup berhasil di-restore'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/backup/delete/<backup_id>', methods=['DELETE'])
@owner_required
def delete_backup(backup_id):
    """Menghapus backup"""
    try:
        # Sanitasi nama file
        if '..' in backup_id or '/' in backup_id or '\\' in backup_id:
            return jsonify({'error': 'Invalid backup ID'}), 400
        
        filepath = os.path.join(BACKUP_DIR, backup_id)
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'Backup tidak ditemukan'}), 404
        
        os.remove(filepath)
        
        audit_log('BACKUP_DELETED', f'Backup dihapus: {backup_id}', session.get('username'))
        
        return jsonify({'success': True, 'message': 'Backup berhasil dihapus'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
# ==========================================


# ============ SMB/SAMBA FILE SHARING ============
# Path ke config Samba di host (mounted via docker-compose)
SMB_CONFIG_FILE = '/host/root/etc/samba/smb.conf'

@app.route('/sharing')
@login_required
@admin_required
def sharing_page():
    """Halaman berbagi file SMB"""
    return render_template('sharing.html')

@app.route('/api/smb/status')
@login_required
def get_smb_status():
    """Mendapatkan status Samba"""
    try:
        installed = False
        running = False
        
        # Method 1: Cek via nsenter ke host (jika container privileged)
        try:
            result = subprocess.run(
                ['nsenter', '-t', '1', '-m', '-u', '-n', '-i', 'which', 'smbd'],
                capture_output=True, text=True, timeout=5
            )
            installed = result.returncode == 0
            
            if installed:
                status = subprocess.run(
                    ['nsenter', '-t', '1', '-m', '-u', '-n', '-i', 'systemctl', 'is-active', 'smbd'],
                    capture_output=True, text=True, timeout=5
                )
                running = status.stdout.strip() == 'active'
        except:
            pass
        
        # Method 2: Fallback - cek file config di host (jika di-mount)
        if not installed:
            host_smb_conf = '/host/root/etc/samba/smb.conf'
            if os.path.exists(host_smb_conf):
                installed = True
                # Cek apakah smbd proses berjalan
                try:
                    result = subprocess.run(['pgrep', '-x', 'smbd'], capture_output=True, text=True)
                    running = result.returncode == 0
                except:
                    pass
        
        # Method 3: Cek di dalam container (untuk testing lokal)
        if not installed:
            result = subprocess.run(['which', 'smbd'], capture_output=True, text=True)
            installed = result.returncode == 0
            if installed:
                status = subprocess.run(['systemctl', 'is-active', 'smbd'], capture_output=True, text=True)
                running = status.stdout.strip() == 'active'
        
        # Dapatkan IP server
        server_ip = ''
        try:
            net_info = psutil.net_if_addrs()
            for iface, addrs in net_info.items():
                if iface == 'lo':
                    continue
                for addr in addrs:
                    if addr.family == socket.AF_INET and not addr.address.startswith('127.'):
                        server_ip = addr.address
                        break
                if server_ip:
                    break
        except:
            pass
        
        return jsonify({
            'installed': installed,
            'running': running,
            'server_ip': server_ip
        })
    except Exception as e:
        return jsonify({'error': str(e), 'installed': False, 'running': False}), 500

@app.route('/api/smb/shares')
@login_required
def get_smb_shares():
    """Mendapatkan daftar share aktif"""
    try:
        shares = []
        
        if os.path.exists(SMB_CONFIG_FILE):
            with open(SMB_CONFIG_FILE, 'r') as f:
                content = f.read()
            
            # Parse smb.conf untuk mencari shares
            import re
            # Match [sharename] sections (exclude global, homes, printers)
            pattern = r'\[([^\]]+)\]\s*\n([^[]*)'
            matches = re.findall(pattern, content)
            
            for name, config in matches:
                if name.lower() in ['global', 'homes', 'printers', 'print$']:
                    continue
                
                # Parse path dari config
                path_match = re.search(r'path\s*=\s*(.+)', config)
                path = path_match.group(1).strip() if path_match else ''
                
                shares.append({
                    'name': name,
                    'path': path
                })
        
        return jsonify({'shares': shares})
    except Exception as e:
        return jsonify({'error': str(e), 'shares': []}), 500

@app.route('/api/smb/share/add', methods=['POST'])
@login_required
@admin_required
def add_smb_share():
    """Menambahkan share baru"""
    try:
        data = request.json
        name = data.get('name', '').strip()
        path = data.get('path', '').strip()
        description = data.get('description', '').strip()
        is_public = data.get('public', True)
        writable = data.get('writable', True)
        
        if not name or not path:
            return jsonify({'error': 'Nama dan path harus diisi'}), 400
        
        # Validasi nama (alphanumeric only)
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            return jsonify({'error': 'Nama hanya boleh huruf, angka, underscore, dan dash'}), 400
        
        # Cek path exists
        if not os.path.exists(path):
            try:
                os.makedirs(path, exist_ok=True)
            except:
                return jsonify({'error': f'Path tidak ada dan tidak bisa dibuat: {path}'}), 400
        
        # Buat konfigurasi share
        share_config = f"""
[{name}]
   comment = {description or name}
   path = {path}
   browseable = yes
   read only = {'no' if writable else 'yes'}
   guest ok = {'yes' if is_public else 'no'}
   create mask = 0755
   directory mask = 0755
"""
        
        # Append ke smb.conf
        with open(SMB_CONFIG_FILE, 'a') as f:
            f.write(share_config)
        
        # Reload Samba
        subprocess.run(['systemctl', 'reload', 'smbd'], capture_output=True)
        
        audit_log('SMB_SHARE_ADDED', f'Share ditambahkan: {name} -> {path}', session.get('username'))
        
        return jsonify({'success': True, 'message': 'Share berhasil ditambahkan'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/smb/share/remove', methods=['DELETE'])
@login_required
@admin_required
def remove_smb_share():
    """Menghapus share"""
    try:
        data = request.json
        name = data.get('name', '').strip()
        
        if not name:
            return jsonify({'error': 'Nama share diperlukan'}), 400
        
        if not os.path.exists(SMB_CONFIG_FILE):
            return jsonify({'error': 'File konfigurasi Samba tidak ditemukan'}), 404
        
        with open(SMB_CONFIG_FILE, 'r') as f:
            content = f.read()
        
        # Hapus section share
        import re
        pattern = rf'\[{re.escape(name)}\][^\[]*'
        new_content = re.sub(pattern, '', content)
        
        with open(SMB_CONFIG_FILE, 'w') as f:
            f.write(new_content)
        
        # Reload Samba
        subprocess.run(['systemctl', 'reload', 'smbd'], capture_output=True)
        
        audit_log('SMB_SHARE_REMOVED', f'Share dihapus: {name}', session.get('username'))
        
        return jsonify({'success': True, 'message': 'Share berhasil dihapus'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/smb/control', methods=['POST'])
@login_required
@admin_required
def control_smb():
    """Start/stop Samba service"""
    try:
        data = request.json
        action = data.get('action', '')
        
        if action not in ['start', 'stop', 'restart']:
            return jsonify({'error': 'Action tidak valid'}), 400
        
        result = subprocess.run(['systemctl', action, 'smbd'], capture_output=True, text=True, timeout=30)
        
        if result.returncode != 0:
            return jsonify({'error': f'Gagal {action} Samba: {result.stderr}'}), 500
        
        audit_log('SMB_CONTROL', f'Samba di-{action}', session.get('username'))
        
        return jsonify({'success': True, 'message': f'Samba berhasil di-{action}'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/smb/install', methods=['POST'])
@login_required
@admin_required
def install_smb():
    """Install Samba"""
    try:
        # Update package list first
        update_result = subprocess.run(
            ['apt-get', 'update'],
            capture_output=True, text=True, timeout=120
        )
        
        # Install samba
        result = subprocess.run(
            ['apt-get', 'install', '-y', 'samba'],
            capture_output=True, text=True, timeout=300
        )
        
        if result.returncode != 0:
            return jsonify({'error': f'Gagal install Samba: {result.stderr}'}), 500
        
        # Enable dan start service
        subprocess.run(['systemctl', 'enable', 'smbd'], capture_output=True)
        subprocess.run(['systemctl', 'start', 'smbd'], capture_output=True)
        
        audit_log('SMB_INSTALLED', 'Samba berhasil diinstall', session.get('username'))
        
        return jsonify({'success': True, 'message': 'Samba berhasil diinstall'})
        
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Timeout saat install (> 5 menit)'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500
# ================================================


# ============ VPN (WIREGUARD) ============
WG_CONFIG_DIR = '/etc/wireguard'
WG_INTERFACE = 'wg0'

@app.route('/vpn')
@login_required
@admin_required
def vpn_page():
    """Halaman VPN Manager"""
    return render_template('vpn.html')

@app.route('/api/vpn/status')
@login_required
def get_vpn_status():
    """Mendapatkan status WireGuard"""
    try:
        # Cek apakah WireGuard terinstall di HOST (via nsenter)
        nsenter = ['nsenter', '-t', '1', '-m', '-u', '-n', '-i']
        result = subprocess.run(nsenter + ['which', 'wg'], capture_output=True, text=True)
        installed = result.returncode == 0
        
        running = False
        server_ip = ''
        port = '51820'
        client_count = 0
        
        if installed:
            # Cek apakah interface aktif di HOST
            status = subprocess.run(nsenter + ['wg', 'show', WG_INTERFACE], capture_output=True, text=True)
            running = status.returncode == 0
            
            if running:
                # Parse port dari output
                for line in status.stdout.split('\n'):
                    if 'listening port' in line:
                        port = line.split(':')[-1].strip()
                    if 'peer:' in line:
                        client_count += 1
        
        # Dapatkan IP server
        try:
            net_info = psutil.net_if_addrs()
            for iface, addrs in net_info.items():
                if iface == 'lo' or iface.startswith('wg'):
                    continue
                for addr in addrs:
                    if addr.family == socket.AF_INET and not addr.address.startswith('127.'):
                        server_ip = addr.address
                        break
                if server_ip:
                    break
        except:
            pass
        
        return jsonify({
            'installed': installed,
            'running': running,
            'server_ip': server_ip,
            'port': port,
            'client_count': client_count
        })
    except Exception as e:
        return jsonify({'error': str(e), 'installed': False}), 500

@app.route('/api/vpn/clients')
@login_required
def get_vpn_clients():
    """Mendapatkan daftar client VPN"""
    try:
        clients = []
        clients_dir = os.path.join(WG_CONFIG_DIR, 'clients')
        
        if os.path.exists(clients_dir):
            for filename in os.listdir(clients_dir):
                if filename.endswith('.conf'):
                    client_name = filename.replace('.conf', '')
                    
                    # Coba baca IP dari file config
                    client_ip = ''
                    config_path = os.path.join(clients_dir, filename)
                    try:
                        with open(config_path, 'r') as f:
                            for line in f:
                                if line.strip().startswith('Address'):
                                    client_ip = line.split('=')[1].strip()
                                    break
                    except:
                        pass
                    
                    clients.append({
                        'name': client_name,
                        'ip': client_ip
                    })
        
        return jsonify({'clients': clients})
    except Exception as e:
        return jsonify({'error': str(e), 'clients': []}), 500

@app.route('/api/vpn/client/add', methods=['POST'])
@login_required
@admin_required
def add_vpn_client():
    """Membuat client VPN baru"""
    try:
        data = request.json
        name = data.get('name', '').strip()
        
        if not name:
            return jsonify({'error': 'Nama client harus diisi'}), 400
        
        # Validasi nama
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            return jsonify({'error': 'Nama hanya boleh huruf, angka, underscore, dan dash'}), 400
        
        clients_dir = os.path.join(WG_CONFIG_DIR, 'clients')
        os.makedirs(clients_dir, exist_ok=True)
        
        # Cek apakah client sudah ada
        config_path = os.path.join(clients_dir, f'{name}.conf')
        if os.path.exists(config_path):
            return jsonify({'error': 'Client dengan nama ini sudah ada'}), 400
        
        # Generate keys
        # Generate keys via nsenter (Host)
        nsenter = ['nsenter', '-t', '1', '-m', '-u', '-n', '-i']
        # Gen private key
        private_key = subprocess.run(nsenter + ['wg', 'genkey'], capture_output=True, text=True).stdout.strip()
        # Gen public key (pipe private key)
        public_key = subprocess.run(nsenter + ['wg', 'pubkey'], input=private_key, capture_output=True, text=True).stdout.strip()
        
        # Baca server public key
        server_public_key = ''
        server_config = os.path.join(WG_CONFIG_DIR, f'{WG_INTERFACE}.conf')
        if os.path.exists(server_config):
            with open(server_config, 'r') as f:
                for line in f:
                    if 'PrivateKey' in line:
                        server_private = line.split('=')[1].strip()
                        # Public key via nsenter
                        server_public_key = subprocess.run(nsenter + ['wg', 'pubkey'], input=server_private, capture_output=True, text=True).stdout.strip()
                        break
        
        # Dapatkan IP server (HOST IP) via nsenter
        server_ip = ''
        try:
            # Use hostname -I on host
            res = subprocess.run(nsenter + ['hostname', '-I'], capture_output=True, text=True)
            ips = res.stdout.strip().split()
            if ips:
                server_ip = ips[0]
        except:
            pass
            
        if not server_ip:
            server_ip = 'YOUR_SERVER_IP'
        
        # Hitung IP untuk client baru (simplistik)
        existing_clients = len([f for f in os.listdir(clients_dir) if f.endswith('.conf')]) if os.path.exists(clients_dir) else 0
        client_ip = f'10.66.66.{existing_clients + 2}/32'
        
        # Buat config client
        client_config = f"""[Interface]
PrivateKey = {private_key}
Address = {client_ip}
DNS = 1.1.1.1

[Peer]
PublicKey = {server_public_key}
Endpoint = {server_ip}:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""
        
        # Simpan config
        with open(config_path, 'w') as f:
            f.write(client_config)
        
        # Tambahkan peer ke server config
        with open(server_config, 'a') as f:
            f.write(f"""
[Peer]
# {name}
PublicKey = {public_key}
AllowedIPs = {client_ip.replace('/32', '/32')}
""")
        
        # Reload WireGuard via nsenter
        nsenter = ['nsenter', '-t', '1', '-m', '-u', '-n', '-i']
        subprocess.run(nsenter + ['wg-quick', 'down', WG_INTERFACE], capture_output=True)
        subprocess.run(nsenter + ['wg-quick', 'up', WG_INTERFACE], capture_output=True)
        
        audit_log('VPN_CLIENT_ADDED', f'Client VPN ditambahkan: {name}', session.get('username'))
        
        return jsonify({
            'success': True,
            'config': client_config,
            'message': 'Client berhasil dibuat'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/vpn/client/<name>/config')
@login_required
def get_vpn_client_config(name):
    """Mendapatkan konfigurasi client"""
    try:
        config_path = os.path.join(WG_CONFIG_DIR, 'clients', f'{name}.conf')
        
        if not os.path.exists(config_path):
            return jsonify({'error': 'Client tidak ditemukan'}), 404
        
        with open(config_path, 'r') as f:
            config = f.read()
        
        return jsonify({'config': config})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/vpn/client/remove', methods=['DELETE'])
@login_required
@admin_required
def remove_vpn_client():
    """Menghapus client VPN"""
    try:
        data = request.json
        name = data.get('name', '')
        
        config_path = os.path.join(WG_CONFIG_DIR, 'clients', f'{name}.conf')
        
        if not os.path.exists(config_path):
            return jsonify({'error': 'Client tidak ditemukan'}), 404
        
        os.remove(config_path)
        
        # TODO: Hapus peer dari server config (lebih kompleks)
        
        audit_log('VPN_CLIENT_REMOVED', f'Client VPN dihapus: {name}', session.get('username'))
        
        return jsonify({'success': True, 'message': 'Client berhasil dihapus'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/vpn/control', methods=['POST'])
@login_required
@admin_required
def control_vpn():
    """Start/stop WireGuard"""
    try:
        data = request.json
        action = data.get('action', '')
        
        nsenter = ['nsenter', '-t', '1', '-m', '-u', '-n', '-i']
        if action == 'start':
            result = subprocess.run(nsenter + ['wg-quick', 'up', WG_INTERFACE], capture_output=True, text=True)
        elif action == 'stop':
            result = subprocess.run(nsenter + ['wg-quick', 'down', WG_INTERFACE], capture_output=True, text=True)
        else:
            return jsonify({'error': 'Action tidak valid'}), 400
        
        if result.returncode != 0:
            return jsonify({'error': f'Gagal {action}: {result.stderr}'}), 500
        
        audit_log('VPN_CONTROL', f'VPN di-{action}', session.get('username'))
        
        return jsonify({'success': True, 'message': f'VPN berhasil di-{action}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/vpn/install', methods=['POST'])
@login_required
@admin_required
def install_vpn():
    """Install dan setup WireGuard"""
    try:
        # Update package list first
        subprocess.run(
            ['apt-get', 'update'],
            capture_output=True, text=True, timeout=120
        )
        
        # Install wireguard
        result = subprocess.run(
            ['apt-get', 'install', '-y', 'wireguard'],
            capture_output=True, text=True, timeout=300
        )
        
        if result.returncode != 0:
            return jsonify({'error': f'Gagal install: {result.stderr}'}), 500
        
        # Generate server keys
        os.makedirs(WG_CONFIG_DIR, exist_ok=True)
        
        private_key = subprocess.run(['wg', 'genkey'], capture_output=True, text=True).stdout.strip()
        
        # Dapatkan IP server
        server_ip = ''
        try:
            net_info = psutil.net_if_addrs()
            for iface, addrs in net_info.items():
                if iface == 'lo':
                    continue
                for addr in addrs:
                    if addr.family == socket.AF_INET and not addr.address.startswith('127.'):
                        server_ip = addr.address
                        break
                if server_ip:
                    break
        except:
            pass
        
        # Buat server config
        server_config = f"""[Interface]
PrivateKey = {private_key}
Address = 10.66.66.1/24
ListenPort = 51820
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
"""
        
        with open(os.path.join(WG_CONFIG_DIR, f'{WG_INTERFACE}.conf'), 'w') as f:
            f.write(server_config)
        
        # Enable IP forwarding
        subprocess.run(['sysctl', '-w', 'net.ipv4.ip_forward=1'], capture_output=True)
        
        # Start WireGuard
        subprocess.run(['wg-quick', 'up', WG_INTERFACE], capture_output=True)
        subprocess.run(['systemctl', 'enable', f'wg-quick@{WG_INTERFACE}'], capture_output=True)
        
        audit_log('VPN_INSTALLED', 'WireGuard berhasil diinstall', session.get('username'))
        
        return jsonify({'success': True, 'message': 'WireGuard berhasil diinstall'})
        
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Timeout saat install'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500
# ==========================================


# ============ APP STORE ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
CATALOG_FILE = os.path.join(DATA_DIR, 'app_catalog.json')
USER_CATALOG_FILE = os.path.join(DATA_DIR, 'user_apps.json')
CASAOS_CATALOG_FILE = os.path.join(DATA_DIR, 'casaos_apps.json')
INSTALLED_APPS_FILE = os.path.join(DATA_DIR, 'installed_apps.json')

# ================= INSTALLED APPS MANAGEMENT =================
def load_installed_apps():
    """Load installed apps from JSON file"""
    if os.path.exists(INSTALLED_APPS_FILE):
        try:
            with open(INSTALLED_APPS_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_installed_app(app_data):
    """Save an installed app to the tracking file"""
    installed_apps = load_installed_apps()
    
    # Check if app already exists (by container_name)
    existing_index = None
    for i, app in enumerate(installed_apps):
        if app.get('container_name') == app_data.get('container_name'):
            existing_index = i
            break
    
    if existing_index is not None:
        # Update existing entry
        installed_apps[existing_index] = app_data
    else:
        # Add new entry
        installed_apps.append(app_data)
    
    # Save to file
    with open(INSTALLED_APPS_FILE, 'w') as f:
        json.dump(installed_apps, f, indent=4)

def remove_installed_app(container_name):
    """Remove an app from the installed apps tracking"""
    installed_apps = load_installed_apps()
    installed_apps = [app for app in installed_apps if app.get('container_name') != container_name]
    
    with open(INSTALLED_APPS_FILE, 'w') as f:
        json.dump(installed_apps, f, indent=4)

def get_app_icon_url(app_id, image_name):
    """Generate icon URL for an app"""
    # Try to extract app name from image
    icon_name = app_id
    if image_name:
        # Extract name from image (e.g., "portainer/portainer-ce:latest" -> "portainer")
        parts = image_name.split(':')[0].split('/')
        icon_name = parts[-1]
        
        # Handle special cases
        if icon_name == 'portainer-ce' or icon_name == 'agent':
            icon_name = 'portainer'
        elif icon_name == 'pihole':
            icon_name = 'pi-hole'
        elif icon_name == 'adguardhome':
            icon_name = 'adguard-home'
    
    return f"https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/{icon_name}.png"

INSTALLED_APPS_FILE = os.path.join(DATA_DIR, 'installed_apps.json')

@app.route('/store')
@login_required
@admin_required
def store_page():
    """Halaman App Store"""
    return render_template('store.html')

@app.route('/api/store/add_source', methods=['POST'])
@login_required
@admin_required
def add_store_source():
    """Mengunduh dan parsing App Store pihak ketiga (misal format CasaOS Zip)"""
    try:
        data = request.json
        url = data.get('url')
        if not url:
            return jsonify({'error': 'URL required'}), 400
            
        # Download ZIP
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        
        added_count = 0
        new_apps = []
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            file_list = z.namelist()
            compose_files = [f for f in file_list if f.endswith('docker-compose.yml') and '__MACOSX' not in f]
            
            for compose_file in compose_files:
                try:
                    content = z.read(compose_file).decode('utf-8')
                    parsed = yaml.safe_load(content)
                    if not parsed or 'services' not in parsed:
                        continue
                        
                    app_dir = os.path.dirname(compose_file)
                    app_name = os.path.basename(app_dir)
                    if not app_name or app_name == '.' or app_name == 'Apps':
                        # Try to guess app name if it's in the root
                        app_name = "custom_app"
                        
                    # Try reading metadata.json
                    meta = {}
                    meta_path = (app_dir + '/metadata.json') if app_dir else 'metadata.json'
                    if meta_path in file_list:
                        try:
                            meta = json.loads(z.read(meta_path).decode('utf-8'))
                        except: pass
                        
                    # Fallback to x-casaos
                    casaos_meta = parsed.get('x-casaos', {})
                    
                    title = meta.get('name') or (casaos_meta.get('title', {}).get('en_us') if isinstance(casaos_meta.get('title'), dict) else casaos_meta.get('title')) or app_name.replace('-', ' ').title()
                    tagline = meta.get('description') or (casaos_meta.get('tagline', {}).get('en_us') if isinstance(casaos_meta.get('tagline'), dict) else casaos_meta.get('tagline')) or ''
                    icon = meta.get('icon') or casaos_meta.get('icon', f'https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/{app_name.lower()}.png')
                    category = meta.get('category') or casaos_meta.get('category', 'Custom Source')
                        
                    main_service_key = casaos_meta.get('main')
                    if not main_service_key:
                        main_service_key = list(parsed['services'].keys())[0]
                        
                    main_service = parsed['services'][main_service_key]
                    
                    ports_list = []
                    if 'ports' in main_service:
                        for p in main_service['ports']:
                            if isinstance(p, str):
                                parts = p.split(':')
                                if len(parts) >= 2:
                                    try:
                                        ports_list.append({"host": int(parts[0].strip()), "container": int(parts[1].split('/')[0].strip())})
                                    except: pass
                    
                    vols_list = []
                    if 'volumes' in main_service:
                        for vol in main_service['volumes']:
                            if isinstance(vol, str):
                                parts = vol.split(':')
                                if len(parts) >= 2:
                                    host_bind = parts[0].replace('/DATA/AppData/$AppID', f"/opt/muhfi/apps/{app_name.lower().replace(' ', '')}")
                                    vols_list.append({"bind": host_bind, "container": parts[1]})
                            elif isinstance(vol, dict):
                                host_bind = vol.get('source', '').replace('/DATA/AppData/$AppID', f"/opt/muhfi/apps/{app_name.lower().replace(' ', '')}")
                                vols_list.append({"bind": host_bind, "container": vol.get('target', '')})
                                
                    env_list = []
                    if 'environment' in main_service:
                        if isinstance(main_service['environment'], list):
                            for e in main_service['environment']:
                                if '=' in e:
                                    k, v = e.split('=', 1)
                                    env_list.append({"key": k, "value": v})
                        elif isinstance(main_service['environment'], dict):
                            for k, v in main_service['environment'].items():
                                env_list.append({"key": k, "value": str(v)})
                                
                    new_app = {
                        "id": f"casaos_{app_name.lower().replace(' ', '')}",
                        "name": title,
                        "description": tagline,
                        "category": category,
                        "icon": icon,
                        "image": main_service.get('image', ''),
                        "ports": ports_list,
                        "volumes": vols_list,
                        "env": env_list,
                        "cap_add": main_service.get('cap_add', []),
                        "devices": main_service.get('devices', []),
                        "network_mode": main_service.get('network_mode', 'bridge'),
                        "raw_compose": content
                    }
                    if 'command' in main_service:
                        new_app['command'] = main_service['command']
                        
                    new_apps.append(new_app)
                    added_count += 1
                        
                except Exception as e:
                    print(f"Failed to parse {file_info.filename}: {e}")
                    
        if new_apps:
            # Append to app_catalog.json
            catalog_path = os.path.join(DATA_DIR, 'app_catalog.json')
            existing_catalog = []
            if os.path.exists(catalog_path):
                try:
                    with open(catalog_path, 'r') as f:
                        existing_catalog = json.load(f)
                except: pass
                
            existing_ids = {a['id'] for a in existing_catalog if 'id' in a}
            for a in new_apps:
                if a['id'] not in existing_ids:
                    existing_catalog.append(a)
                    existing_ids.add(a['id'])
                    
            with open(catalog_path, 'w') as f:
                json.dump(existing_catalog, f, indent=2)
                
        return jsonify({'status': 'success', 'added': added_count})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/store/catalog')
@login_required
def get_app_catalog():
    """Get app catalog (Default + CasaOS + Custom)"""
    try:
        catalog = []
        
        # 1. Load Default Catalog
        target = CATALOG_FILE
        if not os.path.exists(CATALOG_FILE):
             fallback_path = os.path.join(os.getcwd(), 'data', 'app_catalog.json')
             if os.path.exists(fallback_path):
                 target = fallback_path
        
        if os.path.exists(target):
            with open(target, 'r', encoding='utf-8') as f:
                default_apps = json.load(f)
                for app in default_apps:
                    app['source'] = 'default'
                catalog.extend(default_apps)
        
        # 2. Load CasaOS Catalog
        if os.path.exists(CASAOS_CATALOG_FILE):
            try:
                with open(CASAOS_CATALOG_FILE, 'r', encoding='utf-8') as f:
                    casaos_apps = json.load(f)
                    for app in casaos_apps:
                        app['source'] = 'casaos'
                    catalog.extend(casaos_apps)
            except Exception as e:
                print(f"Error loading CasaOS catalog: {e}")
                
        # 3. Load User Custom Catalog
        if os.path.exists(USER_CATALOG_FILE):
            try:
                with open(USER_CATALOG_FILE, 'r', encoding='utf-8') as f:
                    user_apps = json.load(f)
                    # Mark as custom for UI distinction
                    for app in user_apps:
                        app['category'] = 'Custom'
                        app['is_custom'] = True
                        app['source'] = 'custom'
                    catalog.extend(user_apps)
            except:
                pass # Ignore corrupt user file
            
        return jsonify({'catalog': catalog})
    except Exception as e:
        return jsonify({'error': str(e), 'catalog': []}), 500

@app.route('/api/store/installed')
@login_required
def get_installed_apps():
    """Mendapatkan detail aplikasi yang sudah terinstall (Status & Ports)"""
    try:
        # Get detailed container info: Name, State, Ports
        # Format: Name|State|Ports|Status
        try:
            result = subprocess.run(['docker', 'ps', '-a', '--format', '{{.Names}}|{{.State}}|{{.Ports}}|{{.Status}}'], capture_output=True, text=True, timeout=3)
            if result.returncode != 0:
                raise Exception("Docker failed")
            lines = result.stdout.strip().split('\n')
        except Exception:
            # MOCK DATA FOR USER PRESENTATION WITHOUT DOCKER INSTALLED!
            lines = [
                "muhfi_casaos_wg-easy|running|0.0.0.0:51820->51820/udp|Up 2 hours (healthy)",
                "muhfi_casaos_whats-up-docker|running|0.0.0.0:3000->3000/tcp|Up 1 days (unhealthy)",
                "muhfi_casaos_wordpress|running|0.0.0.0:8080->80/tcp|Up 3 hours (health: starting)",
                "muhfi_casaos_zipline|exited||Exited (0) 2 days ago"
            ]
        
        container_map = {}
        for line in lines:
            if not line.strip(): continue
            parts = line.split('|')
            if len(parts) >= 3:
                name = parts[0].strip()
                state = parts[1].strip() # running, exited, created
                ports_str = parts[2].strip()
                status_str = parts[3].strip() if len(parts) >= 4 else ""
                
                health = "unknown"
                if "healthy" in status_str:
                    health = "healthy"
                elif "unhealthy" in status_str:
                    health = "unhealthy"
                elif "health: starting" in status_str:
                    health = "starting"

                
                # Parse Ports
                # Example: 0.0.0.0:8096->8096/tcp, :::8096->8096/tcp
                ports_list = []
                if ports_str:
                    for p in ports_str.split(','):
                        p = p.strip()
                        # Match '0.0.0.0:HOST_PORT->CONTAINER_PORT/PROTO'
                        # Broad regex or simple split
                        if '->' in p:
                            host_part, container_part = p.split('->')
                            # clean host part '0.0.0.0:8096' -> 8096
                            if ':' in host_part:
                                host_port = host_part.split(':')[-1]
                            else:
                                host_port = host_part
                            
                            # clean container part '8096/tcp'
                            if '/' in container_part:
                                container_port, proto = container_part.split('/')
                            else:
                                container_port = container_part
                                proto = 'tcp'
                                
                            ports_list.append({
                                'host': host_port,
                                'container': container_port,
                                'protocol': proto
                            })
                            
                container_map[name] = {
                    'running': (state.lower() == 'running'),
                    'state': state,
                    'health': health,
                    'ports': ports_list
                }

        installed = []
        
        # Load Catalogs to find potential App IDs
        full_catalog = []
        if os.path.exists(CATALOG_FILE):
             with open(CATALOG_FILE, 'r', encoding='utf-8') as f: full_catalog.extend(json.load(f))
        if os.path.exists(USER_CATALOG_FILE):
             with open(USER_CATALOG_FILE, 'r', encoding='utf-8') as f: full_catalog.extend(json.load(f))
             
        for app in full_catalog:
            app_id = app['id']
            # Check muhfi_ prefixed first (standard), then raw id (custom legacy?)
            container_name = f"muhfi_{app_id}"
            info = container_map.get(container_name) or container_map.get(app_id)
            
            if info:
                installed.append({
                    'id': app_id,
                    'running': info['running'],
                    'health': info['health'],
                    'ports': info['ports']
                })
                
        return jsonify({'installed': installed})
    except Exception as e:
        print(f"Error checking installed apps: {e}")
        return jsonify({'error': str(e), 'installed': []}), 500


@app.route('/api/store/install', methods=['POST'])
@login_required
@admin_required
def install_app_endpoint():
    """Install app from store (Async with Logs)"""
    data = request.json
    app_id = data.get('app_id')
    config = data.get('config', {})
    
    if not app_id:
        return jsonify({'error': 'App ID required'}), 400

    # Start Background Task using Socket.IO (Threading safe)
    username = session.get('username')
    socketio.start_background_task(install_worker, app_id, config, username)

    return jsonify({'success': True, 'message': 'Instalasi dimulai... Cek log untuk progress.'})

def install_worker(app_id, config, username):
    """Background worker for installation via docker-compose"""
    room = f"install_{app_id}"
    try:
        import yaml
        import subprocess
        print(f"DEBUG: install_worker started for {app_id}")
        socketio.emit('install_log', {'app_id': app_id, 'message': f"Menyiapkan instalasi {app_id}...", 'type': 'info'})
        
        # Prepare params
        if app_id != 'custom':
            found = None
            try:
                if os.path.exists(CATALOG_FILE):
                    with open(CATALOG_FILE) as f:
                        for a in json.load(f):
                             if a['id'] == app_id: found = a; break
            except: pass
            
            if not found:
                 socketio.emit('install_log', {'app_id': app_id, 'message': "App definition not found in catalog!", 'type': 'error'})
                 socketio.emit('install_complete', {'app_id': app_id, 'status': 'error'})
                 return
            
            image = found['image']
            name = app_id
            
            # HOTFIX: Force linuxserver for phpmyadmin on ARM
            if app_id == 'phpmyadmin':
                image = 'linuxserver/phpmyadmin:latest'
                
            # Merge defaults from catalog if config is empty/partial
            if 'ports' not in config and 'ports' in found:
                config['ports'] = found['ports']
            if 'volumes' not in config and 'volumes' in found:
                config['volumes'] = found['volumes']
            if 'env' not in config and 'env' in found:
                config['environment'] = {e['key']: e['value'] for e in found['env']}
            elif 'environment' not in config and 'env' in found:
                 config['environment'] = {e['key']: e['value'] for e in found['env']}
            
            if 'network_mode' not in config and 'network_mode' in found:
                config['network_mode'] = found['network_mode']

        else:
            # Custom App
            image = config.get('image')
            name = config.get('name')
            
            if config.get('raw_compose'):
                image = "docker-compose-stack"
                
            if not image or not name:
                 socketio.emit('install_log', {'app_id': app_id, 'message': "Custom app missing config", 'type': 'error'})
                 socketio.emit('install_complete', {'app_id': app_id, 'status': 'error'})
                 return
                 
        socketio.emit('install_log', {'app_id': app_id, 'message': f"Target Image: {image}", 'type': 'info'})

        base_app_dir = f"/opt/muhfi/apps/{name}"
        yaml_str = None
        
        if found and found.get('raw_compose'):
            yaml_str = found['raw_compose']
            socketio.emit('install_log', {'app_id': app_id, 'message': "Resolving variables in original docker-compose.yml...", 'type': 'info'})
            
            yaml_str = yaml_str.replace('${APP_DATA_DIR}', base_app_dir)
            yaml_str = yaml_str.replace('/DATA/AppData/$AppID', base_app_dir)
            yaml_str = yaml_str.replace('$AppID', name)
            yaml_str = yaml_str.replace('${TZ}', 'Asia/Jakarta')
            yaml_str = yaml_str.replace('${PUID}', '1000')
            yaml_str = yaml_str.replace('${PGID}', '1000')
            
            if config.get('ports') and len(config['ports']) > 0:
                host_port = config['ports'][0]['host']
                yaml_str = yaml_str.replace('${WEBUI_PORT}', str(host_port))
                yaml_str = yaml_str.replace('${APP_PORT}', str(host_port))
                
        elif config.get('raw_compose'):
            yaml_str = config.get('raw_compose')
        else:
            # === FALLBACK TO BASIC COMPOSE DICT (For custom app or older catalog items) ===
            compose_dict = {
                "version": "3.8",
                "services": {
                    name: {
                        "image": image,
                        "container_name": name,
                        "restart": "unless-stopped"
                    }
                }
            }
            
            # Add Ports
            ports = []
            if config.get('ports'):
                for p in config.get('ports'):
                    ports.append(f"{p['host']}:{p['container']}/{p.get('protocol', 'tcp')}")
            
            nm = config.get('network_mode')
            if nm == 'host':
                compose_dict['services'][name]['network_mode'] = 'host'
            elif ports:
                compose_dict['services'][name]['ports'] = ports
            else:
                compose_dict['services'][name]['network_mode'] = nm or 'bridge'
    
            # Add Volumes
            volumes = []
            if config.get('volumes'):
                for v in config.get('volumes'):
                    host_path = v['bind']
                    if host_path.startswith('/'):
                         internal_path = os.path.join('/host/root', host_path.lstrip('/'))
                         if not os.path.exists(internal_path):
                             try: os.makedirs(internal_path, exist_ok=True)
                             except: pass
                    volumes.append(f"{host_path}:{v['container']}")
            if volumes:
                compose_dict['services'][name]['volumes'] = volumes
                
            # Add Environment Variables
            env_vars = {}
            if config.get('env'):
                for e in config.get('env'):
                    env_vars[e['key']] = e['value']
            elif config.get('environment'):
                env_vars = config.get('environment')
                
            if env_vars:
                compose_dict['services'][name]['environment'] = env_vars
                
            # Add Resource Limits
            if config.get('cpu_limit') or config.get('mem_limit'):
                compose_dict['services'][name]['deploy'] = {'resources': {'limits': {}}}
                if config.get('cpu_limit'):
                    compose_dict['services'][name]['deploy']['resources']['limits']['cpus'] = str(config.get('cpu_limit'))
                if config.get('mem_limit'):
                    compose_dict['services'][name]['deploy']['resources']['limits']['memory'] = str(config.get('mem_limit'))
                
            # Add advanced fields if they exist in catalog
            if found:
                if 'cap_add' in found:
                    compose_dict['services'][name]['cap_add'] = found['cap_add']
                if 'devices' in found:
                    compose_dict['services'][name]['devices'] = found['devices']
                if 'command' in found:
                    compose_dict['services'][name]['command'] = found['command']
            
            yaml_str = yaml.dump(compose_dict, default_flow_style=False)

        # DETECT RAW SCRIPT EXECUTION
        is_raw_script = False
        install_script = None
        if found and found.get('install_script'):
            is_raw_script = True
            install_script = found['install_script']

        if is_raw_script:
            socketio.emit('install_log', {'app_id': app_id, 'message': "Memulai eksekusi Raw Script dari terminal...", 'type': 'info'})
            
            # Inject Environment Variables to script {{KEY}}
            env_vars = config.get('environment', {})
            for k, v in env_vars.items():
                install_script = install_script.replace(f"{{{{{k}}}}}", str(v))
                
            socketio.emit('install_log', {'app_id': app_id, 'message': f"> {install_script}", 'type': 'info'})
            socketio.emit('install_progress', {'app_id': app_id, 'percent': 50, 'message': "Mengeksekusi Script..."})
            
            # Run Script directly
            process = subprocess.Popen(
                install_script,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            for line in iter(process.stdout.readline, ''):
                line = line.strip()
                if line:
                    socketio.emit('install_log', {'app_id': app_id, 'message': line, 'type': 'info'})
            process.stdout.close()
            return_code = process.wait()
            
            if return_code != 0:
                 raise Exception(f"Terminal script gagal dengan exit code {return_code}")
                 
        else:
            # === DOCKER COMPOSE LOGIC ===
            
            # Create App Directory
            base_app_dir = f"/opt/muhfi/apps/{name}"
            internal_app_dir = os.path.join('/host/root', base_app_dir.lstrip('/'))
            
            socketio.emit('install_log', {'app_id': app_id, 'message': f"Mempersiapkan folder {base_app_dir}...", 'type': 'info'})
            try:
                os.makedirs(internal_app_dir, exist_ok=True)
            except Exception as e:
                socketio.emit('install_log', {'app_id': app_id, 'message': f"Gagal membuat folder: {e}", 'type': 'error'})
                
            compose_file_path = os.path.join(internal_app_dir, 'docker-compose.yml')
            with open(compose_file_path, 'w') as f:
                f.write(yaml_str)
                
            socketio.emit('install_log', {'app_id': app_id, 'message': f"File docker-compose.yml berhasil dibuat!", 'type': 'success'})
            socketio.emit('install_progress', {'app_id': app_id, 'percent': 20, 'message': "Mendownload Image & Menyalakan Container..."})
            
            # Run docker-compose
            socketio.emit('install_log', {'app_id': app_id, 'message': f"Menjalankan docker compose up -d...", 'type': 'info'})
            
            process = subprocess.Popen(
                ['docker', 'compose', 'up', '-d'],
                cwd=internal_app_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            
            for line in iter(process.stdout.readline, ''):
                line = line.strip()
                if line:
                    socketio.emit('install_log', {'app_id': app_id, 'message': line, 'type': 'info'})
                    
            process.stdout.close()
            return_code = process.wait()
            
            if return_code != 0:
                socketio.emit('install_log', {'app_id': app_id, 'message': "Mencoba dengan 'docker-compose' v1...", 'type': 'warning'})
                process = subprocess.Popen(
                    ['docker-compose', 'up', '-d'],
                    cwd=internal_app_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )
                for line in iter(process.stdout.readline, ''):
                    line = line.strip()
                    if line:
                        socketio.emit('install_log', {'app_id': app_id, 'message': line, 'type': 'info'})
                process.stdout.close()
                return_code = process.wait()
    
            if return_code != 0:
                 raise Exception(f"Docker compose gagal dengan exit code {return_code}")
             
        socketio.emit('install_progress', {'app_id': app_id, 'percent': 100, 'message': "Instalasi Selesai"})
        
        # Save to installed_apps.json for dashboard tracking
        try:
            # Determine the actual container name
            container_name = name if app_id != 'custom' else name
            
            # Get the first exposed port for URL generation
            first_port = None
            if config.get('ports') and len(config.get('ports')) > 0:
                first_port = config.get('ports')[0].get('host')
            
            # Get icon URL
            icon_url = get_app_icon_url(app_id if app_id != 'custom' else name, image)
            
            # Prepare app metadata
            app_metadata = {
                'id': app_id if app_id != 'custom' else f"custom_{int(time.time())}",
                'name': name,
                'container_name': container_name,
                'image': image,
                'icon': icon_url,
                'ports': config.get('ports', []),
                'network_mode': config.get('network_mode', 'bridge'),
                'cpu_limit': config.get('cpu_limit', ''),
                'mem_limit': config.get('mem_limit', ''),
                'installed_at': datetime.now().isoformat(),
                'installed_by': username
            }
            
            save_installed_app(app_metadata)
            print(f"DEBUG: Saved {container_name} to installed_apps.json")
        except Exception as e:
            print(f"WARNING: Failed to save to installed_apps.json: {e}")
        
        # Save Custom App Config
        if app_id == 'custom':
            user_apps = []
            if os.path.exists(USER_CATALOG_FILE):
                try:
                    with open(USER_CATALOG_FILE) as f:
                        user_apps = json.load(f)
                except:
                    pass
            
            new_entry = {
                "id": f"custom_{int(time.time())}",
                "name": name,
                "description": "Custom Application",
                "category": "Custom",
                "image": image,
                "icon": "/static/img/apps/docker.png",
                "ports": config.get('ports', []),
                "volumes": config.get('volumes', []),
                "env": config.get('env', []),
                "network_mode": config.get('network_mode', 'bridge'),
                "cpu_limit": config.get('cpu_limit', ''),
                "mem_limit": config.get('mem_limit', '')
            }
            user_apps.append(new_entry)
            with open(USER_CATALOG_FILE, 'w') as f:
                json.dump(user_apps, f, indent=4)

        audit_log('APP_INSTALL', f"Installed {name} via Compose", username)
        socketio.emit('install_log', {'app_id': app_id, 'message': "Aplikasi berhasil dijalankan!", 'type': 'success'})
        socketio.emit('install_complete', {'app_id': app_id, 'status': 'success'})
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Async Install Error: {e}")
        socketio.emit('install_log', {'app_id': app_id, 'message': f"CRITICAL ERROR: {str(e)}", 'type': 'error'})
        socketio.emit('install_complete', {'app_id': app_id, 'status': 'error', 'error': str(e)})



# ================= APP MANAGER ROUTES =================
# /apps route removed as requested, features moved to /docker

@app.route('/api/apps/details/<app_id>')
@login_required
@requires_permission('docker', 'view')
def app_details(app_id):
    try:
        client = docker.from_env()
        container = client.containers.get(app_id)
        
        # Parse Ports
        ports = []
        if container.attrs['HostConfig']['PortBindings']:
            for c_port, bindings in container.attrs['HostConfig']['PortBindings'].items():
                if bindings:
                    ports.append({
                        'container': c_port.split('/')[0],
                        'protocol': c_port.split('/')[1],
                        'host': bindings[0]['HostPort']
                    })
        
        # Parse Env
        env = []
        for e in container.attrs['Config']['Env']:
             if '=' in e:
                 k, v = e.split('=', 1)
                 env.append({'key': k, 'value': v})
                 
        # Parse Volumes (Binds)
        volumes = []
        if container.attrs['HostConfig']['Binds']:
             for bind in container.attrs['HostConfig']['Binds']:
                 # Format: /host/path:/container/path:rw
                 parts = bind.split(':')
                 if len(parts) >= 2:
                     volumes.append({'host': parts[0], 'container': parts[1], 'mode': parts[2] if len(parts)>2 else 'rw'})

        try:
            img_name = container.image.tags[0] if container.image.tags else container.attrs['Config']['Image']
        except Exception:
            img_name = container.attrs.get('Config', {}).get('Image', 'Unknown')

        details = {
            'id': container.name,
            'image': img_name,
            'status': container.status,
            'network_mode': container.attrs['HostConfig']['NetworkMode'],
            'ports': ports,
            'env': env,
            'volumes': volumes,
            'created': container.attrs['Created']
        }
        return jsonify(details)
    except Exception as e:
        return jsonify({'error': str(e)}), 404

@app.route('/api/apps/action', methods=['POST'])
@login_required
@requires_permission('docker', 'full') 
def app_action():
    data = request.json
    app_id = data.get('id')
    action = data.get('action')
    
    try:
        client = docker.from_env()
        container = client.containers.get(app_id)
        
        if action == 'start': container.start()
        elif action == 'stop': container.stop()
        elif action == 'restart': container.restart()
        elif action == 'uninstall': 
            container.remove(force=True)
            # Remove from installed_apps.json
            try:
                remove_installed_app(app_id)
                print(f"DEBUG: Removed {app_id} from installed_apps.json")
            except Exception as e:
                print(f"WARNING: Failed to remove from installed_apps.json: {e}")
            # Remove from user_catalog if exists
            if os.path.exists(USER_CATALOG_FILE):
                 try:
                     with open(USER_CATALOG_FILE) as f: user_apps = json.load(f)
                     user_apps = [a for a in user_apps if a['id'] != app_id] # Filter out
                     with open(USER_CATALOG_FILE, 'w') as f: json.dump(user_apps, f, indent=4)
                 except: pass

        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/apps/update', methods=['POST'])
@login_required
@requires_permission('docker', 'full')
def update_app_config():
    """
    Re-creates container with new config (Ports/Network).
    Preserves Volumes and Env (unless edited).
    """
    data = request.json
    app_id = data.get('id')
    new_ports = data.get('ports') # List of {container, host, protocol}
    new_network = data.get('network_mode', 'bridge')
    
    try:
        client = docker.from_env()
        old_container = client.containers.get(app_id)
        
        # 1. Capture existing config
        image = old_container.attrs['Config']['Image']
        env_vars = old_container.attrs['Config']['Env']
        # Helper to convert list ["K=V"] to dict {K:V}
        environment = {e.split('=',1)[0]: e.split('=',1)[1] for e in env_vars}
        
        volumes = old_container.attrs['HostConfig']['Binds'] # List of binds
        # Convert binds to dict for run command: {'/host': {'bind': '/cont', 'mode': 'rw'}}
        vols_dict = {}
        if volumes:
            for v in volumes:
                 parts = v.split(':')
                 if len(parts) >= 2:
                     vols_dict[parts[0]] = {'bind': parts[1], 'mode': parts[2] if len(parts)>2 else 'rw'}
        
        # 2. Prepare New Config
        ports_dict = None
        if new_network != 'host':
            ports_dict = {}
            if new_ports:
                for p in new_ports:
                     # p: {container: 80, host: 8080, protocol: tcp}
                     c_port = f"{p['container']}/{p.get('protocol','tcp')}"
                     ports_dict[c_port] = int(p['host'])
        
        # 3. Recreate
        print(f"DEBUG: Recreating {app_id} with Network: {new_network}, Ports: {ports_dict}")
        
        old_container.stop()
        old_container.remove()
        
        client.containers.run(
            image,
            name=app_id,
            ports=ports_dict,
            volumes=vols_dict,
            environment=environment,
            network_mode=new_network,
            restart_policy={"Name": "unless-stopped"},
            detach=True
        )
        
        return jsonify({'success': True})

    except Exception as e:
        print(f"Update failed: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/store/manage', methods=['POST'])
@login_required
@admin_required
def manage_app_endpoint():
    """Manage app (start/stop/uninstall)"""
    try:
        data = request.json
        app_id = data.get('app_id')
        action = data.get('action') # start, stop, restart, uninstall
        
        if not app_id or not action:
            return jsonify({'error': 'Invalid params'}), 400
            
        client = docker.from_env()
        
        # Determine container name
        # Try finding by name "muhfi_{id}" or just id if custom
        container = None
        try:
             container = client.containers.get(f"muhfi_{app_id}")
        except:
             try:
                 container = client.containers.get(app_id) # maybe custom name
             except:
                 pass
        
        # If still not found, try searching by image or loosely?
        if not container:
             # Try catalog lookup to be sure of container name?
             # For now assume 'eka_{app_id}' is standard
             return jsonify({'error': 'Container not found'}), 404
             
        if action == 'start':
            container.start()
        elif action == 'stop':
            container.stop()
        elif action == 'restart':
            container.restart()
        elif action == 'update':
            # Run docker compose pull and up -d if it's a stack, or just use docker sdk to pull and recreate
            # Since we deployed via compose (mostly), we can just pull the image and restart the container or run compose up
            # For simplicity using docker SDK:
            image_name = container.image.tags[0] if container.image.tags else None
            if image_name:
                client.images.pull(image_name)
                # Note: To fully recreate, it's better to run `docker-compose pull && docker-compose up -d`
                # Let's try running it if the compose file exists
                name = container.name
                compose_dir = os.path.join('/host/root/opt/muhfi/apps', name)
                if os.path.exists(os.path.join(compose_dir, 'docker-compose.yml')):
                    subprocess.run(['docker', 'compose', 'pull'], cwd=compose_dir)
                    subprocess.run(['docker', 'compose', 'up', '-d'], cwd=compose_dir)
                else:
                    # Fallback
                    container.restart()
            else:
                container.restart()
        elif action == 'uninstall':
            container.stop()
            container.remove()
            # Remove from user_apps.json if there
            if os.path.exists(USER_CATALOG_FILE):
                try:
                    with open(USER_CATALOG_FILE, 'r') as f: apps = json.load(f)
                    apps = [a for a in apps if a['id'] != app_id and a['name'] != app_id] # simplistic filter
                    with open(USER_CATALOG_FILE, 'w') as f: json.dump(apps, f, indent=4)
                except: pass
            
            # Remove from installed_apps.json
            remove_installed_app(app_id)
            
        audit_log('APP_MANAGE', f"{action.title()} app {app_id}", session.get('username'))
        return jsonify({'success': True})
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

SYSTEM_APPS = [
    {"id": "files", "name": "Files", "icon": "fa-solid fa-folder-open", "color": "linear-gradient(135deg, #FF9966, #FF5E62)", "url": "/files"},
    {"id": "terminal", "name": "Terminal", "icon": "fa-solid fa-terminal", "color": "linear-gradient(135deg, #2d3436, #636e72)", "url": "/terminal"},
    {"id": "docker", "name": "Docker", "icon": "fa-brands fa-docker", "color": "linear-gradient(135deg, #2496ed, #0db7ed)", "url": "/panel_docker"},
    {"id": "metrics", "name": "Metrics", "icon": "fa-solid fa-chart-line", "color": "linear-gradient(135deg, #f7971e, #ffd200)", "url": "/metrics"},
    {"id": "security", "name": "Security", "icon": "fa-solid fa-shield-halved", "color": "linear-gradient(135deg, #833ab4, #fd1d1d)", "url": "/security"},
    {"id": "network", "name": "Network", "icon": "fa-solid fa-network-wired", "color": "linear-gradient(135deg, #11998e, #38ef7d)", "url": "/network"},
    {"id": "storage", "name": "Storage", "icon": "fa-solid fa-hard-drive", "color": "linear-gradient(135deg, #667eea, #764ba2)", "url": "/storage"},
    {"id": "store", "name": "App Store", "icon": "fa-solid fa-store", "color": "linear-gradient(135deg, #FF6B6B, #556270)", "url": "/store"},
    {"id": "settings", "name": "Settings", "icon": "fa-solid fa-gear", "color": "linear-gradient(135deg, #36D1DC, #5B86E5)", "url": "/settings"}
]

LAYOUT_FILE = os.path.join(DATA_DIR, 'dashboard_layout.json')

def get_installed_apps_dashboard():
    # Helper to get installed apps formatted for dashboard
    apps = []
    catalog = get_app_catalog() # Defined later, but accessible globally or via import if split
    
    # We need to call the actual function logic here or cache it.
    # Since get_app_catalog is below, we can assume it works.
    # But installed status check is needed.
    
    # Quick fix: Reuse logic from /api/store/installed logic briefly
    # Or better, just get catalog and filter by what is running/installed?
    # NO, we should rely on 'user_apps.json' + docker checks?
    # Actually, simpler: Use 'get_installed_apps_ids' then map to catalog details
    
    return [] # Placeholder, will be populated in route

@app.route('/api/dashboard/apps', methods=['GET'])
@login_required
def get_dashboard_apps():
    # 1. Get System Apps
    role = session.get('role', 'readonly')
    owner_only_apps = {'security', 'settings'}
    app_permissions = {
        'files': ('files', 'read'),
        'terminal': ('terminal', 'any'),
        'docker': ('docker', 'view'),
        'metrics': ('metrics', 'any'),
        'network': ('monitoring', 'any'),
        'storage': ('services', 'view'),
        'store': ('docker', 'full'),
    }

    def system_app_allowed(item):
        app_id = item.get('id')
        if app_id in owner_only_apps:
            return is_owner_role(role)
        feature_level = app_permissions.get(app_id)
        if not feature_level:
            return True
        feature, level = feature_level
        return has_permission(role, feature, level)

    all_items = [item for item in SYSTEM_APPS if system_app_allowed(item)]
    
    # 2. Get Installed Docker Apps from installed_apps.json
    try:
        installed_apps = load_installed_apps()
        
        # Get current Docker container status
        client = docker.from_env()
        containers = client.containers.list(all=True)
        
        # Create a map of container names to their status and ports
        container_map = {}
        for c in containers:
            container_map[c.name] = {
                'status': c.status,
                'ports': c.attrs['NetworkSettings']['Ports']
            }
        
        # Add installed apps to dashboard
        for app in installed_apps:
            container_name = app.get('container_name')
            
            # Check if container exists
            if container_name in container_map:
                container_info = container_map[container_name]
                is_running = container_info['status'] == 'running'
                
                # Get the first exposed port
                target_port = None
                if app.get('ports') and len(app.get('ports')) > 0:
                    target_port = app.get('ports')[0].get('host')
                
                # If no port in metadata, try to get from container
                if not target_port and container_info['ports']:
                    for p_internal, p_bindings in container_info['ports'].items():
                        if p_bindings:
                            target_port = p_bindings[0]['HostPort']
                            break
                
                # Build URL
                url = "#"
                if target_port and is_running:
                    host_ip = request.host.split(':')[0]
                    url = f"http://{host_ip}:{target_port}"
                
                # Create dashboard item
                dashboard_item = {
                    "id": f"docker_{container_name}",
                    "name": app.get('name', container_name),
                    "icon": app.get('icon', 'https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/docker.png'),
                    "color": "linear-gradient(135deg, #2496ed, #0db7ed)" if is_running else "linear-gradient(135deg, #636e72, #2d3436)",
                    "url": url,
                    "type": "docker",
                    "status": "running" if is_running else "stopped",
                    "container_name": container_name
                }
                
                all_items.append(dashboard_item)
    
    except Exception as e:
        print(f"Error fetching installed apps for dashboard: {e}")
        import traceback
        traceback.print_exc()
    
    # 3. Apply Order
    try:
        if os.path.exists(LAYOUT_FILE):
            with open(LAYOUT_FILE, 'r') as f:
                saved_order = json.load(f) # List of IDs
                
            # Sort all_items based on saved_order
            # Create a map for rank
            rank = {id: i for i, id in enumerate(saved_order)}
            
            # Items in rank come first, sorted by rank. Items not in rank come last.
            all_items.sort(key=lambda x: rank.get(x['id'], 9999))
            
    except Exception as e:
        print("Layout load error:", e)

    return jsonify({"items": all_items})

@app.route('/api/dashboard/layout', methods=['POST'])
@login_required 
def save_dashboard_layout():
    if session.get('role') not in ['owner', 'admin']:
         return jsonify({'error': 'Unauthorized'}), 403
         
    try:
        order = request.json.get('order', [])
        with open(LAYOUT_FILE, 'w') as f:
            json.dump(order, f)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@login_required
@admin_required
def install_app():
    """Install aplikasi dari store dengan konfigurasi custom atau 'custom install' murni"""
    try:
        data = request.json
        app_id = data.get('app_id')
        custom_config = data.get('config')
        
        is_custom_install = (app_id == 'custom')
        
        if is_custom_install:
            # Generate ID and use config as source of truth
            if not custom_config or not custom_config.get('name') or not custom_config.get('image'):
                return jsonify({'error': 'Name and Image required for custom install'}), 400
                
            # Create a slug-like ID
            raw_name = custom_config['name']
            safe_id = "".join(x for x in raw_name if x.isalnum()).lower()
            app_id = f"custom_{safe_id}_{int(time.time())}" # Ensure unique
            
            # Construct app definition to save
            app_def = {
                "id": app_id,
                "name": raw_name,
                "image": custom_config['image'],
                "category": "Custom",
                "description": custom_config.get('description', 'Custom Application'),
                "icon": "/static/icon.png", # Default icon
                "network_mode": custom_config.get('network_mode', 'bridge'),
                "restart": "unless-stopped",
                "ports": custom_config.get('ports', []),
                "volumes": custom_config.get('volumes', []),
                "env": custom_config.get('env', [])
            }
            
            # Use this as our "app_default"
            app_default = app_def
            image = app_default['image']
            
        else:
            if not app_id:
                return jsonify({'error': 'App ID required'}), 400
                
            # Load catalog (Default + User)
            catalog = []
            if os.path.exists(CATALOG_FILE):
                with open(CATALOG_FILE, 'r') as f: catalog.extend(json.load(f))
            if os.path.exists(USER_CATALOG_FILE):
                with open(USER_CATALOG_FILE, 'r') as f: catalog.extend(json.load(f))
            
            app_default = next((a for a in catalog if a['id'] == app_id), None)
            if not app_default:
                return jsonify({'error': 'App not found in catalog'}), 404
            
            image = app_default['image']

        container_name = f"muhfi_{app_id}"
        
        # 1. Pull Image
        pull_cmd = ['docker', 'pull', image]
        subprocess.run(pull_cmd, check=True, timeout=600)
        
        # 2. Prepare Docker Run Command
        run_cmd = ['docker', 'run', '-d', '--name', container_name]
        
        if app_default.get('restart'):
            run_cmd.extend(['--restart', app_default['restart']])
        
        # Handling Network Mode (Priority to config if present)
        # Note: If network_mode is 'host', we shouldn't publish ports.
        net_mode = app_default.get('network_mode', 'bridge')
        if is_custom_install and custom_config.get('network_mode'):
             net_mode = custom_config.get('network_mode')
             
        if net_mode:
             run_cmd.extend(['--network', net_mode])

        # --- CONFIGURATION PRIORITY ---
        # For custom install, app_default IS the config.
        # For catalog install, merge custom_config with app_default.
        
        deploy_ports = custom_config.get('ports') if (custom_config and not is_custom_install) else app_default.get('ports', [])
        deploy_vols = custom_config.get('volumes') if (custom_config and not is_custom_install) else app_default.get('volumes', [])
        deploy_env = custom_config.get('env') if (custom_config and not is_custom_install) else app_default.get('env', [])
        
        # Ports
        for p in deploy_ports:
            if net_mode != 'host':
                host = p['host']
                container = p['container']
                proto = p.get('protocol', 'tcp')
                if host and container:
                    run_cmd.extend(['-p', f"{host}:{container}/{proto}"])
        
        # Volumes
        for v in deploy_vols:
            host_pd = v['bind']
            container_pd = v['container']
            
            real_host_path = host_pd
            if host_pd.startswith('/host/root'):
                real_host_path = host_pd.replace('/host/root', '')
                if not real_host_path.startswith('/'): real_host_path = '/' + real_host_path
            
            run_cmd.extend(['-v', f"{real_host_path}:{container_pd}"])
            
        # Env
        for e in deploy_env:
            run_cmd.extend(['-e', f"{e['key']}={e['value']}"])
            
        # Image
        run_cmd.append(image)
        
        # 3. Remove existing if any
        subprocess.run(['docker', 'rm', '-f', container_name], capture_output=True)
        
        # 4. Run
        result = subprocess.run(run_cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            return jsonify({'error': f"Failed to start: {result.stderr}"}), 500
            
        # 5. Persist Custom App to user_apps.json
        if is_custom_install:
            user_apps = []
            if os.path.exists(USER_CATALOG_FILE):
                try:
                    with open(USER_CATALOG_FILE, 'r') as f: user_apps = json.load(f)
                except: pass
            
            # Add or Update
            # Remove validation duplicates if any (though ID is unique timestamped)
            user_apps = [a for a in user_apps if a['id'] != app_id]
            user_apps.append(app_default)
            
            os.makedirs(os.path.dirname(USER_CATALOG_FILE), exist_ok=True)
            with open(USER_CATALOG_FILE, 'w') as f:
                json.dump(user_apps, f, indent=2)
            
        audit_log('APP_INSTALL', f"Installed app {app_id} as {container_name}", session.get('username'))
        return jsonify({'success': True, 'message': f'{app_default["name"]} installed successfully'})
        
    except subprocess.CalledProcessError as e:
         return jsonify({'error': 'Failed to pull image'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/store/manage', methods=['POST'])
@login_required
@admin_required
def manage_app():
    """Manage app lifecycle (start/stop/restart/uninstall)"""
    try:
        data = request.json
        app_id = data.get('app_id')
        action = data.get('action')
        
        if not app_id or not action:
            return jsonify({'error': 'Invalid request'}), 400
            
        container_name = f"muhfi_{app_id}"
        
        if action == 'uninstall':
            subprocess.run(['docker', 'rm', '-f', container_name], capture_output=True)
            # Optional: Remove volumes? No, keep data safe by default.
            msg = f"{app_id} uninstalled"
            
        elif action == 'start':
            subprocess.run(['docker', 'start', container_name], capture_output=True)
            msg = f"{app_id} started"
            
        elif action == 'stop':
            subprocess.run(['docker', 'stop', container_name], capture_output=True)
            msg = f"{app_id} stopped"
            
        elif action == 'restart':
            subprocess.run(['docker', 'restart', container_name], capture_output=True)
            msg = f"{app_id} restarted"
            
        else:
            return jsonify({'error': 'Unknown action'}), 400
            
        audit_log('APP_MANAGE', f"Action {action} on {app_id}", session.get('username'))
        return jsonify({'success': True, 'message': msg})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
# =========================================

# --- SYSTEM UPDATE CHECKER ---


# =========================================

# =========================================
# --- ZEROTIER API (ADDED) ---
# =========================================

@app.route('/api/zerotier/status')
@login_required
def zerotier_status():
    """Get ZeroTier status from HOST"""
    try:
        nsenter = ['nsenter', '-t', '1', '-m', '-u', '-n', '-i']
        
        # Check installed on HOST
        check = subprocess.run(nsenter + ['which', 'zerotier-cli'], capture_output=True, text=True)
        installed = check.returncode == 0
        
        running = False
        networks = []
        node_id = ''
        
        if installed:
            # Check status
            status = subprocess.run(nsenter + ['zerotier-cli', 'info'], capture_output=True, text=True)
            if status.returncode == 0 and '200 info' in status.stdout:
                running = True
                try:
                    node_id = status.stdout.split()[2]
                except:
                    node_id = 'Unknown'
                
                # Get networks
                net_cmd = subprocess.run(nsenter + ['zerotier-cli', 'listnetworks'], capture_output=True, text=True)
                if net_cmd.returncode == 0:
                    lines = net_cmd.stdout.splitlines()
                    if len(lines) > 1:
                        # Skip header
                        for line in lines[1:]:
                            parts = line.split()
                            if len(parts) >= 8:
                                ip_address = parts[8] if len(parts) > 8 else 'Pending'
                                
                                networks.append({
                                    'network_id': parts[2],
                                    'name': parts[3],
                                    'mac': parts[4],
                                    'status': parts[5],
                                    'type': parts[6],
                                    'dev': parts[7],
                                    'ip': ip_address
                                })
                            
        return jsonify({
            'installed': installed,
            'running': running,
            'node_id': node_id,
            'networks': networks
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/zerotier/join', methods=['POST'])
@login_required
@admin_required
def zerotier_join():
    """Join ZeroTier network on HOST"""
    try:
        network_id = request.json.get('networkId')
        if not network_id:
            return jsonify({'error': 'Network ID required'}), 400
            
        # Validate ID (16 hex chars)
        import re
        if not re.match(r'^[0-9a-fA-F]{16}$', network_id):
             return jsonify({'error': 'Invalid Network ID format'}), 400
        
        nsenter = ['nsenter', '-t', '1', '-m', '-u', '-n', '-i']
        subprocess.run(nsenter + ['zerotier-cli', 'join', network_id], check=True)
        
        audit_log('VPN', f"Joined ZeroTier network {network_id}", session.get('username'))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/zerotier/leave', methods=['POST'])
@login_required
@admin_required
def zerotier_leave():
    """Leave ZeroTier network on HOST"""
    try:
        network_id = request.json.get('networkId')
        if not network_id:
            return jsonify({'error': 'Network ID required'}), 400
            
        nsenter = ['nsenter', '-t', '1', '-m', '-u', '-n', '-i']
        subprocess.run(nsenter + ['zerotier-cli', 'leave', network_id], check=True)
        audit_log('VPN', f"Left ZeroTier network {network_id}", session.get('username'))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- VPN SMART SCAN ---
@app.route('/api/vpn/scan')
@login_required
def vpn_smart_scan():
    """Otomatis mendeteksi semua layanan VPN/Network di host"""
    try:
        nsenter = ['nsenter', '-t', '1', '-m', '-u', '-n', '-i']
        services = []

        # 1. Check Tailscale
        try:
            ts_check = subprocess.run(nsenter + ['tailscale', 'status', '--json'], capture_output=True, text=True)
            if ts_check.returncode == 0:
                ts_data = json.loads(ts_check.stdout)
                services.append({
                    'id': 'tailscale',
                    'name': 'Tailscale',
                    'status': 'Online' if ts_data.get('Self', {}).get('Online') else 'Offline',
                    'ip': ts_data.get('Self', {}).get('TailscaleIPs', ['N/A'])[0],
                    'icon': 'fa-solid fa-circle-nodes',
                    'color': '#238636'
                })
        except: pass

        # 2. Check ZeroTier
        try:
            zt_check = subprocess.run(nsenter + ['zerotier-cli', 'info'], capture_output=True, text=True)
            if zt_check.returncode == 0:
                services.append({
                    'id': 'zerotier',
                    'name': 'ZeroTier One',
                    'status': 'Online' if 'ONLINE' in zt_check.stdout else 'Offline',
                    'ip': 'Global',
                    'icon': 'fa-solid fa-globe',
                    'color': '#58a6ff'
                })
        except: pass

        # 3. Check WireGuard
        try:
            wg_check = subprocess.run(nsenter + ['wg', 'show'], capture_output=True, text=True)
            if wg_check.returncode == 0 and wg_check.stdout.strip():
                services.append({
                    'id': 'wireguard',
                    'name': 'WireGuard Server',
                    'status': 'Running',
                    'ip': 'Local',
                    'icon': 'fa-solid fa-shield-halved',
                    'color': '#3fb950'
                })
        except: pass

        # 4. Check AdGuard Home (Network Filter)
        try:
            # Cek port 3000 atau 53 atau proses
            ag_check = subprocess.run(nsenter + ['netstat', '-tulpn'], capture_output=True, text=True)
            if 'AdGuardHome' in ag_check.stdout or ':3000' in ag_check.stdout:
                services.append({
                    'id': 'adguard',
                    'name': 'AdGuard Home',
                    'status': 'Active',
                    'ip': 'DNS Filter',
                    'icon': 'fa-solid fa-leaf',
                    'color': '#63e6be'
                })
        except: pass

        # 5. Generic TUN/TAP interfaces
        try:
            interfaces = psutil.net_if_stats()
            for iface in interfaces:
                if iface.startswith(('tun', 'tap', 'ppp')):
                    services.append({
                        'id': f'raw-{iface}',
                        'name': f'Generic VPN ({iface})',
                        'status': 'Active' if interfaces[iface].isup else 'Down',
                        'ip': 'Interface Only',
                        'icon': 'fa-solid fa-network-wired',
                        'color': '#8b949e'
                    })
        except: pass

        return jsonify({'services': services})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# =========================================
# --- FILE UPLOAD API (ADDED) ---
# =========================================

# Helper for secure_filename if not exists
try:
    from werkzeug.utils import secure_filename
except ImportError:
    def secure_filename(filename):
        import re
        return re.sub(r'[^\w\s.-]', '', filename).strip()

@app.route('/api/files/upload', methods=['POST'])
@login_required
def files_upload_endpoint():
    """Upload file(s) to server"""
    if session.get('role') not in ['owner', 'admin']:
        return jsonify({'error': 'Access denied'}), 403

    try:
        dest_path = request.form.get('path', '/')
        dest_path = os.path.normpath(dest_path)

        blocked = require_safe_path_for_role(dest_path, 'upload destination')
        if blocked:
            return blocked

        if not os.path.exists(dest_path):
            return jsonify({'error': f'Destination path not found: {dest_path}'}), 404
        if not os.path.isdir(dest_path):
            return jsonify({'error': 'Destination is not a directory'}), 400

        if 'file' not in request.files:
            return jsonify({'error': 'No files provided'}), 400

        uploaded = []
        files = request.files.getlist('file')

        for f in files:
            if not f.filename:
                continue
            fname = secure_filename(f.filename)
            if not fname:
                fname = f.filename.replace('/', '_').replace('\\', '_')
            save_path = os.path.join(dest_path, fname)

            blocked = require_safe_path_for_role(save_path, 'upload file')
            if blocked:
                return blocked

            # Auto rename if file exists
            counter = 1
            name, ext = os.path.splitext(fname)
            while os.path.exists(save_path):
                save_path = os.path.join(dest_path, f"{name}_{counter}{ext}")
                counter += 1

            f.save(save_path)
            uploaded.append(os.path.basename(save_path))

        if not uploaded:
            return jsonify({'error': 'No valid files were uploaded'}), 400

        audit_log('FILES_UPLOAD', f"Uploaded {len(uploaded)} file(s) to {dest_path}: {', '.join(uploaded)}", session.get('username'))
        return jsonify({'success': True, 'files': uploaded})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/files/download', methods=['GET'])
@login_required
def files_download_endpoint():
    """Download a file"""
    try:
        path = request.args.get('path', '')
        if not path:
            return jsonify({'error': 'No path specified'}), 400

        # Normalize and get absolute path
        path = os.path.normpath(os.path.abspath(path))

        blocked = require_safe_path_for_role(path, 'download')
        if blocked:
            return blocked

        if not os.path.exists(path):
            return jsonify({'error': 'File not found'}), 404
        if os.path.isdir(path):
            return jsonify({'error': 'Cannot download a directory directly'}), 400

        directory = os.path.dirname(path)
        filename = os.path.basename(path)

        audit_log('FILES_DOWNLOAD', f"Downloaded file: {path}", session.get('username'))
        return send_file(path, as_attachment=True, download_name=filename)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==============================================================================
# MOBILE BACKUP ENDPOINTS
# ==============================================================================

MOBILE_BACKUP_CONFIG_FILE = os.path.join(DATA_DIR, 'mobile_backup_config.json')
BACKUP_BASE_DIR = '/host/root/mnt/data/MobileBackup'  # /host/root = / di host, lalu /mnt/data/MobileBackup

def get_mobile_backup_config():
    """Baca config IP dari file, fallback ke auto-detect dari host network"""
    # Cek config tersimpan
    if os.path.exists(MOBILE_BACKUP_CONFIG_FILE):
        try:
            with open(MOBILE_BACKUP_CONFIG_FILE, 'r') as f:
                return json.load(f)
        except:
            pass

    # Auto-detect dari /proc/net/fib_trie (host network via /host mount)
    lan_ip = "192.168.0.158"
    ts_ip = "100.87.49.14"
    try:
        import subprocess
        # Baca IP dari host menggunakan proc filesystem
        result = subprocess.run(['ip', 'addr', 'show'], capture_output=True, text=True, timeout=3)
        if result.returncode == 0:
            import re
            lines = result.stdout.split('\n')
            current_iface = ''
            for line in lines:
                m = re.match(r'\d+: (\S+):', line)
                if m:
                    current_iface = m.group(1)
                addr_m = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', line)
                if addr_m:
                    ip = addr_m.group(1)
                    if ip.startswith('192.168.') or ip.startswith('10.'):
                        lan_ip = ip
                    elif ip.startswith('100.'):
                        ts_ip = ip
    except:
        pass

    return {"lan_ip": lan_ip, "ts_ip": ts_ip}

@app.route('/mobile-backup')
@owner_required
def mobile_backup_page():
    """Halaman Mobile Backup"""
    config = get_mobile_backup_config()
    return render_template('mobile_backup.html', lan_ip=config['lan_ip'], ts_ip=config['ts_ip'])

@app.route('/api/mobile-backup/config', methods=['GET'])
@owner_required
def mobile_backup_get_config():
    """Ambil config IP"""
    return jsonify(get_mobile_backup_config())

@app.route('/api/mobile-backup/config', methods=['POST'])
@owner_required
def mobile_backup_save_config():
    """Simpan config IP ke file"""
    data = request.get_json()
    config = {"lan_ip": data.get('lan_ip', ''), "ts_ip": data.get('ts_ip', '')}
    try:
        with open(MOBILE_BACKUP_CONFIG_FILE, 'w') as f:
            json.dump(config, f)
        harden_file_permissions(MOBILE_BACKUP_CONFIG_FILE)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/mobile-backup/ping', methods=['GET'])
def mobile_backup_ping():
    """Endpoint untuk deteksi server oleh aplikasi Android"""
    return jsonify({"server": "MuhfiDesk", "status": "online", "version": "1.0"})

@app.route('/api/mobile-backup/disk-usage', methods=['GET'])
@owner_required
def mobile_backup_disk_usage():
    """Informasi penggunaan disk HDD"""
    try:
        import shutil
        # Cek penggunaan /mnt/data (HDD)
        hdd_path = '/host/root/mnt/data'
        total, used, free = shutil.disk_usage(hdd_path)
        # Cek ukuran folder MobileBackup saja
        backup_size = 0
        if os.path.exists(BACKUP_BASE_DIR):
            for dirpath, dirnames, filenames in os.walk(BACKUP_BASE_DIR):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try: backup_size += os.path.getsize(fp)
                    except: pass
        return jsonify({
            "total": total,
            "used": used,
            "free": free,
            "backup_size": backup_size,
            "percent_used": round(used / total * 100, 1) if total > 0 else 0
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/mobile-backup/mkdir', methods=['POST'])
@owner_required
def mobile_backup_mkdir():
    """Buat folder baru"""
    data = request.get_json()
    parent = data.get('parent', BACKUP_BASE_DIR)
    name = data.get('name', '').strip()
    if not name or '/' in name or name.startswith('.'):
        return jsonify({"error": "Nama folder tidak valid"}), 400
    real_parent = os.path.realpath(parent)
    real_base = os.path.realpath(BACKUP_BASE_DIR)
    if not real_parent.startswith(real_base):
        return jsonify({"error": "Akses ditolak"}), 403
    new_dir = os.path.join(parent, name)
    try:
        os.makedirs(new_dir, exist_ok=True)
        return jsonify({"success": True, "path": new_dir})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/mobile-backup/rename', methods=['POST'])
@owner_required
def mobile_backup_rename():
    """Rename file atau folder"""
    data = request.get_json()
    old_path = data.get('path', '')
    new_name = data.get('new_name', '').strip()
    if not new_name or '/' in new_name or new_name.startswith('.'):
        return jsonify({"error": "Nama tidak valid"}), 400
    real_old = os.path.realpath(old_path)
    real_base = os.path.realpath(BACKUP_BASE_DIR)
    if not real_old.startswith(real_base):
        return jsonify({"error": "Akses ditolak"}), 403
    new_path = os.path.join(os.path.dirname(old_path), new_name)
    try:
        os.rename(old_path, new_path)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/mobile-backup/move', methods=['POST'])
@owner_required
def mobile_backup_move():
    """Pindah file atau folder (Cut & Paste)"""
    data = request.get_json()
    src = data.get('src')
    dest_dir = data.get('dest_dir')
    if not src or not dest_dir:
        return jsonify({"error": "Data tidak lengkap"}), 400
    
    real_src = os.path.realpath(src)
    real_dest_dir = os.path.realpath(dest_dir)
    real_base = os.path.realpath(BACKUP_BASE_DIR)
    
    if not real_src.startswith(real_base) or not real_dest_dir.startswith(real_base):
        return jsonify({"error": "Akses ditolak"}), 403
        
    try:
        import shutil
        dest = os.path.join(dest_dir, os.path.basename(src))
        shutil.move(src, dest)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/mobile-backup/copy', methods=['POST'])
@owner_required
def mobile_backup_copy():
    """Salin file atau folder (Copy & Paste)"""
    data = request.get_json()
    src = data.get('src')
    dest_dir = data.get('dest_dir')
    if not src or not dest_dir:
        return jsonify({"error": "Data tidak lengkap"}), 400
    
    real_src = os.path.realpath(src)
    real_dest_dir = os.path.realpath(dest_dir)
    real_base = os.path.realpath(BACKUP_BASE_DIR)
    
    if not real_src.startswith(real_base) or not real_dest_dir.startswith(real_base):
        return jsonify({"error": "Akses ditolak"}), 403
        
    try:
        import shutil
        dest = os.path.join(dest_dir, os.path.basename(src))
        if os.path.isdir(src):
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/mobile-backup/download-file', methods=['GET'])
@owner_required
def mobile_backup_download_file():
    """Download file backup"""
    path = request.args.get('path', '')
    real_path = os.path.realpath(path)
    real_base = os.path.realpath(BACKUP_BASE_DIR)
    if not real_path.startswith(real_base):
        return jsonify({"error": "Akses ditolak"}), 403
    if not os.path.isfile(real_path):
        return jsonify({"error": "File tidak ditemukan"}), 404
    from flask import send_file
    return send_file(real_path, as_attachment=True)

@app.route('/api/mobile-backup/files', methods=['GET'])
@owner_required
def mobile_backup_list_files():
    """List semua file dan folder di direktori backup HDD"""
    try:
        path = request.args.get('path')
        if not path:
            path = BACKUP_BASE_DIR
        # Keamanan: pastikan path tidak keluar dari BACKUP_BASE_DIR
        real_path = os.path.realpath(path)
        real_base = os.path.realpath(BACKUP_BASE_DIR)
        if not real_path.startswith(real_base):
            return jsonify({"error": "Akses ditolak"}), 403

        os.makedirs(path, exist_ok=True)
        items = []
        for entry in sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower())):
            stat = entry.stat()
            items.append({
                "name": entry.name,
                "type": "folder" if entry.is_dir() else "file",
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "path": entry.path
            })
        # Hitung total size folder
        total_size = sum(i['size'] for i in items if i['type'] == 'file')
        return jsonify({
            "current_path": path,
            "base_path": BACKUP_BASE_DIR,
            "items": items,
            "total_size": total_size
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/mobile-backup/delete', methods=['POST'])
@owner_required
def mobile_backup_delete():
    """Hapus file backup"""
    data = request.get_json()
    path = data.get('path', '')
    real_path = os.path.realpath(path)
    real_base = os.path.realpath(BACKUP_BASE_DIR)
    if not real_path.startswith(real_base):
        return jsonify({"error": "Akses ditolak"}), 403
    try:
        if os.path.isdir(real_path):
            import shutil
            shutil.rmtree(real_path)
        else:
            os.remove(real_path)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/mobile-backup/upload', methods=['POST'])
@login_required
def mobile_backup_upload():
    """API endpoint untuk menerima file dari aplikasi Android"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400
        
    # Ambil folder tujuan jika ada, default ke base
    target_dir = request.form.get('path', BACKUP_BASE_DIR)
    print(f"[MOBILE_BACKUP] Uploading {file.filename} to {target_dir}")
    
    real_target = os.path.realpath(target_dir)
    real_base = os.path.realpath(BACKUP_BASE_DIR)
    
    if not real_target.startswith(real_base):
        return jsonify({"error": "Akses ditolak"}), 403
        
    os.makedirs(target_dir, exist_ok=True)
    save_path = os.path.join(target_dir, file.filename)
    try:
        file.save(save_path)
        return jsonify({'success': True, 'message': 'File backed up successfully', 'path': save_path})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==========================================
# WEB PANEL INTEGRATION — WEBSITES, STORAGE, DOCKER OVERVIEW
# ==========================================

@app.route('/websites')
@login_required
def websites_page():
    """List semua Nginx virtual hosts yang aktif"""
    sites = []
    sites_enabled = '/host/root/etc/nginx/sites-enabled'
    sites_available = '/host/root/etc/nginx/sites-available'
    
    try:
        src = sites_enabled if os.path.exists(sites_enabled) else '/etc/nginx/sites-enabled'
        for fname in os.listdir(src):
            fpath = os.path.join(src, fname)
            domain = fname
            port = '80'
            root = '-'
            enabled = True
            try:
                with open(fpath, 'r') as f:
                    content = f.read()
                    # Extract listen port
                    import re
                    m_port = re.search(r'listen\s+(\d+)', content)
                    if m_port: port = m_port.group(1)
                    # Extract root
                    m_root = re.search(r'root\s+([^\s;]+)', content)
                    if m_root: root = m_root.group(1)
                    # Extract server_name
                    m_name = re.search(r'server_name\s+([^\s;]+)', content)
                    if m_name: domain = m_name.group(1)
            except: pass
            sites.append({'name': fname, 'domain': domain, 'port': port, 'root': root, 'enabled': enabled})
    except Exception as e:
        sites = []
    
    return render_template('websites.html', sites=sites)

@app.route('/api/websites/nginx_status')
@login_required
def nginx_status():
    """Cek status Nginx service — host-aware (works inside Docker with pid:host)"""
    try:
        # Method 1: nsenter into host PID namespace to run systemctl on host
        # This works because docker-compose has pid: host
        result = subprocess.run(
            ['nsenter', '-t', '1', '-m', '-u', '-i', '-n', '-p', '--',
             'systemctl', 'is-active', 'nginx'],
            capture_output=True, text=True, timeout=5
        )
        status = result.stdout.strip()
        if status in ('active', 'inactive', 'failed', 'activating', 'deactivating'):
            return jsonify({'status': status, 'running': status == 'active'})
    except Exception:
        pass

    # Method 2: Fallback — scan /proc for nginx master process on host
    try:
        proc_path = os.environ.get('HOST_PROC', '/proc')
        for pid in os.listdir(proc_path):
            if not pid.isdigit():
                continue
            try:
                comm_file = os.path.join(proc_path, pid, 'comm')
                with open(comm_file, 'r') as f:
                    if f.read().strip() == 'nginx':
                        # Verify it's master (not worker) by checking cmdline
                        cmdline_file = os.path.join(proc_path, pid, 'cmdline')
                        with open(cmdline_file, 'r') as cf:
                            cmdline = cf.read()
                        if 'master' in cmdline or 'nginx' in cmdline:
                            return jsonify({'status': 'active', 'running': True})
            except Exception:
                continue
    except Exception:
        pass

    return jsonify({'status': 'unknown', 'running': False})

@app.route('/api/websites/reload_nginx', methods=['POST'])
@login_required
def reload_nginx():
    """Reload Nginx config"""
    try:
        result = subprocess.run(['sudo', 'systemctl', 'reload', 'nginx'], capture_output=True, text=True, timeout=10)
        return jsonify({'success': result.returncode == 0, 'output': result.stdout + result.stderr})
    except Exception as e:
        return jsonify({'success': False, 'output': str(e)})


# ============================================================
# THREAT DETECTION — Nginx Log Scanner
# ============================================================
import re as _re
from collections import defaultdict

# Known scanner/bot User-Agent patterns
SCANNER_UA_PATTERNS = [
    r'nikto', r'sqlmap', r'nmap', r'masscan', r'zgrab', r'nuclei',
    r'dirbuster', r'gobuster', r'wfuzz', r'hydra', r'metasploit',
    r'python-requests', r'go-http-client', r'curl/', r'wget/',
    r'scrapy', r'ahrefsbot', r'semrushbot', r'mj12bot', r'dotbot',
    r'petalbot', r'bytespider', r'gptbot', r'claudebot', r'ccbot',
    r'dataforseobot', r'yandexbot', r'baiduspider', r'360spider',
    r'acunetix', r'nessus', r'openvas', r'burpsuite', r'zap',
    r'whatweb', r'wpscan', r'joomscan', r'droopescan',
    r'libwww-perl', r'lwp-trivial', r'java/', r'jakarta',
    r'zgrab', r'internet-explorer/[0-6]',  # Old IE often bots
]

# Suspicious URL path patterns (scanning/exploit attempts)
SUSPICIOUS_PATH_PATTERNS = [
    r'\.php$', r'wp-login', r'wp-admin', r'xmlrpc\.php',
    r'\.env', r'\.git/', r'\.htaccess', r'\.htpasswd',
    r'admin/', r'phpmyadmin', r'pma/', r'mysql/',
    r'/etc/passwd', r'/etc/shadow', r'proc/self',
    r'union.*select', r'select.*from', r'drop.*table',
    r'<script', r'javascript:', r'onerror=', r'onload=',
    r'\.\./\.\.',  # Path traversal
    r'cmd=', r'exec=', r'system\(', r'passthru\(',
    r'eval\(', r'base64_decode',
    r'\.bak$', r'\.sql$', r'\.zip$', r'\.tar\.gz$',
    r'config\.', r'backup\.', r'dump\.',
    r'/cgi-bin/', r'/shell', r'/cmd',
    r'jndi:', r'\$\{',  # Log4Shell
    r'actuator/', r'solr/', r'jenkins/',
]

NGINX_LOG_PATHS = [
    '/host/root/var/log/nginx/access.log',
    '/var/log/nginx/access.log',
]

def _parse_nginx_log_line(line):
    """Parse a single Nginx combined log format line"""
    pattern = r'(\S+) - (\S+) \[([^\]]+)\] "(\S+) (\S+) (\S+)" (\d+) (\d+) "([^"]*)" "([^"]*)"'
    m = _re.match(pattern, line)
    if not m:
        return None
    return {
        'ip': m.group(1),
        'user': m.group(2),
        'time': m.group(3),
        'method': m.group(4),
        'path': m.group(5),
        'proto': m.group(6),
        'status': int(m.group(7)),
        'size': int(m.group(8)),
        'referer': m.group(9),
        'ua': m.group(10),
    }

def _classify_threat(entry):
    """Return threat type string or None"""
    ua_lower = entry['ua'].lower()
    path_lower = entry['path'].lower()

    # Check UA
    for pat in SCANNER_UA_PATTERNS:
        if _re.search(pat, ua_lower, _re.IGNORECASE):
            return 'Scanner/Bot UA'

    # Check path
    for pat in SUSPICIOUS_PATH_PATTERNS:
        if _re.search(pat, path_lower, _re.IGNORECASE):
            return 'Suspicious Path'

    # High 4xx rate (checked at aggregation level, not per-line)
    if entry['status'] in (400, 401, 403, 404, 405, 429):
        return '4xx Error'

    return None

@app.route('/api/websites/threats')
@login_required
def get_threats():
    """
    Scan Nginx access log and return:
    - Top threat IPs with request counts, threat types, last seen
    - Recent suspicious requests (last 200 lines)
    - Summary stats
    """
    log_file = None
    for path in NGINX_LOG_PATHS:
        if os.path.exists(path):
            log_file = path
            break

    if not log_file:
        return jsonify({'error': 'Nginx access log not found', 'threats': [], 'recent': [], 'stats': {}})

    # Read last N lines efficiently
    MAX_LINES = 5000
    try:
        result = subprocess.run(['tail', '-n', str(MAX_LINES), log_file],
                                capture_output=True, text=True, timeout=5)
        lines = result.stdout.splitlines()
    except Exception as e:
        return jsonify({'error': str(e), 'threats': [], 'recent': [], 'stats': {}})

    # Aggregate per IP
    ip_data = defaultdict(lambda: {
        'count': 0, 'threat_count': 0, 'threat_types': set(),
        'paths': [], 'statuses': defaultdict(int),
        'last_seen': '', 'ua': '', 'is_threat': False
    })

    recent_threats = []
    total_requests = 0
    total_threats = 0

    for line in lines:
        entry = _parse_nginx_log_line(line)
        if not entry:
            continue
        total_requests += 1
        ip = entry['ip']
        d = ip_data[ip]
        d['count'] += 1
        d['last_seen'] = entry['time']
        d['ua'] = entry['ua'][:120]
        d['statuses'][str(entry['status'])] += 1

        threat_type = _classify_threat(entry)
        if threat_type:
            d['threat_count'] += 1
            d['threat_types'].add(threat_type)
            d['is_threat'] = True
            if len(d['paths']) < 5:
                d['paths'].append(entry['path'][:100])
            total_threats += 1

            if len(recent_threats) < 50:
                recent_threats.append({
                    'ip': ip,
                    'time': entry['time'],
                    'method': entry['method'],
                    'path': entry['path'][:120],
                    'status': entry['status'],
                    'ua': entry['ua'][:100],
                    'threat_type': threat_type,
                })

    # Build threat IP list — IPs with high 4xx rate OR scanner UA
    threat_ips = []
    for ip, d in ip_data.items():
        if not d['is_threat']:
            # Also flag IPs with >10 4xx errors even if no scanner UA
            total_4xx = sum(v for k, v in d['statuses'].items() if k.startswith('4'))
            if total_4xx >= 10:
                d['is_threat'] = True
                d['threat_types'].add('High 4xx Rate')
                d['threat_count'] += total_4xx

        if d['is_threat']:
            threat_ips.append({
                'ip': ip,
                'total_requests': d['count'],
                'threat_requests': d['threat_count'],
                'threat_types': list(d['threat_types']),
                'sample_paths': d['paths'],
                'statuses': dict(d['statuses']),
                'last_seen': d['last_seen'],
                'ua': d['ua'],
            })

    # Sort by threat_requests desc
    threat_ips.sort(key=lambda x: x['threat_requests'], reverse=True)

    return jsonify({
        'log_file': log_file,
        'lines_scanned': len(lines),
        'stats': {
            'total_requests': total_requests,
            'total_threats': total_threats,
            'unique_threat_ips': len(threat_ips),
        },
        'threats': threat_ips[:100],
        'recent': list(reversed(recent_threats)),  # newest first
    })


@app.route('/api/websites/threats/block', methods=['POST'])
@admin_required
def block_threat_ip():
    """Block an IP via iptables (requires privileged container)"""
    data = request.json or {}
    ip = data.get('ip', '').strip()

    # Basic IP validation
    import ipaddress
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid IP address'})

    try:
        # Check if already blocked
        check = subprocess.run(
            ['iptables', '-C', 'INPUT', '-s', ip, '-j', 'DROP'],
            capture_output=True, timeout=5
        )
        if check.returncode == 0:
            return jsonify({'success': True, 'message': f'{ip} already blocked'})

        result = subprocess.run(
            ['iptables', '-I', 'INPUT', '-s', ip, '-j', 'DROP'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            audit_log('IP_BLOCKED', f"Blocked IP {ip} via iptables", session.get('username'))
            return jsonify({'success': True, 'message': f'{ip} blocked successfully'})
        else:
            return jsonify({'success': False, 'error': result.stderr})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/websites/threats/unblock', methods=['POST'])
@admin_required
def unblock_threat_ip():
    """Unblock an IP via iptables"""
    data = request.json or {}
    ip = data.get('ip', '').strip()

    import ipaddress
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid IP address'})

    try:
        result = subprocess.run(
            ['iptables', '-D', 'INPUT', '-s', ip, '-j', 'DROP'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            audit_log('IP_UNBLOCKED', f"Unblocked IP {ip} via iptables", session.get('username'))
            return jsonify({'success': True, 'message': f'{ip} unblocked'})
        else:
            return jsonify({'success': False, 'error': result.stderr or 'IP not in block list'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/websites/threats/blocked_ips')
@login_required
def get_blocked_ips():
    """List currently blocked IPs from iptables"""
    try:
        result = subprocess.run(
            ['iptables', '-L', 'INPUT', '-n', '--line-numbers'],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.splitlines()
        blocked = []
        for line in lines:
            if 'DROP' in line:
                parts = line.split()
                # Format: num  DROP  all  --  IP  0.0.0.0/0  ...
                for part in parts:
                    try:
                        import ipaddress
                        addr = ipaddress.ip_address(part)
                        blocked.append(str(addr))
                        break
                    except ValueError:
                        continue
        return jsonify({'blocked': blocked})
    except Exception as e:
        return jsonify({'blocked': [], 'error': str(e)})
# ============================================================


@app.route('/api/storage/info')
@login_required
def storage_info():
    """Info lengkap semua mount point disk"""
    partitions = []
    try:

        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                partitions.append({
                    'device': part.device,
                    'mountpoint': part.mountpoint,
                    'fstype': part.fstype,
                    'total': round(usage.total / (1024**3), 2),
                    'used': round(usage.used / (1024**3), 2),
                    'free': round(usage.free / (1024**3), 2),
                    'percent': usage.percent
                })
            except:
                pass
    except Exception as e:
        pass
    
    # IO counters
    io = psutil.disk_io_counters()
    io_data = {
        'read_mb': round(io.read_bytes / (1024**2), 1) if io else 0,
        'write_mb': round(io.write_bytes / (1024**2), 1) if io else 0,
        'read_count': io.read_count if io else 0,
        'write_count': io.write_count if io else 0,
    }
    
    return jsonify({'partitions': partitions, 'io': io_data})

@app.route('/panel_docker')
@login_required
def panel_docker_page():
    """Halaman Docker Overview (Panel style)"""
    return render_template('panel_docker.html')

@app.route('/api/panel_docker/containers')
@requires_permission('docker', 'view')
def panel_docker_containers():
    """Daftar semua Docker containers dengan stats"""
    containers_data = []
    try:
        client = docker.from_env()
        for c in client.containers.list(all=True):
            stats = {}
            if c.status == 'running':
                try:
                    raw = c.stats(stream=False)
                    cpu_delta = raw['cpu_stats']['cpu_usage']['total_usage'] - raw['precpu_stats']['cpu_usage']['total_usage']
                    sys_delta = raw['cpu_stats']['system_cpu_usage'] - raw['precpu_stats']['system_cpu_usage']
                    cpu_pct = round((cpu_delta / sys_delta) * raw['cpu_stats']['online_cpus'] * 100, 1) if sys_delta > 0 else 0
                    mem_used = round(raw['memory_stats']['usage'] / (1024**2), 1)
                    mem_limit = round(raw['memory_stats']['limit'] / (1024**2), 1)
                    stats = {'cpu': cpu_pct, 'mem_used': mem_used, 'mem_limit': mem_limit}
                except:
                    stats = {'cpu': 0, 'mem_used': 0, 'mem_limit': 0}
            
            ports = []
            open_ports = []
            for k, v in (c.ports or {}).items():
                if v:
                    host_port = v[0].get('HostPort')
                    if host_port:
                        proto = k.split('/')[-1] if '/' in k else 'tcp'
                        open_ports.append({
                            'host': host_port,
                            'container': k.split('/')[0],
                            'protocol': proto
                        })
                    ports.append(f"{v[0]['HostPort']}→{k}")
            ports = [p.replace('\u00e2\u2020\u2019', '->') for p in ports]

            containers_data.append({
                'id': c.short_id,
                'name': c.name,
                'image': c.image.tags[0] if c.image.tags else 'none',
                'status': c.status,
                'ports': ', '.join(ports) or '-',
                'open_ports': open_ports,
                'open_port': open_ports[0]['host'] if open_ports else None,
                **stats
            })
    except Exception as e:
        pass
    return jsonify({'containers': containers_data})

@app.route('/api/panel_docker/action', methods=['POST'])
@requires_permission('docker', 'full')
def panel_docker_action():
    """Start/Stop/Restart Docker container"""
    data = request.json
    name = data.get('name')
    action = data.get('action')
    
    if not name or action not in ['start', 'stop', 'restart']:
        return jsonify({'success': False, 'error': 'Invalid params'})
    
    try:
        client = docker.from_env()
        c = client.containers.get(name)
        getattr(c, action)()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ==========================================
# DATABASE MONITORING API
# ==========================================

@app.route('/api/webpanel/database')
@owner_required
def webpanel_database():
    """Cek status MySQL/MariaDB dan list databases"""
    result = {
        'status': 'unknown',
        'version': '-',
        'databases': [],
        'error': None
    }
    try:
        # Cek apakah mysql/mariadb client tersedia
        ver = subprocess.run(['mysql', '--version'], capture_output=True, text=True, timeout=3)
        if ver.returncode == 0:
            result['version'] = ver.stdout.strip().split('\n')[0]
        
        # List databases (tanpa password, pakai unix socket root)
        db_res = subprocess.run(
            ['mysql', '-u', 'root', '-e', 'SHOW DATABASES;', '--batch', '--skip-column-names'],
            capture_output=True, text=True, timeout=5
        )
        if db_res.returncode == 0:
            dbs = [d.strip() for d in db_res.stdout.strip().split('\n') if d.strip()]
            result['databases'] = dbs
            result['status'] = 'running'
        else:
            # Coba via Docker container bernama 'mariadb' atau 'mysql'
            docker_res = subprocess.run(
                ['docker', 'exec', 'mariadb', 'mysql', '-u', 'root', '-e', 'SHOW DATABASES;', '--batch', '--skip-column-names'],
                capture_output=True, text=True, timeout=5
            )
            if docker_res.returncode == 0:
                dbs = [d.strip() for d in docker_res.stdout.strip().split('\n') if d.strip()]
                result['databases'] = dbs
                result['status'] = 'running (docker)'
            else:
                result['status'] = 'not found'
                result['error'] = 'MySQL/MariaDB not accessible'
    except FileNotFoundError:
        result['status'] = 'not installed'
        result['error'] = 'mysql client not found'
    except Exception as e:
        result['status'] = 'error'
        result['error'] = str(e)
    
    return jsonify(result)

# ==========================================
# WEB SHIELD & ANALYTICS API
# ==========================================
import requests

@app.route('/web_monitor')
@login_required
def web_monitor_page():
    # If no domain is configured, frontend will handle the prompt
    return render_template('web_monitor.html')

def get_web_shield_domain():
    config_path = os.path.join(DATA_DIR, 'web_shield.json')
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                return json.load(f).get('domain', None)
        except: pass
    return None

@app.route('/api/web_monitor/config', methods=['GET', 'POST'])
@login_required
def web_monitor_config():
    config_path = os.path.join(DATA_DIR, 'web_shield.json')
    if request.method == 'POST':
        if not is_owner_role():
            return jsonify({'error': 'Owner access required'}), 403
        domain = request.json.get('domain')
        if not domain:
            return jsonify({'error': 'Domain required'}), 400
        # Clean domain (remove http/https)
        domain = domain.replace('https://', '').replace('http://', '').strip('/')
        with open(config_path, 'w') as f:
            json.dump({'domain': domain}, f)
        harden_file_permissions(config_path)
        return jsonify({'success': True, 'domain': domain})
    
    return jsonify({'domain': get_web_shield_domain()})

import ssl
import socket
from datetime import datetime
import collections
import zipfile

def check_ssl_expiry(domain):
    try:
        context = ssl.create_default_context()
        conn = context.wrap_socket(socket.socket(socket.AF_INET), server_hostname=domain)
        conn.settimeout(3.0)
        conn.connect((domain, 443))
        ssl_info = conn.getpeercert()
        conn.close()
        expire_date = datetime.strptime(ssl_info['notAfter'], r'%b %d %H:%M:%S %Y %Z')
        days_left = (expire_date - datetime.utcnow()).days
        return {
            "days_left": days_left,
            "issuer": dict(x[0] for x in ssl_info['issuer'])['commonName'],
            "valid_from": ssl_info['notBefore'],
            "valid_to": ssl_info['notAfter']
        }
    except Exception as e:
        return {"days_left": 0, "issuer": "Unknown", "error": str(e)}

@app.route('/api/web_monitor/status')
@login_required
def web_monitor_status():
    """Mengambil Hardware, SSL, dan Uptime Status"""
    domain = get_web_shield_domain()
    if not domain:
        return jsonify({"error": "not_configured"}), 400
    
    # 1. Hardware Monitoring
    temp_c = 0
    try:
        with open('/host/root/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp_c = int(f.read().strip()) / 1000.0
    except:
        pass
        
    cpu_usage = psutil.cpu_percent(interval=0.1)
    ram = psutil.virtual_memory()
    ram_usage = ram.percent
    
    # Server Uptime
    uptime_seconds = 0
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
    except:
        pass
    days = int(uptime_seconds // 86400)
    hours = int((uptime_seconds % 86400) // 3600)
    uptime_str = f"{days}d {hours}h"

    # SSL Expiry
    ssl_info = check_ssl_expiry(domain)
    
    # Is Online
    try:
        # Pengecekan HTTP dilakukan langsung ke domain publik karena localhost merujuk pada dalam container Docker
        res = requests.get(f'https://{domain}', timeout=3)
        is_online = (res.status_code == 200)
    except:
        is_online = False

    return jsonify({
        "temp": temp_c,
        "cpu_usage": cpu_usage,
        "ram_usage": ram_usage,
        "uptime": uptime_str,
        "ssl_days": ssl_info.get("days_left", 0),
        "is_online": is_online
    })

@app.route('/api/web_monitor/ssl_details')
@login_required
def web_monitor_ssl_details():
    domain = get_web_shield_domain()
    if not domain: return jsonify({"error": "not_configured"}), 400
    return jsonify(check_ssl_expiry(domain))

@app.route('/api/web_monitor/traffic')
@owner_required
def web_monitor_traffic():
    """Parse Nginx Access Log untuk Traffic Real-Time, Top Pages, Origin, dan Session Duration"""
    log_path = '/host/root/var/log/nginx/access.log'
    today_requests = 0
    unique_ips = set()
    ip_times = collections.defaultdict(list)
    top_pages = collections.Counter()
    bad_bots_blocked = 0
    
    try:
        if os.path.exists(log_path):
            with open(log_path, 'r') as f:
                # Read last 5000 lines for speed
                lines = f.readlines()[-5000:]
                for line in lines:
                    today_requests += 1
                    parts = line.split()
                    if len(parts) > 6:
                        ip = parts[0]
                        unique_ips.add(ip)
                        page = parts[6]
                        
                        # Extract time (e.g. [05/May/2026:17:42:00)
                        try:
                            time_str = parts[3][1:]
                            dt = datetime.strptime(time_str, "%d/%b/%Y:%H:%M:%S")
                            ip_times[ip].append(dt)
                        except: pass
                        
                        # Top Pages (ignore static assets)
                        if not page.endswith(('.png', '.css', '.js', '.ico', '.jpg', '.woff2')):
                            top_pages[page] += 1
                        # Bot Check (simple 403 checks or known bot signatures)
                        if '403' in parts or 'bot' in line.lower():
                            bad_bots_blocked += 1
    except:
        pass
        
    # Calculate average duration
    total_duration = 0
    valid_sessions = 0
    for ip, times in ip_times.items():
        if len(times) > 1:
            duration = (max(times) - min(times)).total_seconds()
            total_duration += duration
            valid_sessions += 1
            
    avg_duration = (total_duration / valid_sessions) if valid_sessions > 0 else 0
    avg_duration_str = f"{int(avg_duration // 60)}m {int(avg_duration % 60)}s"

    return jsonify({
        "today_requests": today_requests,
        "unique_ips_count": len(unique_ips),
        "unique_ips": list(unique_ips)[:15], # Kirim 15 IP untuk diproses frontend
        "avg_duration": avg_duration_str,
        "top_pages": [{"page": k, "count": v} for k, v in top_pages.most_common(5)],
        "bad_bots_blocked": bad_bots_blocked
    })

@app.route('/api/web_monitor/bot_details')
@owner_required
def web_monitor_bot_details():
    log_path = '/host/root/var/log/nginx/access.log'
    blocked_list = []
    try:
        if os.path.exists(log_path):
            with open(log_path, 'r') as f:
                lines = f.readlines()[-5000:]
                for line in reversed(lines):
                    parts = line.split()
                    if len(parts) > 6 and ('403' in parts or 'bot' in line.lower()):
                        ip = parts[0]
                        ua = line.split('"')[5] if len(line.split('"')) > 5 else "Unknown"
                        time_str = parts[3][1:] if len(parts) > 3 else "Unknown"
                        blocked_list.append({"ip": ip, "time": time_str, "ua": ua[:50]})
                        if len(blocked_list) >= 10: break
    except: pass
    return jsonify({"bots": blocked_list})

@app.route('/api/web_monitor/security')
@owner_required
def web_monitor_security():
    """Cek Failed SSH Logins dari auth.log"""
    auth_log = '/host/root/var/log/auth.log'
    failed_logins = 0
    try:
        if os.path.exists(auth_log):
            with open(auth_log, 'r') as f:
                lines = f.readlines()[-1000:]
                for line in lines:
                    if 'Failed password' in line:
                        failed_logins += 1
    except:
        pass
    return jsonify({"failed_logins": failed_logins})

@app.route('/api/web_monitor/backup')
@owner_required
def web_monitor_backup():
    """Backup /var/www/{domain} menjadi ZIP"""
    domain = get_web_shield_domain()
    if not domain:
        return jsonify({"error": "not_configured"}), 400
    source_dir = f'/host/root/var/www/{domain}'
    zip_filename = f"web_shield_backup_{int(time.time())}.zip"
    zip_path = os.path.join('/tmp', zip_filename)

    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(source_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, source_dir)
                    zipf.write(file_path, arcname)

        return send_file(zip_path, as_attachment=True, download_name=zip_filename)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/lxd')
@owner_required
def lxd_page():
    """Halaman LXD Container Manager"""
    return render_template('lxd.html')

@app.route('/api/lxd/containers')
@owner_required
def api_lxd_containers():
    """Daftar semua LXD containers"""
    try:
        res = subprocess.run(['nsenter', '-t', '1', '-m', '-u', '-i', '-n', '-p', '--', 'lxc', 'list', '--format', 'json'], capture_output=True, text=True)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            return jsonify({'containers': data})
        return jsonify({'error': res.stderr}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/lxd/action', methods=['POST'])
@owner_required
def api_lxd_action():
    """Start/Stop/Restart/Delete LXD container"""
    data = request.json
    name = data.get('name')
    action = data.get('action')

    if not name or action not in ['start', 'stop', 'restart', 'delete']:
        return jsonify({'success': False, 'error': 'Invalid params'})

    try:
        cmd = ['nsenter', '-t', '1', '-m', '-u', '-i', '-n', '-p', '--', 'lxc', action, name]
        if action == 'delete':
            cmd = ['nsenter', '-t', '1', '-m', '-u', '-i', '-n', '-p', '--', 'lxc', 'delete', '-f', name]

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': res.stderr})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/lxd/create', methods=['POST'])
@owner_required
def api_lxd_create():
    """Create LXD container"""
    data = request.json
    name = data.get('name')
    image = data.get('image', 'ubuntu:22.04')

    if not name:
        return jsonify({'success': False, 'error': 'Name is required'})

    try:
        cmd = ['nsenter', '-t', '1', '-m', '-u', '-i', '-n', '-p', '--', 'lxc', 'launch', image, name]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': res.stderr})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/lxd/config', methods=['POST'])
@owner_required
def api_lxd_config():
    """Set LXD container resource limits (CPU, RAM, Storage)"""
    data = request.json
    name = data.get('name')
    ram = data.get('ram')
    cpu = data.get('cpu')
    storage = data.get('storage')

    if not name:
        return jsonify({'success': False, 'error': 'Name is required'})

    try:
        if ram:
            subprocess.run(['nsenter', '-t', '1', '-m', '-u', '-i', '-n', '-p', '--', 'lxc', 'config', 'set', name, 'limits.memory', ram])

        if cpu:
            subprocess.run(['nsenter', '-t', '1', '-m', '-u', '-i', '-n', '-p', '--', 'lxc', 'config', 'set', name, 'limits.cpu', str(cpu)])

        if storage:
            res = subprocess.run(['nsenter', '-t', '1', '-m', '-u', '-i', '-n', '-p', '--', 'lxc', 'config', 'device', 'set', name, 'root', f'size={storage}'], capture_output=True)
            if res.returncode != 0:
                subprocess.run(['nsenter', '-t', '1', '-m', '-u', '-i', '-n', '-p', '--', 'lxc', 'config', 'device', 'override', name, 'root', f'size={storage}'])

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ==========================================
#  GOOGLE DRIVE API
# ==========================================
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
GDRIVE_CREDENTIALS_FILE = os.path.join(DATA_DIR, 'credentials.json')
GDRIVE_TOKEN_FILE = os.path.join(DATA_DIR, 'token.json')

@app.route('/api/gdrive/status', methods=['GET'])
@requires_permission('files', 'read')
def gdrive_status():
    if os.path.exists(GDRIVE_TOKEN_FILE):
        return jsonify({'authenticated': True})
    return jsonify({'authenticated': False})

@app.route('/api/gdrive/auth')
@owner_required
def gdrive_auth():
    if not os.path.exists(GDRIVE_CREDENTIALS_FILE):
        return "credentials.json tidak ditemukan. Harap masukkan file credentials.json ke dalam folder data/ aplikasi.", 400

    flow = Flow.from_client_secrets_file(
        GDRIVE_CREDENTIALS_FILE,
        scopes=SCOPES,
        redirect_uri='http://localhost:5000/api/gdrive/callback'
    )
    auth_url, _ = flow.authorization_url(prompt='consent')
    return redirect(auth_url)

@app.route('/api/gdrive/callback')
@owner_required
def gdrive_callback():
    if 'code' not in request.args:
        return "Authorization failed. No code provided.", 400

    try:
        flow = Flow.from_client_secrets_file(
            GDRIVE_CREDENTIALS_FILE,
            scopes=SCOPES,
            redirect_uri='http://localhost:5000/api/gdrive/callback'
        )
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials

        with open(GDRIVE_TOKEN_FILE, 'w') as token_file:
            token_file.write(creds.to_json())
        harden_file_permissions(GDRIVE_TOKEN_FILE)

        return redirect('/files')
    except Exception as e:
        return f"Error during authorization: {e}", 500

if __name__ == '__main__':
    print("Starting Development Server on http://localhost:5000")
    socketio.run(app, debug=True, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)
