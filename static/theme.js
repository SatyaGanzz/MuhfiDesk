/**
 * MuhfiDesk Theme Manager v2.0
 * Supports: Dark Mode, Light Mode, Glassmorphic Mode
 * Features: Live theme switching, custom background upload (local, stored in localStorage)
 */

const THEMES = {
    dark: {
        '--bg-base':            '#0f1117',
        '--bg-surface':         '#161b22',
        '--bg-elevated':        '#1c2128',
        '--glass-bg':           'rgba(22, 27, 34, 0.85)',
        '--glass-border':       'rgba(255, 255, 255, 0.08)',
        '--glass-highlight':    'rgba(255, 255, 255, 0.05)',
        '--text-main':          '#e6edf3',
        '--text-muted':         '#7d8590',
        '--text-subtle':        '#484f58',
        '--accent':             '#58a6ff',
        '--accent-glow':        'rgba(88, 166, 255, 0.25)',
        '--card-blur':          '0px',
        '--shadow':             '0 4px 24px rgba(0,0,0,0.4)',
        '--shadow-lg':          '0 8px 40px rgba(0,0,0,0.6)',
        '--body-bg':            '#0f1117',
        '--progress-inner':     '#1c2128',
        '--border-radius-base': '16px',
        '--top-bar-bg':         'rgba(22, 27, 34, 0.9)',
        '--scrollbar-thumb':    'rgba(255,255,255,0.12)',
    },
    light: {
        '--bg-base':            '#f0f4f8',
        '--bg-surface':         '#ffffff',
        '--bg-elevated':        '#f8fafc',
        '--glass-bg':           'rgba(255, 255, 255, 0.9)',
        '--glass-border':       'rgba(0, 0, 0, 0.08)',
        '--glass-highlight':    'rgba(255, 255, 255, 0.8)',
        '--text-main':          '#1a202c',
        '--text-muted':         '#718096',
        '--text-subtle':        '#a0aec0',
        '--accent':             '#3b82f6',
        '--accent-glow':        'rgba(59, 130, 246, 0.2)',
        '--card-blur':          '0px',
        '--shadow':             '0 2px 16px rgba(0,0,0,0.08)',
        '--shadow-lg':          '0 8px 32px rgba(0,0,0,0.12)',
        '--body-bg':            '#f0f4f8',
        '--progress-inner':     '#e8ecf0',
        '--border-radius-base': '16px',
        '--top-bar-bg':         'rgba(255,255,255,0.95)',
        '--scrollbar-thumb':    'rgba(0,0,0,0.15)',
    },
    glassmorphic: {
        '--bg-base':            'transparent',
        '--bg-surface':         'rgba(255, 255, 255, 0.07)',
        '--bg-elevated':        'rgba(255, 255, 255, 0.12)',
        '--glass-bg':           'rgba(255, 255, 255, 0.1)',
        '--glass-border':       'rgba(255, 255, 255, 0.2)',
        '--glass-highlight':    'rgba(255, 255, 255, 0.15)',
        '--text-main':          '#ffffff',
        '--text-muted':         'rgba(255,255,255,0.65)',
        '--text-subtle':        'rgba(255,255,255,0.4)',
        '--accent':             '#a78bfa',
        '--accent-glow':        'rgba(167, 139, 250, 0.35)',
        '--card-blur':          'blur(20px)',
        '--shadow':             '0 8px 32px rgba(0,0,0,0.25)',
        '--shadow-lg':          '0 16px 48px rgba(0,0,0,0.35)',
        '--body-bg':            'transparent',
        '--progress-inner':     'rgba(255,255,255,0.08)',
        '--border-radius-base': '20px',
        '--top-bar-bg':         'rgba(255, 255, 255, 0.1)',
        '--scrollbar-thumb':    'rgba(255,255,255,0.2)',
    }
};

const DEFAULT_BG = {
    dark:          '#0f1117', // Solid color instead of wallpaper
    light:         '/static/wallpapers/WAL5.jpg',
    glassmorphic:  '#161b22'  // Solid color instead of wallpaper
};

// ─── Core apply functions ────────────────────────────────────────────────────

function applyThemeVars(themeName) {
    const vars = THEMES[themeName] || THEMES.dark;
    const root = document.documentElement;
    for (const [key, val] of Object.entries(vars)) {
        root.style.setProperty(key, val);
    }
    
    // Prevent white flash while wallpaper loads
    if (themeName === 'light') {
        root.style.backgroundColor = '#f0f4f8';
    } else {
        root.style.backgroundColor = '#0f1117';
    }

    // Also update body classes so specific rules (like light mode overrides) work properly
    const className = `theme-${themeName}`;
    if (document.body) {
        document.body.className = className;
    } else {
        // If body not ready yet, wait for DOMContentLoaded
        document.addEventListener('DOMContentLoaded', () => {
            document.body.className = className;
        });
    }
}

