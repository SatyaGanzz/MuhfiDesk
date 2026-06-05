let currentPath = 'drives://';
let selectedItem = null;
let clipboard = null;
let currentPage = 1;
let hasMore = false;
let isLoading = false;
let viewMode = 'drives'; // 'files' or 'drives'
let currentSortBy = 'name';
let currentSortOrder = 'asc';

// Quick folder list for sidebar
const quickFolders = [];

document.addEventListener('DOMContentLoaded', () => {
    const urlParams = new URLSearchParams(window.location.search);
    const initialPath = urlParams.get('path');
    
    if (initialPath) {
        loadFiles(initialPath);
    } else {
        loadDrives();
    }
    buildSidebarTree();
    setupDragAndDrop();

    // Close context menu on click outside
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.context-menu')) {
            hideContextMenu();
        }
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Delete' && selectedItem) deleteItem();
        if (e.key === 'F2' && selectedItem) renameItem();
        if (e.ctrlKey && e.key === 'c' && selectedItem) copyItem('copy');
        if (e.ctrlKey && e.key === 'x' && selectedItem) copyItem('cut');
        if (e.ctrlKey && e.key === 'v' && clipboard) pasteItem();
    });
});

function loadDrives() {
    viewMode = 'drives';
    currentPath = 'drives://';
    
    document.getElementById('file-list').innerHTML = '';
    document.getElementById('status-text').textContent = 'Loading drives...';
    document.getElementById('status-path').textContent = 'This PC';
    
    renderBreadcrumbs('drives://');
    updateSidebarActive('drives://');

    fetch('/api/files/drives')
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                alert("Error loading drives: " + data.error);
                return;
            }
            renderDriveTable(data.drives);
            document.getElementById('status-text').textContent = `${data.drives.length} drives found`;
        })
        .catch(err => {
            console.error("Drives fetch error:", err);
            document.getElementById('status-text').textContent = 'Error loading drives';
        });
}

function renderDriveTable(drives) {
    const tbody = document.getElementById('file-list');
    tbody.innerHTML = '';
    
    drives.forEach(drive => {
        const tr = document.createElement('tr');
        tr.onclick = (e) => selectDriveRow(tr, drive, e);
        tr.ondblclick = () => loadFiles(drive.mountpoint);
        
        const usedGB = drive.total ? (drive.used / 1024**3).toFixed(1) : '-';
        const totalGB = drive.total ? (drive.total / 1024**3).toFixed(1) : '-';
        const percent = drive.percent || 0;
        
        // Progress bar style for disk usage
        let barColor = '#238636'; // Green
        if (percent > 70) barColor = '#d29922'; // Yellow
        if (percent > 90) barColor = '#f85149'; // Red

        tr.innerHTML = `
            <td>
                <div style="display:flex; align-items:center; gap:0.8rem">
                    <span class="fm-icon dir" style="font-size:1.2rem"><i class="fa-solid fa-hard-drive"></i></span>
                    <div>
                        <div style="font-weight:500">${drive.mountpoint}</div>
                        <div style="font-size:0.75rem; color:#888">${drive.device} (${drive.fstype})</div>
                    </div>
                </div>
            </td>
            <td>
                <div style="width:120px">
                    <div style="font-size:0.75rem; margin-bottom:4px; display:flex; justify-content:space-between">
                        <span>${usedGB} / ${totalGB} GB</span>
                        <span>${percent}%</span>
                    </div>
                    <div style="height:6px; background:rgba(255,255,255,0.1); border-radius:3px; overflow:hidden">
                        <div style="width:${percent}%; height:100%; background:${barColor}"></div>
                    </div>
                </div>
            </td>
            <td style="color:var(--text-muted)">-</td>
            <td style="color:var(--text-muted)">-</td>
        `;
        tbody.appendChild(tr);
    });
}

