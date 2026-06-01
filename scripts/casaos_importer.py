#!/usr/bin/env python3
"""
CasaOS App Importer
Import applications from BigBearTechWorld CasaOS repository
"""

import os
import json
import requests
import zipfile
from io import BytesIO
import yaml

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
CASAOS_CACHE_DIR = os.path.join(DATA_DIR, 'casaos_cache')
CASAOS_APPS_FILE = os.path.join(DATA_DIR, 'casaos_apps.json')

# Repository URL
CASAOS_REPO_URL = "https://github.com/bigbeartechworld/big-bear-casaos/archive/refs/heads/master.zip"

def download_casaos_repo():
    """Download CasaOS repository"""
    print("📥 Downloading CasaOS repository...")
    
    try:
        response = requests.get(CASAOS_REPO_URL, timeout=120)
        response.raise_for_status()
        
        # Create cache directory
        os.makedirs(CASAOS_CACHE_DIR, exist_ok=True)
        
        # Extract ZIP
        print("📦 Extracting repository...")
        with zipfile.ZipFile(BytesIO(response.content)) as zip_ref:
            zip_ref.extractall(CASAOS_CACHE_DIR)
        
        print("✅ Repository downloaded and extracted")
        return True
        
    except Exception as e:
        print(f"❌ Failed to download repository: {e}")
        return False

def parse_casaos_app(app_dir, app_name):
    """Parse single CasaOS app from docker-compose.yml"""
    compose_file = os.path.join(app_dir, 'docker-compose.yml')
    
    if not os.path.exists(compose_file):
        return None
    
    try:
        with open(compose_file, 'r', encoding='utf-8') as f:
            compose_data = yaml.safe_load(f)
        
        if not compose_data:
            return None
        
        # Extract metadata from x-casaos
        casaos_meta = compose_data.get('x-casaos', {})
        
        # Get services
        services = compose_data.get('services', {})
        if not services:
            return None
        
        # Get first service (main service)
        service_name = list(services.keys())[0]
        service = services[service_name]
        
        # Parse ports
        ports = []
        for port in service.get('ports', []):
            try:
                if isinstance(port, str):
                    parts = port.split(':')
                    if len(parts) >= 2:
                        host_port = parts[0]
                        container_port = parts[1].split('/')[0]
                        protocol = 'tcp'
                        if '/' in parts[1]:
                            protocol = parts[1].split('/')[1]
                        
                        ports.append({
                            'host': int(host_port),
                            'container': int(container_port),
                            'protocol': protocol
                        })
                elif isinstance(port, dict):
                    ports.append({
                        'host': int(port.get('published', port.get('target', 8080))),
                        'container': int(port.get('target', 8080)),
                        'protocol': port.get('protocol', 'tcp')
                    })
            except:
                continue
        
        # Parse volumes
        volumes = []
        for vol in service.get('volumes', []):
            try:
                if isinstance(vol, str):
                    parts = vol.split(':')
                    if len(parts) >= 2:
                        # Convert CasaOS path to MuhfiDesk path
                        host_path = parts[0]
                        if host_path.startswith('/DATA/AppData/'):
                            host_path = host_path.replace('/DATA/AppData/', f'/opt/muhfi/apps/{app_name}/')
                        
                        volumes.append({
                            'bind': host_path,
                            'container': parts[1],
                            'description': 'App data'
                        })
            except:
                continue
        
        # Parse environment
        env = []
        env_data = service.get('environment', {})
        
        if isinstance(env_data, dict):
            for key, value in env_data.items():
                env.append({
                    'key': key,
                    'value': str(value) if value is not None else ''
                })
        elif isinstance(env_data, list):
            for item in env_data:
                if '=' in item:
                    key, value = item.split('=', 1)
                    env.append({
                        'key': key,
                        'value': value
                    })
        
        # Get description
        description = ''
        tagline = ''
        
        if isinstance(casaos_meta.get('description'), dict):
            description = casaos_meta['description'].get('en_us', '')
        elif isinstance(casaos_meta.get('description'), str):
            description = casaos_meta['description']
        
        if isinstance(casaos_meta.get('tagline'), dict):
            tagline = casaos_meta['tagline'].get('en_us', '')
        elif isinstance(casaos_meta.get('tagline'), str):
            tagline = casaos_meta['tagline']
        
        # Use tagline if description is empty
        if not description and tagline:
            description = tagline
        
        # Build app object
        app = {
            'id': app_name,
            'name': casaos_meta.get('developer', app_name.replace('-', ' ').title()),
            'description': description or f'{app_name} application',
            'category': casaos_meta.get('category', 'Utility'),
            'image': service.get('image', ''),
            'icon': casaos_meta.get('icon', f'https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/{app_name}.png'),
            'ports': ports,
            'volumes': volumes,
            'env': env,
            'network_mode': service.get('network_mode', 'bridge'),
            'restart': service.get('restart', 'unless-stopped'),
            'source': 'casaos',
            'author': casaos_meta.get('author', 'CasaOS Community')
        }
        
        return app
        
    except Exception as e:
        print(f"⚠️  Error parsing {app_name}: {e}")
        return None

def import_casaos_apps():
    """Import all CasaOS apps"""
    print("\n🚀 Starting CasaOS app import...\n")
    
    # Download repo
    if not download_casaos_repo():
        return []
    
    # Find Apps directory
    apps_dir = os.path.join(CASAOS_CACHE_DIR, 'big-bear-casaos-master', 'Apps')
    
    if not os.path.exists(apps_dir):
        print(f"❌ Apps directory not found at: {apps_dir}")
        return []
    
    casaos_apps = []
    failed = 0
    
    # Parse each app
    app_folders = [f for f in os.listdir(apps_dir) if os.path.isdir(os.path.join(apps_dir, f))]
    total = len(app_folders)
    
    print(f"📂 Found {total} apps to import\n")
    
    for i, app_name in enumerate(app_folders, 1):
        app_path = os.path.join(apps_dir, app_name)
        
        try:
            app = parse_casaos_app(app_path, app_name)
            if app:
                casaos_apps.append(app)
                print(f"✅ [{i}/{total}] {app['name']}")
            else:
                failed += 1
                print(f"⚠️  [{i}/{total}] {app_name} - No valid compose file")
        except Exception as e:
            failed += 1
            print(f"❌ [{i}/{total}] {app_name} - {str(e)}")
    
    # Save to JSON
    print(f"\n💾 Saving to {CASAOS_APPS_FILE}...")
    
    with open(CASAOS_APPS_FILE, 'w', encoding='utf-8') as f:
        json.dump(casaos_apps, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Import completed!")
    print(f"   Successful: {len(casaos_apps)}")
    print(f"   Failed: {failed}")
    print(f"   Total: {total}\n")
    
    return casaos_apps

if __name__ == '__main__':
    import_casaos_apps()