function applyBackground(bgValue) {
    let styleEl = document.getElementById('global-theme-override');
    if (!styleEl) {
        styleEl = document.createElement('style');
        styleEl.id = 'global-theme-override';
        document.head.appendChild(styleEl);
    }

    let cssBody = '';
    if (!bgValue || bgValue === 'transparent') {
        // glassmorphic - use default gradient
        const saved = localStorage.getItem('dashboard_custom_bg');
        if (saved) {
            bgValue = saved;
        } else {
            const theme = localStorage.getItem('dashboard_theme') || 'dark';
            bgValue = DEFAULT_BG[theme] || DEFAULT_BG.dark;
        }
    }

    let cssBg = bgValue;
    if (bgValue.startsWith('http') || bgValue.startsWith('/') || bgValue.startsWith('data:image')) {
        cssBg = `url('${bgValue}') center/cover no-repeat fixed`;
    } else if (bgValue.includes('gradient')) {
        cssBg = `${bgValue}`;
    }

    const brightness = localStorage.getItem('dashboard_bg_brightness') || 100;

    styleEl.innerHTML = `
        body {
            background: transparent !important;
            min-height: 100vh !important;
        }
        body::before {
            content: '';
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: ${cssBg} !important;
            background-size: cover !important;
            background-position: center !important;
            background-attachment: fixed !important;
            z-index: -2;
            filter: brightness(${brightness}%);
            pointer-events: none;
        }
    `;
}

function applyTheme(themeName, customBg) {
    themeName = themeName || 'dark';
    applyThemeVars(themeName);

    // Determine background
    let bg = customBg || localStorage.getItem('dashboard_custom_bg');
    if (!bg) bg = DEFAULT_BG[themeName];
    applyBackground(bg);
}

// ─── Public API ──────────────────────────────────────────────────────────────

function setTheme(themeName) {
    localStorage.setItem('dashboard_theme', themeName);
    const customBg = localStorage.getItem('dashboard_custom_bg');
    applyTheme(themeName, customBg);
    updateThemeSwitcherUI(themeName);
    showThemeToast(themeName);
}

function setCustomBackground(bgValue) {
    localStorage.setItem('dashboard_custom_bg', bgValue);
    applyBackground(bgValue);
}

function resetCustomBackground() {
    localStorage.removeItem('dashboard_custom_bg');
    const theme = localStorage.getItem('dashboard_theme') || 'dark';
    applyBackground(DEFAULT_BG[theme]);
}

// Also keep old compat
function setAndSaveTheme(bgValue) {
    setCustomBackground(bgValue);
}

// ─── UI Helpers ──────────────────────────────────────────────────────────────

function updateThemeSwitcherUI(themeName) {
    // Update floating theme switcher if present
    document.querySelectorAll('.theme-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.theme === themeName);
    });
    // Update radio cards in settings if present
    document.querySelectorAll('.theme-radio-card').forEach(card => {
        card.classList.toggle('active', card.dataset.theme === themeName);
    });
}

function showThemeToast(themeName) {
    const labels = { dark: 'Mode Gelap', light: 'Mode Terang', glassmorphic: 'Glassmorphic' };
    const icons  = { dark: '🌙', light: '☀️', glassmorphic: '✨' };
    
    let toast = document.getElementById('theme-toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'theme-toast';
        toast.style.cssText = `
            position: fixed;
            bottom: 2rem;
            right: 2rem;
            background: var(--glass-bg, rgba(30,34,53,0.9));
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--glass-border, rgba(255,255,255,0.15));
            color: var(--text-main, #fff);
            padding: 0.75rem 1.4rem;
            border-radius: 50px;
            font-size: 0.95rem;
            font-family: 'Inter', sans-serif;
            font-weight: 500;
            z-index: 9999;
            box-shadow: var(--shadow-lg, 0 8px 32px rgba(0,0,0,0.4));
            display: flex;
            align-items: center;
            gap: 0.6rem;
            opacity: 0;
            transform: translateY(12px);
            transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
            pointer-events: none;
        `;
        document.body.appendChild(toast);
    }
    
    toast.innerHTML = `${icons[themeName]} Tema diubah ke <strong>${labels[themeName]}</strong>`;
    requestAnimationFrame(() => {
        toast.style.opacity = '1';
        toast.style.transform = 'translateY(0)';
    });
    
    clearTimeout(toast._timeout);
    toast._timeout = setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(12px)';
    }, 2500);
}

// ─── Floating Theme Switcher Widget ─────────────────────────────────────────

