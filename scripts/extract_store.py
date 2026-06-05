"""
Extract store apps dari big-bear-casaos-master/Apps/ ke store_apps/
Jalankan sekali: python scripts/extract_store.py
"""
import os
import sys
import shutil
import json
import yaml
import io

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR  = os.path.join(BASE_DIR, 'big-bear-casaos-master', 'Apps')
DST_DIR  = os.path.join(BASE_DIR, 'store_apps')

def parse_app(app_id, compose_path):
    try:
        with open(compose_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        if not data:
            return None

        meta = data.get('x-casaos', {}) or {}

        def get_text(field):
            val = meta.get(field, {})
            if isinstance(val, dict):
                return val.get('en_us', '') or ''
            return str(val) if val else ''

        name        = get_text('title') or app_id
        description = get_text('description') or get_text('tagline')
        icon        = meta.get('icon', '') or ''
        category    = meta.get('category', 'BigBearCasaOS') or 'BigBearCasaOS'
        developer   = meta.get('developer', '') or ''
        port_map    = str(meta.get('port_map', '')) or ''
        main_svc    = meta.get('main', 'app') or 'app'

        services = data.get('services', {}) or {}
        svc = services.get(main_svc) or (next(iter(services.values())) if services else {})

        image = svc.get('image', '') if svc else ''

        # Ports
        ports = []
        for p in (svc.get('ports', []) if svc else []):
            s = str(p).strip().strip('"\'')
            parts = s.split(':')
            if len(parts) == 2:
                host = parts[0]
                cont = parts[1].split('/')[0]
                proto = parts[1].split('/')[1] if '/' in parts[1] else 'tcp'
                ports.append({'host': host, 'container': cont, 'protocol': proto})
            elif len(parts) == 3:
                ports.append({'host': parts[1], 'container': parts[2].split('/')[0], 'protocol': 'tcp'})

        # Volumes
        volumes = []
        for v in (svc.get('volumes', []) if svc else []):
            parts = str(v).split(':')
            if len(parts) >= 2:
                volumes.append({'bind': parts[0], 'container': parts[1], 'description': parts[1]})

        # Environment
        env_list = []
        raw_env = svc.get('environment', []) if svc else []
        if isinstance(raw_env, list):
            for e in raw_env:
                if '=' in str(e):
                    k, v = str(e).split('=', 1)
                    env_list.append({'key': k, 'value': v})
        elif isinstance(raw_env, dict):
            for k, v in raw_env.items():
                env_list.append({'key': k, 'value': str(v) if v is not None else ''})

        return {
            'id': app_id,
            'name': name,
            'description': description,
            'icon': icon,
            'category': category,
            'developer': developer,
            'image': image,
            'port_map': port_map,
            'ports': ports,
            'volumes': volumes,
            'env': env_list,
        }
    except Exception as e:
        print(f"  [WARN] Parse error {app_id}: {e}")
        return None


def main():
    if not os.path.isdir(SRC_DIR):
        print(f"ERROR: Source dir not found: {SRC_DIR}")
        sys.exit(1)

    os.makedirs(DST_DIR, exist_ok=True)
    print(f"Source : {SRC_DIR}")
    print(f"Dest   : {DST_DIR}")
    print()

    catalog = []
    ok = 0
    skip = 0

    folders = sorted(f for f in os.listdir(SRC_DIR) if os.path.isdir(os.path.join(SRC_DIR, f)))
    total = len(folders)

    for i, app_id in enumerate(folders, 1):
        src_app = os.path.join(SRC_DIR, app_id)
        compose_src = os.path.join(src_app, 'docker-compose.yml')

        if not os.path.isfile(compose_src):
            skip += 1
            continue

        # Parse metadata
        meta = parse_app(app_id, compose_src)
        if not meta:
            skip += 1
            continue

        # Buat folder tujuan dan copy docker-compose.yml
        dst_app = os.path.join(DST_DIR, app_id)
        os.makedirs(dst_app, exist_ok=True)
        shutil.copy2(compose_src, os.path.join(dst_app, 'docker-compose.yml'))

        # Copy config.json jika ada
        config_src = os.path.join(src_app, 'config.json')
        if os.path.isfile(config_src):
            shutil.copy2(config_src, os.path.join(dst_app, 'config.json'))

        catalog.append(meta)
        ok += 1
        print(f"  [{i:3d}/{total}] OK  {app_id:40s} -> {meta['name']}")

    # Simpan catalog.json
    catalog_path = os.path.join(DST_DIR, 'catalog.json')
    with open(catalog_path, 'w', encoding='utf-8') as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    print()
    print(f"===========================================")
    print(f"  Total apps   : {total}")
    print(f"  Berhasil     : {ok}")
    print(f"  Dilewati     : {skip}")
    print(f"  Catalog JSON : {catalog_path}")
    print(f"  Output dir   : {DST_DIR}")
    print(f"===========================================")
    print()
    print("Sekarang lu bisa hapus folder: big-bear-casaos-master/")
    print("Dan update STORE_APPS_DIR di app.py ke store_apps/")


if __name__ == '__main__':
    main()