function selectDriveRow(tr, drive, e) {
    document.querySelectorAll('.fm-table tr').forEach(r => r.classList.remove('selected'));
    tr.classList.add('selected');
    // Mock a selectedItem for detail panel but minimal
    selectedItem = {
        name: drive.mountpoint,
        is_dir: true,
        path: drive.mountpoint,
        size: drive.total ? formatSize(drive.total) : '-',
        date: '-',
        perm: drive.fstype
    };
    updateDetailPanel();
}

function buildSidebarTree() {
    const container = document.getElementById('sidebar-tree');
    container.innerHTML = '';

    // PC / Home root
    const pcDiv = document.createElement('div');
    pcDiv.className = 'tree-item';
    pcDiv.dataset.path = 'drives://';
    pcDiv.innerHTML = `<i class="fa-solid fa-display"></i> This PC`;
    pcDiv.onclick = () => loadDrives();
    container.appendChild(pcDiv);

    // Fetch platform-aware quick folders
    fetch('/api/files/platform')
        .then(r => r.json())
        .then(data => {
            if (!data.quick_folders || data.quick_folders.length === 0) return;

            const quickLabel = document.createElement('div');
            quickLabel.style.cssText = 'font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;padding:0.8rem 0.6rem 0.3rem;letter-spacing:0.05em';
            quickLabel.textContent = 'Quick Access';
            container.appendChild(quickLabel);

            data.quick_folders.forEach(folder => {
                const div = document.createElement('div');
                div.className = 'tree-item';
                div.dataset.path = folder.path;

                // Detect if it's a sub-folder (name contains /)
                const isSub = folder.name.includes('/');
                const icon = folder.icon || 'fa-folder';
                const label = isSub ? folder.name.split('/').pop() : folder.name;

                // Set icon colors dynamically
                let iconColor = '#FF9966'; // default folder color
                if (icon.includes('fa-hard-drive')) iconColor = '#23a6d5';
                if (icon.includes('fa-home')) iconColor = '#a78bfa';
                if (icon.includes('fa-desktop')) iconColor = '#58a6ff';
                if (icon.includes('fa-download')) iconColor = '#4ecdc4';
                if (icon.includes('fa-file')) iconColor = '#888';
                if (icon.includes('fa-music')) iconColor = '#e73c7e';
                if (icon.includes('fa-video')) iconColor = '#ffe66d';
                if (icon.includes('fa-image')) iconColor = '#ee7752';

                div.style.paddingLeft = isSub ? '1.8rem' : '';
                div.title = folder.path; // Tooltip shows full path on hover
                div.innerHTML = `<i class="fa-solid ${icon}" style="color:${iconColor}; width:16px;text-align:center"></i> ${label}`;
                div.onclick = () => loadFiles(folder.path);
                container.appendChild(div);
            });
        })
        .catch(() => {}); // silent fail


    // Fetch drives
    fetch('/api/files/drives')
        .then(r => r.json())
        .then(data => {
            if (!data.drives || data.drives.length === 0) return;

            const label = document.createElement('div');
            label.style.cssText = 'font-size:0.7rem;color:var(--text-muted);text-transform:uppercase;padding:0.8rem 0.6rem 0.4rem';
            label.textContent = 'Drives';
            container.appendChild(label);

            data.drives.forEach(drive => {
                const div = document.createElement('div');
                div.className = 'tree-item';
                div.dataset.path = drive.mountpoint;
                div.innerHTML = `<i class="fa-solid fa-hard-drive" style="color:#23a6d5; width:16px; text-align:center"></i> ${drive.mountpoint}`;
                div.onclick = () => loadFiles(drive.mountpoint);
                container.appendChild(div);
            });
        })
        .catch(() => {});
}