function injectThemeSwitcher() {
    if (document.getElementById('theme-switcher-fab')) return;
    
    const fab = document.createElement('div');
    fab.id = 'theme-switcher-fab';
    fab.innerHTML = `
        <div id="theme-fab-toggle" title="Ganti Tema" onclick="toggleThemeFab()">
            <i class="fa-solid fa-swatchbook"></i>
        </div>
        <div id="theme-fab-panel">
            <div class="theme-fab-label">Pilih Tema</div>
            <button class="theme-btn" data-theme="dark" onclick="setTheme('dark')" title="Mode Gelap">
                <i class="fa-solid fa-moon"></i>
                <span>Gelap</span>
            </button>
            <button class="theme-btn" data-theme="light" onclick="setTheme('light')" title="Mode Terang">
                <i class="fa-solid fa-sun"></i>
                <span>Terang</span>
            </button>
            <button class="theme-btn" data-theme="glassmorphic" onclick="setTheme('glassmorphic')" title="Glassmorphic">
                <i class="fa-solid fa-wand-magic-sparkles"></i>
                <span>Glass</span>
            </button>
            <hr style="border-color: var(--glass-border, rgba(255,255,255,0.1)); margin: 0.4rem 0">
            <label class="theme-btn upload-btn" title="Upload Wallpaper">
                <input type="file" accept="image/*" onchange="handleWallpaperUpload(event)" style="display:none">
                <i class="fa-solid fa-image"></i>
                <span>Wallpaper</span>
            </label>
            <button class="theme-btn reset-btn" onclick="resetCustomBackground()" title="Reset Background">
                <i class="fa-solid fa-rotate-left"></i>
                <span>Reset</span>
            </button>
        </div>
    `;
    
    const style = document.createElement('style');
    style.textContent = `
        #theme-switcher-fab {
            position: fixed;
            bottom: 2rem;
            left: 2rem;
            z-index: 8888;
            font-family: 'Inter', sans-serif;
        }
        #theme-fab-toggle {
            width: 46px;
            height: 46px;
            border-radius: 50%;
            background: var(--glass-bg, rgba(30,34,53,0.85));
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--glass-border, rgba(255,255,255,0.15));
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            box-shadow: var(--shadow, 0 4px 16px rgba(0,0,0,0.3));
            color: var(--accent, #58a6ff);
            font-size: 1.1rem;
            transition: all 0.25s ease;
        }
        #theme-fab-toggle:hover {
            transform: scale(1.1) rotate(15deg);
            box-shadow: 0 0 20px var(--accent-glow, rgba(88,166,255,0.3));
        }
        #theme-fab-panel {
            position: absolute;
            bottom: 56px;
            left: 0;
            background: var(--glass-bg, rgba(22,27,34,0.95));
            backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            border: 1px solid var(--glass-border, rgba(255,255,255,0.12));
            border-radius: 16px;
            padding: 0.75rem;
            min-width: 140px;
            box-shadow: var(--shadow-lg, 0 8px 40px rgba(0,0,0,0.5));
            display: flex;
            flex-direction: column;
            gap: 0.3rem;
            transform-origin: bottom left;
            transform: scale(0.85) translateY(8px);
            opacity: 0;
            pointer-events: none;
            transition: all 0.25s cubic-bezier(0.34, 1.56, 0.64, 1);
        }
        #theme-fab-panel.open {
            transform: scale(1) translateY(0);
            opacity: 1;
            pointer-events: all;
        }
        .theme-fab-label {
            font-size: 0.72rem;
            font-weight: 600;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: var(--text-muted, #7d8590);
            padding: 0 0.4rem 0.3rem;
        }
        .theme-btn {
            display: flex;
            align-items: center;
            gap: 0.6rem;
            padding: 0.5rem 0.8rem;
            border: none;
            border-radius: 10px;
            background: transparent;
            color: var(--text-main, #e6edf3);
            cursor: pointer;
            font-size: 0.88rem;
            font-family: 'Inter', sans-serif;
            font-weight: 500;
            transition: all 0.18s ease;
            text-align: left;
            width: 100%;
        }
        .theme-btn:hover {
            background: var(--glass-highlight, rgba(255,255,255,0.08));
            color: var(--accent, #58a6ff);
        }
        .theme-btn.active {
            background: var(--accent-glow, rgba(88,166,255,0.15));
            color: var(--accent, #58a6ff);
        }
        .theme-btn.active::after {
            content: '✓';
            margin-left: auto;
            font-size: 0.8rem;
        }
        .theme-btn i { width: 16px; text-align: center; }
        .upload-btn { cursor: pointer; }
        .reset-btn { color: var(--text-muted, #7d8590); }
        .reset-btn:hover { color: #f85149 !important; }
    `;
    document.head.appendChild(style);
    document.body.appendChild(fab);
}

