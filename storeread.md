# MuhfiDesk — Store Install Real-time Flow

## Konteks Project

**MuhfiDesk** adalah self-hosted server dashboard berbasis Flask + Socket.IO.
Saya sudah punya:

- `store.html` — frontend lengkap (tidak perlu diubah)
- `catalog.json` — coba kamu cek isinya
- `store_api.py` — blueprint Flask dengan semua route (catalog, install, manage, dll)

---

## Yang Perlu Diselesaikan

Bagian **install real-time** belum berjalan dengan benar. Ada 3 masalah utama:

### Masalah 1 — Socket.IO emit dari background thread tidak sampai ke browser

Saat install dijalankan di background thread, `socketio.emit()` tidak terkirim ke client.

```python
# Di store_api.py
def _install_catalog_app(app):
    from app import socketio  # import ini kadang gagal atau emit tidak sampai
    socketio.emit('install_log', {'message': 'Downloading...', 'type': 'info'})
```

### Masalah 2 — Urutan log yang harus muncul di panel Log

Log harus muncul berurutan seperti ini:

```
📦 Downloading... (nama_app)
⚙️  Installing... (nama_app)
✅ Done installing (nama_app)
```

### Masalah 3 — Setelah install selesai, app belum otomatis muncul di dashboard

Frontend perlu auto-refresh Docker Status dan installed apps tanpa reload halaman.

---

## Struktur Project

```
muhfidesk/
├── app.py              ← Flask app + SocketIO instance
├── store_api.py        ← Blueprint store (sudah ada, perlu diperbaiki)
├── catalog.json        ← 300+ apps
└── templates/
    ├── store.html      ← Frontend store (JANGAN DIUBAH)
    └── dashboard.html  ← Dashboard
```

---

## Kode app.py (struktur dasar)

```python
from flask import Flask
from flask_socketio import SocketIO

app = Flask(__name__)
app.secret_key = 'your-secret-key'
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

from store_api import store_bp
app.register_blueprint(store_bp)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
```

---

## Kode store_api.py (bagian install, perlu diperbaiki)

```python
import threading
import docker
from flask import Blueprint, request, jsonify, session

store_bp = Blueprint('store', __name__)

@store_bp.route('/api/store/install', methods=['POST'])
def install_app():
    role = session.get('role', 'readonly')
    if role not in ('owner', 'admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.json
    app_id = data.get('app_id')
    config = data.get('config', {})

    # Ambil app dari catalog
    catalog = load_catalog()
    app = next((a for a in catalog if a['id'] == app_id), None)
    if not app:
        return jsonify({'error': 'App not found'}), 404

    # Spawn thread — ini yang bermasalah
    thread = threading.Thread(
        target=_install_catalog_app,
        args=(app,),
        daemon=True
    )
    thread.start()

    return jsonify({'status': 'started'})


def _install_catalog_app(app):
    from app import socketio  # ← kadang tidak bisa emit ke client

    def log(msg, type='info'):
        socketio.emit('install_log', {
            'message': msg,
            'type': type,
            'app_id': app['name']
        })

    def progress(pct, msg):
        socketio.emit('install_progress', {'percent': pct, 'message': msg})

    try:
        client = docker.from_env()
        name = app['name']
        image = app['image']
        app_name = app['id'].replace('casaos_', '').replace('_', '-')

        # STEP 1
        log(f'📦 Downloading... ({name})', 'info')
        progress(10, f'Pulling {image}...')
        client.images.pull(image)

        # STEP 2
        log(f'⚙️ Installing... ({name})', 'info')
        progress(60, 'Creating container...')

        # build kwargs ...
        container = client.containers.run(
            image=image,
            name=app_name,
            detach=True,
            restart_policy={'Name': 'unless-stopped'},
            # ports, volumes, env ...
        )

        # STEP 3
        log(f'✅ Done installing ({name})', 'success')
        progress(100, 'Done!')
        socketio.emit('install_complete', {'status': 'success', 'app_id': app['id']})

    except Exception as e:
        log(f'❌ Error: {str(e)}', 'error')
        socketio.emit('install_complete', {'status': 'error', 'error': str(e)})
```

---

## Kode Frontend socket listener (di store.html, sudah ada)

```javascript
const socket = io();

socket.on("install_log", function (data) {
  appendLog(data.message, data.type); // tampilkan di log panel
});

socket.on("install_progress", function (data) {
  updateProgress(data.percent, data.message); // update progress bar
});

socket.on("install_complete", function (data) {
  if (data.status === "success") {
    // Setelah ini perlu auto-refresh installed apps + dashboard
    setTimeout(() => {
      loadInstalledApps().then(applyFilters);
    }, 1500);
  }
});
```

---

## Yang Diminta

Tolong perbaiki dan lengkapi kode berikut:

### 1. Perbaiki `_install_catalog_app()` di `store_api.py`

- Pastikan `socketio.emit()` dari background thread **benar-benar sampai ke browser**
- Gunakan pattern yang tepat untuk Flask-SocketIO dengan `async_mode='threading'`
- Log harus keluar berurutan: `Downloading → Installing → Done`
- Handle semua field dari catalog: `ports`, `volumes`, `env`, `cap_add`, `devices`, `network_mode`, `command`

### 2. Tambahkan endpoint `/api/docker/status`

Dipakai dashboard untuk auto-refresh Docker Status card setelah install selesai.

```python
# Yang perlu dikembalikan:
{
    "containers": [
        {
            "name": "uptime-kuma",
            "status": "running",
            "image": "louislam/uptime-kuma:2",
            "ports": ["3001:3001"]
        }
    ],
    "total": 5,
    "running": 4
}
```

### 3. Perbaiki frontend auto-refresh di `store.html`

Setelah `install_complete`, frontend harus:

1. Tampilkan tombol "Refresh & Open"
2. Auto-refresh installed status (badge Running muncul di kartu app)
3. Emit event ke dashboard supaya Docker Status card ikut update tanpa reload

---

## Constraint

- `store.html` **JANGAN DIUBAH** strukturnya, hanya boleh tambah/perbaiki JavaScript
- Gunakan `docker-py` SDK (bukan subprocess) untuk semua operasi Docker
- `socketio` instance ada di `app.py`, import dari sana
- `async_mode='threading'` sudah di-set di `app.py`
- Semua install harus jalan di background thread supaya request tidak timeout

---

## Output yang Diharapkan

1. File `store_api.py` yang sudah diperbaiki (full file)
2. Snippet JavaScript tambahan untuk `store.html` (bagian socket handler)
3. Endpoint `/api/docker/status` yang bisa dipakai dashboard

Jelaskan juga kenapa Socket.IO emit dari thread bisa gagal dan cara fix yang benar.