function loadFiles(path, page = 1) {
    viewMode = 'files';
    if (page === 1) {
        document.getElementById('file-list').innerHTML = '';
        document.getElementById('status-text').textContent = 'Loading...';
        currentPage = 1;
        selectedItem = null;
    }

    isLoading = true;

    fetch(`/api/files/list?path=${encodeURIComponent(path)}&page=${page}&sort_by=${currentSortBy}&order=${currentSortOrder}`)
        .then(r => r.json())
        .then(data => {
            isLoading = false;

            if (data.error) {
                alert("Error: " + data.error);
                return;
            }

            currentPath = data.current_path;

            if (page === 1) {
                renderBreadcrumbs(currentPath);
                updateSidebarActive(currentPath);
            }

            renderTable(data.items, page === 1);

            hasMore = data.has_more;
            currentPage = page;

            document.getElementById('status-text').textContent = `${data.total || 0} items`;
            document.getElementById('status-path').textContent = currentPath;
        })
        .catch(err => {
            console.error(err);
            isLoading = false;
        });
}

function updateSidebarActive(path) {
    document.querySelectorAll('.tree-item').forEach(el => {
        el.classList.toggle('active', el.dataset.path === path);
    });
}

function renderBreadcrumbs(path) {
    const container = document.getElementById('breadcrumbs');
    container.innerHTML = '';

    if (path === 'drives://') {
        const el = document.createElement('span');
        el.className = 'fm-crumb active';
        el.innerHTML = '<i class="fa-solid fa-display"></i> This PC';
        container.appendChild(el);
        return;
    }

    // Always show "This PC" as root of breadcrumbs
    const pcEl = document.createElement('span');
    pcEl.className = 'fm-crumb';
    pcEl.innerHTML = 'This PC';
    pcEl.onclick = () => loadDrives();
    container.appendChild(pcEl);

    const pcSep = document.createElement('span');
    pcSep.className = 'fm-crumb-sep';
    pcSep.innerHTML = '<i class="fa-solid fa-chevron-right"></i>';
    container.appendChild(pcSep);

    // Root (/)
    const rootEl = document.createElement('span');
    rootEl.className = 'fm-crumb' + (path === '/' ? ' active' : '');
    rootEl.textContent = '/';
    rootEl.onclick = () => loadFiles('/');
    container.appendChild(rootEl);

    if (path === '/') return;

    const parts = path.split('/').filter(p => p);
    let builtPath = '';
    parts.forEach((part, index) => {
        builtPath += '/' + part;

        const sep = document.createElement('span');
        sep.className = 'fm-crumb-sep';
        sep.innerHTML = '<i class="fa-solid fa-chevron-right"></i>';
        container.appendChild(sep);

        const el = document.createElement('span');
        el.className = 'fm-crumb';
        el.textContent = part;
        const p = builtPath;

        if (index === parts.length - 1) {
            el.classList.add('active');
        } else {
            el.onclick = () => loadFiles(p);
        }
        container.appendChild(el);
    });
}

function formatSize(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function renderTable(items, isNew) {
    const tbody = document.getElementById('file-list');

    if (isNew && items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:2rem; color:var(--text-muted)">Empty folder</td></tr>';
        return;
    }

    items.forEach(item => {
        const tr = document.createElement('tr');
        tr.onclick = (e) => selectRow(tr, item, e);
        tr.ondblclick = () => openItem(item);
        tr.oncontextmenu = (e) => showContextMenu(e, item, tr);

        let iconClass = item.is_dir ? 'fa-folder dir' : 'fa-file file';
        let iconColor = item.is_dir ? '#FF9966' : '#a0a0b0';
        
        if (!item.is_dir) {
            const extMatch = item.name.match(/\.([^.]+)$/);
            const ext = extMatch ? extMatch[1].toLowerCase() : '';
            
            if (['py', 'js', 'html', 'css', 'json', 'xml', 'sh', 'php', 'c', 'cpp', 'java'].includes(ext)) { iconClass = 'fa-file-code file'; iconColor = '#58a6ff'; }
            else if (['txt', 'log', 'md', 'conf', 'ini'].includes(ext)) { iconClass = 'fa-file-lines file'; iconColor = '#8b949e'; }
            else if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'ico'].includes(ext)) { iconClass = 'fa-file-image file'; iconColor = '#ee7752'; }
            else if (['zip', 'tar', 'gz', '7z', 'rar'].includes(ext)) { iconClass = 'fa-file-zipper file'; iconColor = '#e3b341'; }
            else if (['doc', 'docx'].includes(ext)) { iconClass = 'fa-file-word file'; iconColor = '#2b579a'; }
            else if (['xls', 'xlsx', 'csv'].includes(ext)) { iconClass = 'fa-file-excel file'; iconColor = '#217346'; }
            else if (['ppt', 'pptx'].includes(ext)) { iconClass = 'fa-file-powerpoint file'; iconColor = '#b7472a'; }
            else if (['pdf'].includes(ext)) { iconClass = 'fa-file-pdf file'; iconColor = '#da0b20'; }
            else if (['mp4', 'mkv', 'avi', 'mov'].includes(ext)) { iconClass = 'fa-file-video file'; iconColor = '#e73c7e'; }
            else if (['mp3', 'wav', 'ogg', 'flac'].includes(ext)) { iconClass = 'fa-file-audio file'; iconColor = '#23a6d5'; }
        }

        tr.innerHTML = `
            <td><span class="fm-icon ${item.is_dir ? 'dir' : 'file'}"><i class="fa-solid ${iconClass.split(' ')[0]}" style="color: ${iconColor}"></i></span>${item.name}</td>
            <td>${item.size}</td>
            <td style="font-family:monospace; color:#888">${item.perm || '-'}</td>
            <td style="color:var(--text-muted)">${item.date}</td>
        `;
        tbody.appendChild(tr);
    });
}

