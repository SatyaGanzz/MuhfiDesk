/**
 * Global Theme and Background Manager
 * Handles applying custom themes/backgrounds across all dashboard pages
 * Injects a FAB (Floating Action Button) in the bottom right corner.
 */

document.addEventListener('DOMContentLoaded', () => {
    // 1. Setup default theme constants
    const THEMES = [
        { id: 'mazer', name: 'Mazer (Dark Blue)', background: '#1b203d' },
        { id: 'github', name: 'GitHub Dark', background: '#0d1117' },
        { id: 'hacker', name: 'Hacker Terminal', background: '#0a0a0a' },
        { id: 'purple', name: 'Deep Purple', background: '#2d1b3d' },
        { id: 'grad1', name: 'Gradient Flow', background: 'linear-gradient(-45deg, #ee7752, #e73c7e, #23a6d5, #23d5ab)' }
    ];

    // Load saved preferences
    let savedTheme = localStorage.getItem('dashboard_bg');
    if (!savedTheme) {
        savedTheme = THEMES[0].background; // Default to Mazer
    }

    // 2. Apply the theme using a global <style> override to force consistency
    applyBackground(savedTheme);

    // 3. Inject the FAB and Modal HTML
    injectThemeWidget();
});

function applyBackground(bgValue) {
    let styleEl = document.getElementById('global-theme-override');
    if (!styleEl) {
        styleEl = document.createElement('style');
        styleEl.id = 'global-theme-override';
        document.head.appendChild(styleEl);
    }
    
    // Check if it's an image URL or CSS value
    let cssValue = bgValue;
    if (bgValue.startsWith('http') || bgValue.startsWith('/') || bgValue.startsWith('data:image')) {
        cssValue = `url('${bgValue}') center/cover no-repeat fixed`;
    } else if (bgValue.includes('gradient')) {
        // gradient animation logic if needed, but static for now
        cssValue = `${bgValue} fixed`;
    }

    styleEl.innerHTML = `
        body {
            background: ${cssValue} !important;
            background-size: cover !important;
            background-attachment: fixed !important;
            min-height: 100vh !important;
        }
        .wallpaper {
            display: none !important;
        }
    `;
}

