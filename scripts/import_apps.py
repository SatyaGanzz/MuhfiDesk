import os
import json
import yaml

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS_DIR = os.path.join(BASE_DIR, 'big-bear-casaos', 'Apps')
DATA_DIR = os.path.join(BASE_DIR, 'data')
CATALOG_PATH = os.path.join(DATA_DIR, 'app_catalog.json')

def load_apps():
    apps = []
    
    if not os.path.exists(APPS_DIR):
        print(f"Apps directory not found: {APPS_DIR}")
        return apps

    # Load existing catalog
    existing_catalog = []
    if os.path.exists(CATALOG_PATH):
        try:
            with open(CATALOG_PATH, 'r', encoding='utf-8') as f:
                existing_catalog = json.load(f)
        except Exception as e:
            print(f"Failed to load existing catalog: {e}")
            
    existing_ids = {app['id'] for app in existing_catalog if 'id' in app}
    
    for app_name in os.listdir(APPS_DIR):
        app_path = os.path.join(APPS_DIR, app_name)
        if not os.path.isdir(app_path):
            continue
            
        compose_file = os.path.join(app_path, 'docker-compose.yml')
        if not os.path.exists(compose_file):
            continue
            
        try:
            with open(compose_file, 'r', encoding='utf-8') as f:
                content = f.read()
            parsed = yaml.safe_load(content)
            
            if not parsed or 'services' not in parsed:
                continue
                
            # Fallback to x-casaos
            casaos_meta = parsed.get('x-casaos', {})
            
            title = (casaos_meta.get('title', {}).get('en_us') if isinstance(casaos_meta.get('title'), dict) else casaos_meta.get('title')) or app_name.replace('-', ' ').title()
            tagline = (casaos_meta.get('tagline', {}).get('en_us') if isinstance(casaos_meta.get('tagline'), dict) else casaos_meta.get('tagline')) or ''
            icon = casaos_meta.get('icon', f'https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/{app_name.lower()}.png')
            category = casaos_meta.get('category', 'BigBearCasaOS')
                
            main_service_key = casaos_meta.get('main')
            if not main_service_key:
                main_service_key = list(parsed['services'].keys())[0]
                
            main_service = parsed['services'].get(main_service_key, {})
            
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
                        
            app_id = f"casaos_{app_name.lower().replace(' ', '')}"
            new_app = {
                "id": app_id,
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
                
            if app_id not in existing_ids:
                existing_catalog.append(new_app)
                existing_ids.add(app_id)
                
        except Exception as e:
            print(f"Failed to parse {app_name}: {e}")
            
    with open(CATALOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(existing_catalog, f, indent=4)
        
    print(f"Successfully processed apps. Total apps in catalog: {len(existing_catalog)}")

if __name__ == '__main__':
    load_apps()