function selectRow(tr, item, e) {
    document.querySelectorAll('.fm-table tr').forEach(r => r.classList.remove('selected'));
    tr.classList.add('selected');
    selectedItem = item;

    // Don't trigger when right-clicking
    if (e && e.button === 2) return;
}

function updateDetailPanel() {
    // Removed
}

function closeDetailPanel() {
    // Removed
}

function toggleSort(field) {
    if (currentSortBy === field) {
        currentSortOrder = currentSortOrder === 'asc' ? 'desc' : 'asc';
    } else {
        currentSortBy = field;
        currentSortOrder = 'asc';
    }
    
    // Update icons
    ['name', 'size', 'date'].forEach(f => {
        const icon = document.getElementById('sort-icon-' + f);
        if (icon) {
            icon.className = 'fa-solid fa-sort';
            if (f === currentSortBy) {
                icon.className = currentSortOrder === 'asc' ? 'fa-solid fa-sort-up' : 'fa-solid fa-sort-down';
            }
        }
    });

    if (viewMode === 'files') {
        loadFiles(currentPath, 1);
    }
}

function openItem(item) {
    if (item.is_dir) {
        loadFiles(item.path);
    } else {
        const ext = item.name.split('.').pop().toLowerCase();
        const imageExts = ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp', 'ico'];
        const textExts = ['txt', 'log', 'md', 'conf', 'py', 'js', 'html', 'css', 'json', 'xml', 'sh', 'csv', 'yaml', 'yml', 'ini'];
        
        if (imageExts.includes(ext)) {
            viewImage(item);
        } else if (textExts.includes(ext) || !item.name.includes('.')) {
            openEditor(item);
        } else {
            // For all other file types (pdf, zip, mp4, etc.), open in new tab (will download or preview in browser)
            window.open('/api/files/download?path=' + encodeURIComponent(item.path), '_blank');
        }
    }
}