function injectThemeWidget() {
    // Don't inject if already exists
    if (document.getElementById('theme-fab-container')) return;

    const container = document.createElement('div');
    container.id = 'theme-fab-container';
    container.innerHTML = `
        <!-- Floating Button -->
        <div id="theme-fab" style="position: fixed; bottom: 30px; right: 30px; width: 50px; height: 50px; background: #435ebe; color: white; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 1.5rem; cursor: pointer; box-shadow: 0 4px 15px rgba(0,0,0,0.3); z-index: 9999; transition: transform 0.2s;">
            <i class="fa-solid fa-palette"></i>
        </div>

        <!-- Theme Modal (Hidden by default) -->
        <div id="theme-modal" style="position: fixed; bottom: 90px; right: 30px; width: 300px; background: rgba(30,30,40,0.95); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.1); border-radius: 16px; padding: 1.5rem; box-shadow: 0 10px 30px rgba(0,0,0,0.5); z-index: 9998; opacity: 0; pointer-events: none; transform: translateY(20px); transition: all 0.3s ease; color: #fff; font-family: 'Inter', sans-serif;">
            <h3 style="margin-top: 0; margin-bottom: 1rem; font-size: 1.1rem; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 0.5rem;">Customize Theme</h3>
            
            <div style="margin-bottom: 1rem;">
                <label style="font-size: 0.85rem; color: #aaa; margin-bottom: 0.5rem; display: block;">Preset Backgrounds</label>
                <div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
                    <div class="theme-preset" data-bg="#1b203d" style="width:30px;height:30px;border-radius:50%;background:#1b203d;cursor:pointer;border:2px solid transparent;" title="Mazer"></div>
                    <div class="theme-preset" data-bg="#0d1117" style="width:30px;height:30px;border-radius:50%;background:#0d1117;cursor:pointer;border:2px solid transparent;" title="GitHub"></div>
                    <div class="theme-preset" data-bg="#000000" style="width:30px;height:30px;border-radius:50%;background:#000000;cursor:pointer;border:2px solid transparent;" title="Black"></div>
                    <div class="theme-preset" data-bg="#2d1b3d" style="width:30px;height:30px;border-radius:50%;background:#2d1b3d;cursor:pointer;border:2px solid transparent;" title="Purple"></div>
                    <div class="theme-preset" data-bg="linear-gradient(45deg, #1f4037, #99f2c8)" style="width:30px;height:30px;border-radius:50%;background:linear-gradient(45deg, #1f4037, #99f2c8);cursor:pointer;border:2px solid transparent;" title="Mint Gradient"></div>
                    <div class="theme-preset" data-bg="linear-gradient(45deg, #0f2027, #203a43, #2c5364)" style="width:30px;height:30px;border-radius:50%;background:linear-gradient(45deg, #0f2027, #203a43);cursor:pointer;border:2px solid transparent;" title="Ocean Gradient"></div>
                </div>
            </div>

            <div style="margin-bottom: 1rem;">
                <label style="font-size: 0.85rem; color: #aaa; margin-bottom: 0.5rem; display: block;">Custom Color (HEX)</label>
                <div style="display:flex; gap: 0.5rem;">
                    <input type="color" id="theme-color-picker" style="width:40px; height: 35px; border: none; border-radius: 6px; cursor: pointer; background: transparent; padding:0;">
                    <input type="text" id="theme-color-text" placeholder="#1b203d" style="flex:1; background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.1); color:white; padding: 0.4rem 0.6rem; border-radius: 6px; font-size: 0.85rem; outline:none;">
                </div>
            </div>

            <div>
                <label style="font-size: 0.85rem; color: #aaa; margin-bottom: 0.5rem; display: block;">Custom Image</label>
                <div style="display:flex; flex-direction:column; gap: 0.5rem;">
                    <div style="display:flex; gap: 0.5rem; align-items: center;">
                        <input type="file" id="theme-file-upload" accept="image/*" style="display:none;">
                        <button id="theme-upload-btn" style="flex:1; background:rgba(255,255,255,0.1); color:white; border:1px solid rgba(255,255,255,0.2); border-radius:6px; padding: 0.4rem 0.8rem; cursor:pointer; font-size:0.85rem;"><i class="fa-solid fa-upload"></i> Upload Local Image</button>
                    </div>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(container);

    // Interactions
    const fab = document.getElementById('theme-fab');
    const modal = document.getElementById('theme-modal');
    let isOpen = false;

    fab.addEventListener('click', () => {
        isOpen = !isOpen;
        if (isOpen) {
            modal.style.opacity = '1';
            modal.style.pointerEvents = 'all';
            modal.style.transform = 'translateY(0)';
            fab.style.transform = 'rotate(45deg)';
        } else {
            modal.style.opacity = '0';
            modal.style.pointerEvents = 'none';
            modal.style.transform = 'translateY(20px)';
            fab.style.transform = 'rotate(0deg)';
        }
    });

    // Handle presets
    document.querySelectorAll('.theme-preset').forEach(preset => {
        preset.addEventListener('click', (e) => {
            const bg = e.target.getAttribute('data-bg');
            setAndSaveTheme(bg);
        });
    });

    // Handle color picker and text input
    const colorPicker = document.getElementById('theme-color-picker');
    const colorText = document.getElementById('theme-color-text');

    colorPicker.addEventListener('input', (e) => {
        colorText.value = e.target.value;
        setAndSaveTheme(e.target.value);
    });

    colorText.addEventListener('change', (e) => {
        let val = e.target.value;
        if (!val.startsWith('#') && val.length === 6) val = '#' + val;
        colorPicker.value = val;
        setAndSaveTheme(val);
    });


    // Handle local image upload via FileReader and Canvas compression
    const fileInput = document.getElementById('theme-file-upload');
    const uploadBtn = document.getElementById('theme-upload-btn');
    
    uploadBtn.addEventListener('click', () => fileInput.click());
    
    fileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (!file) return;
        
        uploadBtn.innerHTML = "Processing...";
        
        const reader = new FileReader();
        reader.onload = function(event) {
            const img = new Image();
            img.onload = function() {
                // Compress image to fit in localStorage (max 1920x1080)
                const canvas = document.createElement('canvas');
                const MAX_WIDTH = 1920;
                const MAX_HEIGHT = 1080;
                let width = img.width;
                let height = img.height;
                
                if (width > height) {
                    if (width > MAX_WIDTH) {
                        height *= MAX_WIDTH / width;
                        width = MAX_WIDTH;
                    }
                } else {
                    if (height > MAX_HEIGHT) {
                        width *= MAX_HEIGHT / height;
                        height = MAX_HEIGHT;
                    }
                }
                
                canvas.width = width;
                canvas.height = height;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0, width, height);
                
                // Convert to JPEG at 70% quality to save space
                const dataUrl = canvas.toDataURL('image/jpeg', 0.7);
                try {
                    setAndSaveTheme(dataUrl);
                    uploadBtn.innerHTML = "<i class='fa-solid fa-check'></i> Uploaded";
                    setTimeout(() => { uploadBtn.innerHTML = "<i class='fa-solid fa-upload'></i> Upload Local Image"; }, 2000);
                } catch(err) {
                    alert("Image is too large to save! Please try a smaller image.");
                    uploadBtn.innerHTML = "<i class='fa-solid fa-upload'></i> Upload Local Image";
                }
            };
            img.src = event.target.result;
        };
        reader.readAsDataURL(file);
    });
}

function setAndSaveTheme(bgValue) {
    applyBackground(bgValue);
    localStorage.setItem('dashboard_bg', bgValue);
}