function toggleThemeFab() {
    const panel = document.getElementById('theme-fab-panel');
    if (panel) panel.classList.toggle('open');
    
    // Close on outside click
    setTimeout(() => {
        document.addEventListener('click', closeFabOnOutside, { once: true });
    }, 10);
}

function closeFabOnOutside(e) {
    const fab = document.getElementById('theme-switcher-fab');
    if (fab && !fab.contains(e.target)) {
        const panel = document.getElementById('theme-fab-panel');
        if (panel) panel.classList.remove('open');
    }
}

// ─── Wallpaper Upload Handler ────────────────────────────────────────────────

function handleWallpaperUpload(event) {
    const file = event.target.files[0];
    if (!file) return;
    
    // Close FAB panel
    const panel = document.getElementById('theme-fab-panel');
    if (panel) panel.classList.remove('open');
    
    const reader = new FileReader();
    reader.onload = function(e) {
        // Compress image before storing
        const img = new Image();
        img.onload = function() {
            const canvas = document.createElement('canvas');
            const MAX_W = 1920, MAX_H = 1080;
            let { width, height } = img;
            if (width > MAX_W || height > MAX_H) {
                const ratio = Math.min(MAX_W / width, MAX_H / height);
                width = Math.round(width * ratio);
                height = Math.round(height * ratio);
            }
            canvas.width = width;
            canvas.height = height;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(img, 0, 0, width, height);
            
            // Try at quality 0.8, then lower if too big
            let dataUrl = canvas.toDataURL('image/jpeg', 0.8);
            if (dataUrl.length > 1_200_000) {
                dataUrl = canvas.toDataURL('image/jpeg', 0.55);
            }
            if (dataUrl.length > 1_200_000) {
                dataUrl = canvas.toDataURL('image/jpeg', 0.35);
            }
            
            try {
                setCustomBackground(dataUrl);
                showThemeToast('glassmorphic'); // reuse for notification
                // Override toast text
                const toast = document.getElementById('theme-toast');
                if (toast) toast.innerHTML = '🖼️ Wallpaper berhasil diterapkan!';
            } catch(err) {
                alert('Gambar terlalu besar untuk disimpan di browser. Coba gambar yang lebih kecil.');
            }
        };
        img.src = e.target.result;
    };
    reader.readAsDataURL(file);
    event.target.value = ''; // reset input
}

// ─── Init ────────────────────────────────────────────────────────────────────

// Apply immediately to prevent FOUC (glitch flash)
(function() {
    try {
        const savedTheme = localStorage.getItem('dashboard_theme') || 'dark';
        const customBg   = localStorage.getItem('dashboard_custom_bg');
        applyTheme(savedTheme, customBg);
    } catch(e) {
        console.error('Theme init error:', e);
    }
})();

document.addEventListener('DOMContentLoaded', () => {
    const savedTheme = localStorage.getItem('dashboard_theme') || 'dark';
    const customBg   = localStorage.getItem('dashboard_custom_bg');
    
    applyTheme(savedTheme, customBg); // Re-apply in case body classes were missed
    updateThemeSwitcherUI(savedTheme);
    
    // Trigger localization
    loadAndApplyLanguage();
});

// ─── i18n Localization ────────────────────────────────────────────────────────

function loadAndApplyLanguage() {
    fetchLanguageAndTranslate();
}

function fetchLanguageAndTranslate() {
    fetch('/api/settings')
        .then(res => res.json())
        .then(data => {
            const lang = (data && data.general && data.general.language) ? data.general.language : 'en';
            if (lang !== 'en') {
                fetch(`/static/locales/${lang}.json`)
                    .then(res => res.json())
                    .then(dict => applyTranslation(dict))
                    .catch(err => console.log('Failed to load language file', err));
            }
        })
        .catch(err => console.log('Failed to fetch language settings', err));
}

function applyTranslation(dict) {
    if (!dict) return;
    
    // Walk the DOM and replace exact text nodes
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
    let node;
    const nodesToReplace = [];
    while(node = walk.nextNode()) {
        const text = node.nodeValue.trim();
        if (text && dict[text]) {
            nodesToReplace.push({node, original: text, translation: dict[text]});
        }
    }
    
    nodesToReplace.forEach(item => {
        item.node.nodeValue = item.node.nodeValue.replace(item.original, item.translation);
    });
    
    // Also translate placeholders and titles
    document.querySelectorAll('[placeholder], [title]').forEach(el => {
        if (el.placeholder) {
            const pText = el.placeholder.trim();
            if (dict[pText]) {
                el.placeholder = dict[pText];
            }
        }
        if (el.title) {
            const tText = el.title.trim();
            if (dict[tText]) {
                el.title = dict[tText];
            }
        }
    });
}