function viewImage(item) {
    let viewer = document.getElementById('image-viewer-overlay');
    if (!viewer) {
        viewer = document.createElement('div');
        viewer.id = 'image-viewer-overlay';
        viewer.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.85);z-index:9999;display:flex;flex-direction:column;align-items:center;justify-content:center;backdrop-filter:blur(5px);';
        
        viewer.innerHTML = `
            <div style="width:100%;padding:1.5rem;display:flex;justify-content:space-between;position:absolute;top:0;box-sizing:border-box;">
                <h3 id="image-viewer-title" style="color:#fff;margin:0;font-weight:400;text-shadow:0 2px 4px rgba(0,0,0,0.5);"></h3>
                <button onclick="document.getElementById('image-viewer-overlay').style.display='none'" style="background:none;border:none;color:#fff;font-size:1.5rem;cursor:pointer;text-shadow:0 2px 4px rgba(0,0,0,0.5);"><i class="fa-solid fa-xmark"></i></button>
            </div>
            <img id="image-viewer-img" src="" style="max-width:90%;max-height:80vh;object-fit:contain;border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,0.5);">
            <div style="position:absolute;bottom:2rem;display:flex;gap:1rem;">
                <button id="image-viewer-download" class="fm-btn" style="background:rgba(255,255,255,0.15);border:1px solid rgba(255,255,255,0.3);padding:0.6rem 1.2rem;font-size:1rem;"><i class="fa-solid fa-download"></i> Download</button>
            </div>
        `;
        document.body.appendChild(viewer);
    }
    
    document.getElementById('image-viewer-title').textContent = item.name;
    const imgUrl = '/api/files/download?path=' + encodeURIComponent(item.path);
    document.getElementById('image-viewer-img').src = imgUrl;
    
    document.getElementById('image-viewer-download').onclick = function() {
        window.open(imgUrl, '_blank');
    };
    
    viewer.style.display = 'flex';
}

function openSelected() {
    if (selectedItem) openItem(selectedItem);
    hideContextMenu();
}

// Context Menu
function showContextMenu(e, item, tr) {
    e.preventDefault();
    selectRow(tr, item);

    const menu = document.getElementById('context-menu');
    menu.classList.remove('hidden');
    menu.style.left = e.clientX + 'px';
    menu.style.top = e.clientY + 'px';

    // Adjust if off-screen
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth) menu.style.left = (window.innerWidth - rect.width - 10) + 'px';
    if (rect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - rect.height - 10) + 'px';
}

function hideContextMenu() {
    document.getElementById('context-menu').classList.add('hidden');
}

// Actions
function promptAsync(message, defaultValue = '') {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed; inset:0; background:rgba(0,0,0,0.6); backdrop-filter:blur(5px); z-index:99999; display:flex; justify-content:center; align-items:center; opacity:0; transition:opacity 0.2s;';
        
        const card = document.createElement('div');
        card.style.cssText = 'background:var(--bg-elevated, #1c2128); border:1px solid var(--glass-border, rgba(255,255,255,0.1)); border-radius:12px; padding:1.5rem; width:400px; max-width:90%; box-shadow:0 12px 40px rgba(0,0,0,0.5); transform:translateY(20px); transition:transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275); color:var(--text-main, #fff);';
        
        card.innerHTML = `
            <div style="font-weight:600; margin-bottom:1rem; font-size:1.1rem;">${message}</div>
            <input type="text" id="custom-prompt-input" value="${defaultValue}" style="width:100%; padding:0.6rem 0.8rem; border-radius:6px; border:1px solid var(--glass-border, rgba(255,255,255,0.2)); background:rgba(0,0,0,0.2); color:#fff; font-family:inherit; outline:none; margin-bottom:1.5rem;" autocomplete="off">
            <div style="display:flex; justify-content:flex-end; gap:0.5rem;">
                <button id="custom-prompt-cancel" style="background:rgba(255,255,255,0.1); border:none; color:#fff; padding:0.5rem 1rem; border-radius:6px; cursor:pointer;">Cancel</button>
                <button id="custom-prompt-ok" style="background:var(--accent, #58a6ff); border:none; color:#0f1117; font-weight:600; padding:0.5rem 1rem; border-radius:6px; cursor:pointer;">OK</button>
            </div>
        `;
        
        overlay.appendChild(card);
        document.body.appendChild(overlay);
        
        const input = document.getElementById('custom-prompt-input');
        const btnCancel = document.getElementById('custom-prompt-cancel');
        const btnOk = document.getElementById('custom-prompt-ok');
        
        setTimeout(() => {
            overlay.style.opacity = '1';
            card.style.transform = 'translateY(0)';
            input.focus();
            input.select();
        }, 10);
        
        function close(val) {
            overlay.style.opacity = '0';
            card.style.transform = 'translateY(20px)';
            setTimeout(() => {
                overlay.remove();
                resolve(val);
            }, 200);
        }
        
        btnCancel.onclick = () => close(null);
        btnOk.onclick = () => close(input.value);
        input.onkeydown = (e) => {
            if (e.key === 'Enter') close(input.value);
            if (e.key === 'Escape') close(null);
        };
    });
}

