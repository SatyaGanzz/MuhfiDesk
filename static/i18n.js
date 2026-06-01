// i18n.js - Internationalization System
let translations = {};
let currentLang = 'en'; // Default language

// Load translations
async function loadTranslations() {
    try {
        const response = await fetch('/static/translations.json');
        translations = await response.json();
        
        // Get saved language from settings
        const settingsResponse = await fetch('/api/settings');
        const settings = await settingsResponse.json();
        currentLang = settings.general?.language || 'en';
        
        // Apply translations
        applyTranslations();
    } catch (error) {
        console.error('Failed to load translations:', error);
    }
}

// Get translation
function t(key) {
    if (!translations[currentLang]) return key;
    return translations[currentLang][key] || key;
}

// Apply translations to all elements with data-i18n attribute
function applyTranslations() {
    document.querySelectorAll('[data-i18n]').forEach(element => {
        const key = element.getAttribute('data-i18n');
        const translated = t(key);
        
        // Check if it's a placeholder
        if (element.hasAttribute('placeholder')) {
            element.setAttribute('placeholder', translated);
        } else if (element.tagName === 'INPUT' && element.type === 'button') {
            element.value = translated;
        } else {
            element.textContent = translated;
        }
    });
    
    // Update page title if exists
    const titleElement = document.querySelector('title[data-i18n]');
    if (titleElement) {
        const key = titleElement.getAttribute('data-i18n');
        document.title = t(key);
    }
}

// Change language
async function changeLanguage(lang) {
    currentLang = lang;
    
    // Save to settings
    try {
        const response = await fetch('/api/settings');
        const settings = await response.json();
        settings.general.language = lang;
        
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        
        // Apply translations
        applyTranslations();
        
        // Reload page to apply translations everywhere
        setTimeout(() => {
            window.location.reload();
        }, 500);
    } catch (error) {
        console.error('Failed to save language:', error);
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', loadTranslations);