async function createNew(type) {
    const name = await promptAsync(`Enter name for new ${type}:`);
    if (!name) return;

    const path = currentPath === '/' ? `/${name}` : `${currentPath}/${name}`;

    fetch('/api/files/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            action: type === 'folder' ? 'create_folder' : 'create_file',
            path: path
        })
    }).then(refreshAfterAction);
}

async function renameItem() {
    hideContextMenu();
    if (!selectedItem) return;
    const newName = await promptAsync("Rename to:", selectedItem.name);
    if (!newName || newName === selectedItem.name) return;

    const dir = currentPath === '/' ? '' : currentPath;
    const newPath = `${dir}/${newName}`;

    fetch('/api/files/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            action: 'rename',
            path: selectedItem.path,
            new_path: newPath
        })
    }).then(refreshAfterAction);
}

function deleteItem() {
    hideContextMenu();
    if (!selectedItem) return;
    if (!confirm(`Delete "${selectedItem.name}"? This cannot be undone.`)) return;

    fetch('/api/files/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            action: 'delete',
            path: selectedItem.path
        })
    }).then(refreshAfterAction);
}

function copyItem(op) {
    hideContextMenu();
    if (!selectedItem) return;
    clipboard = { path: selectedItem.path, op: op };
}

function pasteItem() {
    hideContextMenu();
    if (!clipboard) return;

    fetch('/api/files/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            action: 'paste',
            path: currentPath,
            source: clipboard.path,
            operation: clipboard.op
        })
    }).then(r => r.json()).then(data => {
        if (data.success) {
            clipboard = null;
            loadFiles(currentPath);
        } else {
            alert("Paste failed: " + (data.error || 'Unknown'));
        }
    });
}

function refreshAfterAction(res) {
    res.json().then(data => {
        if (data.success) loadFiles(currentPath);
        else alert("Action failed: " + (data.error || 'Unknown'));
    });
}

// Toggle Functions
function toggleCompact() {
    document.body.classList.toggle('compact');
    document.getElementById('btn-compact').classList.toggle('active');
}

function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('collapsed');
    document.getElementById('btn-sidebar').classList.toggle('active');
}

// Editor
const editorOverlay = document.getElementById('editor-overlay');
const editorText = document.getElementById('editor-textarea');
const editorTitle = document.getElementById('editor-filename');
let currentEditingPath = null;

function openEditor(item) {
    if (!item || item.is_dir) return;
    hideContextMenu();

    editorOverlay.style.display = 'flex';
    editorTitle.textContent = item.name;
    currentEditingPath = item.path;
    editorText.value = "Loading...";

    fetch(`/api/files/content?path=${encodeURIComponent(item.path)}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                editorText.value = "Error: " + data.error;
            } else {
                editorText.value = data.content;
            }
        });
}

function saveFile() {
    if (!currentEditingPath) return;

    fetch('/api/files/content', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            path: currentEditingPath,
            content: editorText.value
        })
    }).then(r => r.json()).then(data => {
        if (data.success) {
            alert("Saved!");
            closeEditor();
        } else {
            alert("Error: " + data.error);
        }
    });
}

function closeEditor() {
    editorOverlay.style.display = 'none';
    currentEditingPath = null;
    editorText.value = '';
}

// Search / Filter
let allItems = [];

function filterFiles(query) {
    if (!query) {
        // Reload to show all
        loadFiles(currentPath);
        return;
    }

    const lowerQuery = query.toLowerCase();
    const tbody = document.getElementById('file-list');
    const rows = tbody.querySelectorAll('tr');

    rows.forEach(row => {
        const name = row.querySelector('td')?.textContent?.toLowerCase() || '';
        row.style.display = name.includes(lowerQuery) ? '' : 'none';
    });
}

function clearSearch() {
    document.getElementById('search-input').value = '';
    loadFiles(currentPath);
}

// Close detail panel
function closeDetailPanel() {
    selectedItem = null;
    document.querySelectorAll('.fm-table tr').forEach(r => r.classList.remove('selected'));
    document.getElementById('detail-panel').classList.add('hidden');
}

// Open in Terminal - Integration
function openInTerminal() {
    hideContextMenu();
    let path = currentPath;
    if (selectedItem && selectedItem.is_dir) {
        path = selectedItem.path;
    }
    window.location.href = '/terminal?path=' + encodeURIComponent(path);
}

// Upload Files
function uploadFiles(files) {
    if (!files || files.length === 0) return;

    // Guard: must be inside a real folder
    if (!currentPath || currentPath === 'drives://') {
        alert('Please navigate into a folder before uploading.');
        return;
    }

    const formData = new FormData();
    formData.append('path', currentPath);

    for (let i = 0; i < files.length; i++) {
        formData.append('file', files[i]);
    }

    document.getElementById('status-text').textContent = `Uploading ${files.length} file(s)...`;

    fetch('/api/files/upload', {
        method: 'POST',
        body: formData
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                document.getElementById('status-text').textContent = `Uploaded: ${data.files.join(', ')}`;
                loadFiles(currentPath);
            } else {
                alert('Upload failed: ' + (data.error || 'Unknown error'));
                document.getElementById('status-text').textContent = 'Upload failed';
            }
        })
        .catch(err => {
            alert('Upload error: ' + err);
            document.getElementById('status-text').textContent = 'Upload error';
        })
        .finally(() => {
            document.getElementById('upload-input').value = '';
        });
}

// Download Selected File
function downloadSelected() {
    if (!selectedItem) {
        alert('Pilih file untuk di-download');
        return;
    }
    if (selectedItem.is_dir) {
        alert('Tidak bisa download folder');
        return;
    }

    // Open download in new tab
    window.open('/api/files/download?path=' + encodeURIComponent(selectedItem.path), '_blank');
}

function toggleLocationMenu(event) {
    const menu = document.getElementById('loc-dropdown');
    if (menu) menu.classList.toggle('hidden');
}




// --- DRAG AND DROP UPLOAD ---
function setupDragAndDrop() {
    const overlay = document.getElementById('drop-overlay');
    if (!overlay) return;
    
    let dragCounter = 0;

    document.addEventListener('dragenter', (e) => {
        e.preventDefault();
        dragCounter++;
        if (viewMode === 'files') {
            overlay.classList.add('active');
        }
    });

    document.addEventListener('dragleave', (e) => {
        e.preventDefault();
        dragCounter--;
        if (dragCounter === 0) {
            overlay.classList.remove('active');
        }
    });

    document.addEventListener('dragover', (e) => {
        e.preventDefault();
    });

    document.addEventListener('drop', (e) => {
        e.preventDefault();
        dragCounter = 0;
        overlay.classList.remove('active');

        if (viewMode !== 'files') {
            alert('Please open a folder first before uploading.');
            return;
        }
        


        const files = e.dataTransfer.files;
        if (files.length > 0) {
            uploadFiles(files);
        }
    });
}

function uploadFiles(files) {
    const formData = new FormData();
    formData.append('path', currentPath);
    for (let i = 0; i < files.length; i++) {
        formData.append('file', files[i]);
    }

    document.getElementById('status-text').textContent = `Uploading ${files.length} file(s)...`;

    fetch('/api/files/upload', {
        method: 'POST',
        body: formData
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) {
            alert('Upload failed: ' + data.error);
        }
        loadFiles(currentPath);
    })
    .catch(err => {
        console.error(err);
        alert('Upload error');
        loadFiles(currentPath);
    });
}
