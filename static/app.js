let processedIds = new Set();
let isSoundEnabled = true;
let showFiltered = false; // 除外商品を表示するかどうかのフラグ
let siteConfigs = {};
let keepaCsvStatus = null;
let lastVisibleResults = [];
const CUSTOM_PRESET_STORAGE_KEY = 'msResearch.customPreset';

function formatFileSize(bytes) {
    const size = Number(bytes || 0);
    if (size <= 0) return '';
    if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))}KB`;
    return `${(size / (1024 * 1024)).toFixed(1)}MB`;
}

function getMonthlySalesValue(value) {
    const text = String(value || '').trim();
    if (!text || text === '—' || text === 'データなし') return 0;
    const match = text.match(/\d+/g);
    return match ? parseInt(match.join(''), 10) : 0;
}

function getPresetDefinitions() {
    return {
        none: { label: '条件プリセット', matches: () => true },
        standard: {
            label: '標準',
            matches: item => Number(item.profit || 0) >= 100 && getPercentNumber(item.roi) >= 10,
        },
        high_rotation: {
            label: '高回転重視',
            matches: item => Number(item.profit || 0) >= 0 && getMonthlySalesValue(item.monthly_sales) >= 30,
        },
        high_profit: {
            label: '高利益重視',
            matches: item => Number(item.profit || 0) >= 500 && getPercentNumber(item.roi) >= 15,
        },
        approval_focus: {
            label: '申請入口チェック',
            matches: item => Number(item.profit || 0) >= 100 && item.restriction_code === 'APPROVAL_REQUIRED',
        },
        custom_saved: {
            label: '保存した条件',
            matches: () => true,
        },
    };
}

function toggleControlPanel() {
    const panelBody = document.getElementById('controlPanelBody');
    const toggleBtn = document.getElementById('togglePanelBtn');
    if (!panelBody || !toggleBtn) return;

    const isCollapsed = panelBody.classList.toggle('collapsed');
    toggleBtn.textContent = isCollapsed ? '▼ 設定を表示' : '▲ 設定を隠す';
    toggleBtn.setAttribute('aria-expanded', isCollapsed ? 'false' : 'true');
}

// --- Tab Management ---
function switchTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    
    if (tabName === 'research') {
        document.querySelector('button[onclick*="research"]').classList.add('active');
        document.getElementById('researchTab').classList.add('active');
    } else {
        document.querySelector('button[onclick*="settings"]').classList.add('active');
        document.getElementById('settingsTab').classList.add('active');
        fetchBrands();
    }
}

// --- Brand Management ---
async function fetchBrands() {
    try {
        const res = await fetch('/brands');
        const data = await res.json();
        const brands = data.brands || []; // Access data.brands as assigned in API
        const list = document.getElementById('brandList');
        
        if (!list) return;

        if (brands.length === 0) {
            list.innerHTML = '<p style="text-align: center; color: #999; padding: 2rem;">登録されたブランドはありません。</p>';
        } else {
            list.innerHTML = brands.map(brand => `
                <div class="brand-item">
                    <span style="font-weight: 500;">${brand}</span>
                    <button class="btn-delete" onclick="deleteBrand('${brand}')">×</button>
                </div>
            `).join('');
        }
    } catch (err) {
        console.error("Failed to fetch brands:", err);
    }
}

async function addBrand() {
    const input = document.getElementById('newBrandName');
    const brand = input.value.trim();
    if (!brand) return;

    const res = await fetch('/brands', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ brand })
    });
    
    if (res.status === 200) {
        input.value = '';
        fetchBrands();
    }
}

async function deleteBrand(brand) {
    if (!confirm(`ブランド「${brand}」を削除しますか？`)) return;
    const res = await fetch(`/brands/${encodeURIComponent(brand)}`, { method: 'DELETE' });
    if (res.status === 200) fetchBrands();
}

// --- Sound Notification ---
function playNotificationSound() {
    if (document.getElementById('soundSettings').value === 'off') return;
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const oscillator = audioCtx.createOscillator();
    const gainNode = audioCtx.createGain();

    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(880, audioCtx.currentTime);
    oscillator.frequency.exponentialRampToValueAtTime(440, audioCtx.currentTime + 0.5);
    
    gainNode.gain.setValueAtTime(0.1, audioCtx.currentTime);
    gainNode.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.5);

    oscillator.connect(gainNode);
    gainNode.connect(audioCtx.destination);

    oscillator.start();
    oscillator.stop(audioCtx.currentTime + 0.5);
}

// --- Dynamic Categories & Sort ---
async function loadSiteConfigs() {
    try {
        const res = await fetch('/site-configs');
        const data = await res.json();
        siteConfigs = data.sites || {};
    } catch (err) {
        console.error("Failed to load site configs:", err);
        siteConfigs = {};
    }
}

async function fetchKeepaCsvStatus() {
    try {
        const res = await fetch('/keepa-csv/status');
        keepaCsvStatus = await res.json();
        renderKeepaCsvStatus();
    } catch (err) {
        console.error("Failed to load Keepa CSV status:", err);
    }
}

function renderKeepaCsvStatus() {
    const statusEl = document.getElementById('keepaCsvStatus');
    if (!statusEl) return;

    if (!keepaCsvStatus || !keepaCsvStatus.loaded) {
        statusEl.textContent = 'CSV未読込';
        statusEl.style.color = '#666';
        return;
    }

    const sizeLabel = formatFileSize(keepaCsvStatus.file_size_bytes);
    const sizeText = sizeLabel ? ` | ${sizeLabel}` : '';
    statusEl.textContent = `${keepaCsvStatus.filename} | ${keepaCsvStatus.indexed_eans}件一致用インデックス${sizeText} | ${keepaCsvStatus.loaded_at}`;
    statusEl.style.color = '#2d5a27';
}

function updateKeepaCsvFileLabel() {
    const input = document.getElementById('keepaCsvFile');
    const label = document.getElementById('keepaCsvFilename');
    if (!input || !label) return;

    const file = input.files?.[0];
    label.textContent = file ? file.name : '選択されていません';
}

async function uploadKeepaCsv() {
    const input = document.getElementById('keepaCsvFile');
    const statusEl = document.getElementById('keepaCsvStatus');
    const file = input?.files?.[0];
    if (!file) {
        alert('Keepa CSV を選んでください。');
        return;
    }

    try {
        if (statusEl) {
            statusEl.textContent = `読込中... ${file.name} | ${formatFileSize(file.size)}`;
            statusEl.style.color = '#3f6fb6';
        }

        const res = await fetch('/keepa-csv/upload', {
            method: 'POST',
            headers: {
                'Content-Type': 'text/csv',
                'X-Filename': file.name,
            },
            body: file,
        });

        const result = await res.json();
        if (!res.ok || result.status !== 'success') {
            renderKeepaCsvStatus();
            alert(result.message || 'Keepa CSV の読込に失敗しました。');
            return;
        }

        keepaCsvStatus = result;
        renderKeepaCsvStatus();
    } catch (err) {
        console.error('Keepa CSV upload failed:', err);
        renderKeepaCsvStatus();
        alert('Keepa CSV の読込に失敗しました。');
    }
}

function updateCategories() {
    const targetSite = document.getElementById('targetSite').value;
    const sortSelect = document.getElementById('sortOrder');
    const customUrlInput = document.getElementById('customUrl');
    const catContainer = document.getElementById('categoryContainer');
    if (!catContainer || !sortSelect || !customUrlInput) return;

    const config = siteConfigs[targetSite];
    if (!config) return;

    const categories = config.categories || [];
    sortSelect.innerHTML = (config.sort_options || []).map(opt =>
        `<option value="${opt.value}">${opt.label}</option>`
    ).join('');
    customUrlInput.placeholder = config.placeholder || 'https://...';

    catContainer.innerHTML = categories.map(c => `
        <label class="chip-checkbox ${targetSite}">
            <input type="checkbox" name="category" value="${c.value}">
            <span>${c.label}</span>
        </label>`).join('');

    renderMultiSiteOptions();
    updateMatchModeUI();
    updateResearchScopeControls();
}

function renderMultiSiteOptions() {
    const optionsEl = document.getElementById('multiSiteOptions');
    if (!optionsEl) return;

    optionsEl.innerHTML = Object.entries(siteConfigs).map(([siteKey, config]) => `
        <label class="multi-site-chip">
            <input type="checkbox" name="targetSiteMulti" value="${siteKey}">
            <span>${config.display_name || siteKey}</span>
        </label>
    `).join('');
}

function updateMatchModeUI() {
    const matchMode = document.getElementById('matchMode')?.value || 'realtime';
    const multiSiteSelector = document.getElementById('multiSiteSelector');
    const targetSiteSelect = document.getElementById('targetSite');
    const customUrlInput = document.getElementById('customUrl');
    const categoryContainer = document.getElementById('categoryContainer');

    const isAllSitesCsv = matchMode === 'all_sites_csv';
    if (multiSiteSelector) {
        multiSiteSelector.style.display = isAllSitesCsv ? 'block' : 'none';
    }
    if (targetSiteSelect) {
        targetSiteSelect.disabled = false;
    }
    if (customUrlInput) {
        customUrlInput.disabled = isAllSitesCsv;
        if (isAllSitesCsv) {
            customUrlInput.value = '';
            customUrlInput.placeholder = '全サイトCSV照合ではカスタムURLは使いません';
        } else {
            const targetSite = targetSiteSelect?.value;
            customUrlInput.placeholder = siteConfigs[targetSite]?.placeholder || 'https://...';
        }
    }
    if (categoryContainer) {
        categoryContainer.style.opacity = isAllSitesCsv ? '0.45' : '1';
        categoryContainer.style.pointerEvents = isAllSitesCsv ? 'none' : 'auto';
    }
}

function updateResearchScopeControls() {
    const autoPageMode = document.getElementById('autoPageMode');
    const fullCategoryMode = document.getElementById('fullCategoryMode');
    const startPage = document.getElementById('startPage');
    const endPage = document.getElementById('endPage');
    const maxItems = document.getElementById('maxItems');

    if (!autoPageMode || !fullCategoryMode || !startPage || !endPage || !maxItems) return;

    if (fullCategoryMode.checked) {
        autoPageMode.checked = true;
    }

    autoPageMode.disabled = fullCategoryMode.checked;
    startPage.disabled = autoPageMode.checked;
    endPage.disabled = autoPageMode.checked;
    maxItems.disabled = fullCategoryMode.checked;

    if (fullCategoryMode.checked) {
        maxItems.title = 'カテゴリ全件モードでは取得上限を使いません';
    } else {
        maxItems.title = '';
    }
}

function populateConditionPresetOptions(presets = []) {
    const presetSelect = document.getElementById('presetFilter');
    if (!presetSelect) return;
    const currentValue = presetSelect.value || 'none';

    const definitions = getPresetDefinitions();
    const options = [{ value: 'none', label: '条件プリセット' }];
    presets.forEach(preset => {
        if (definitions[preset.value]) {
            options.push({ value: preset.value, label: preset.label });
        }
    });
    if (!options.some(option => option.value === 'custom_saved')) {
        options.push({ value: 'custom_saved', label: '保存した条件' });
    }

    presetSelect.innerHTML = options
        .map(option => `<option value="${option.value}">${option.label}</option>`)
        .join('');
    presetSelect.value = options.some(option => option.value === currentValue) ? currentValue : 'none';
}

function applyConditionPreset() {
    const presetValue = document.getElementById('presetFilter')?.value || 'none';
    if (presetValue === 'custom_saved') {
        const raw = localStorage.getItem(CUSTOM_PRESET_STORAGE_KEY);
        if (!raw) {
            alert('保存した条件がまだありません。');
            document.getElementById('presetFilter').value = 'none';
            filterResults();
            return;
        }
        try {
            const saved = JSON.parse(raw);
            if (saved.resultMode) document.getElementById('resultModeFilter').value = saved.resultMode;
            if (saved.sortMode) document.getElementById('sortMode').value = saved.sortMode;
            if (saved.profitFilter) document.getElementById('profitFilter').value = saved.profitFilter;
            if (saved.restrictionFilter) document.getElementById('restrictionFilter').value = saved.restrictionFilter;
            if (typeof saved.pinFavorites === 'boolean') document.getElementById('pinFavoritesToggle').checked = saved.pinFavorites;
        } catch (err) {
            console.error('Failed to restore custom preset', err);
        }
    }
    filterResults();
}

function saveCurrentPreset() {
    const payload = {
        resultMode: document.getElementById('resultModeFilter')?.value || 'all',
        sortMode: document.getElementById('sortMode')?.value || 'newest',
        profitFilter: document.getElementById('profitFilter')?.value || 'all',
        restrictionFilter: document.getElementById('restrictionFilter')?.value || 'all',
        pinFavorites: document.getElementById('pinFavoritesToggle')?.checked || false,
    };
    localStorage.setItem(CUSTOM_PRESET_STORAGE_KEY, JSON.stringify(payload));
    const presetSelect = document.getElementById('presetFilter');
    if (presetSelect) {
        presetSelect.value = 'custom_saved';
    }
    filterResults();
}

function matchesPreset(item, presetValue) {
    if (!presetValue || presetValue === 'none' || presetValue === 'custom_saved') {
        return true;
    }
    const definitions = getPresetDefinitions();
    return definitions[presetValue]?.matches ? definitions[presetValue].matches(item) : true;
}

async function favoriteVisibleWatchItems() {
    const watchIds = lastVisibleResults
        .filter(item => Number(item.profit || 0) >= 0 && Number(item.profit || 0) <= 99)
        .map(item => item.id);

    if (watchIds.length === 0) {
        alert('今表示されている監視候補はありません。');
        return;
    }

    const res = await fetch('/results/watch/favorite_bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: watchIds }),
    });
    const result = await res.json();
    if (!res.ok || result.status !== 'success') {
        alert('監視候補の保存に失敗しました。');
        return;
    }

    if (lastData) {
        lastData.results.forEach(item => {
            if (watchIds.includes(item.id)) {
                item.is_favorite = 1;
            }
        });
        updateResultsList(lastData, true);
    }
}

// --- Start Research ---
async function startResearch() {
    const targetSite = document.getElementById('targetSite').value;
    const targetSites = Array.from(document.querySelectorAll('input[name="targetSiteMulti"]:checked')).map(cb => cb.value);
    const customUrl = document.getElementById('customUrl').value;
    const maxItems = document.getElementById('maxItems').value;
    const startPage = document.getElementById('startPage').value;
    const endPage = document.getElementById('endPage').value;
    const sortOrder = document.getElementById('sortOrder').value;
    const autoPageMode = document.getElementById('autoPageMode').checked;
    const fullCategoryMode = document.getElementById('fullCategoryMode').checked;
    const matchMode = document.getElementById('matchMode').value;
    const focusMode = document.getElementById('focusMode').checked;
    const skipHistory = document.getElementById('skipHistory').checked;
    const monitorMode = document.getElementById('monitorMode').checked;
    const startBtn = document.getElementById('startBtn');
    
    const checkboxes = document.querySelectorAll('input[name="category"]:checked');
    // カスタムURLがある場合は、チェックボックスのカテゴリーを完全に無視する（最優先ルール）
    const categories = (customUrl && customUrl.trim().startsWith('http')) ? [] : Array.from(checkboxes).map(cb => cb.value);

    if (matchMode === 'all_sites_csv' && targetSites.length === 0) {
        alert('全サイトCSV照合では、対象サイトを1つ以上選んでください。');
        return;
    }

    if (matchMode !== 'all_sites_csv' && !customUrl.trim() && categories.length === 0) {
        alert('ジャンルを1つ以上選ぶか、カスタムURLを入力してください。');
        return;
    }

    startBtn.style.display = 'none';
    const stopBtn = document.getElementById('stopBtn');
    if(stopBtn) stopBtn.style.display = 'block';

    document.getElementById('progressSection').style.display = 'block';
    document.getElementById('resultsList').innerHTML = '';
    processedIds.clear();

    const response = await fetch('/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
            target_site: targetSite,
            target_sites: targetSites,
            categories: categories, 
            custom_url: customUrl || null,
            max_items: parseInt(maxItems || '50', 10),
            start_page: parseInt(startPage || '1', 10),
            end_page: parseInt(endPage || '1', 10),
            sort_order: sortOrder,
            auto_page_mode: autoPageMode,
            full_category_mode: fullCategoryMode,
            match_mode: matchMode,
            focus_mode: focusMode,
            skip_history: skipHistory,
            monitor_mode: monitorMode
        })
    });

    const result = await response.json();
    if (result.status === 'error') {
        alert(result.message);
        startBtn.style.display = 'block';
        if(stopBtn) stopBtn.style.display = 'none';
    }
}

// --- Stop / Clear Research ---
async function stopResearch() {
    await fetch('/stop', { method: 'POST' });
    // 停止後に即座にUIを更新（SSEのレスポンスを待たずにボタンを復活させる）
    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    if (startBtn) {
        startBtn.style.display = 'block';
        startBtn.innerText = 'リサーチ開始';
        startBtn.disabled = false;
    }
    if (stopBtn) stopBtn.style.display = 'none';
}

// --- Filter Toggle ---
function toggleFilteredItems() {
    showFiltered = !showFiltered;
    const btn = document.getElementById('toggleFilteredBtn');
    if (btn) {
        btn.innerHTML = showFiltered ? '🔽 除外を隠す' : '🔼 除外商品も表示';
        // アクティブな時の色味を変えるためのクラス切り替え
        btn.classList.toggle('active', showFiltered);
    }
    filterResults();
}

async function toggleFavorite(resId, currentStatus) {
    const nextStatus = !currentStatus;
    try {
        const res = await fetch(`/results/${resId}/toggle_favorite`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: nextStatus })
        });
        if (res.ok && lastData) {
            const item = lastData.results.find(r => r.id === resId);
            if (item) item.is_favorite = nextStatus ? 1 : 0;
            updateResultsList(lastData, true); // 強制描画
        }
    } catch (err) {
        console.error("Favorite toggle failed:", err);
    }
}

async function toggleChecked(resId, currentStatus) {
    const nextStatus = !currentStatus;
    try {
        const res = await fetch(`/results/${resId}/toggle_checked`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: nextStatus })
        });
        if (res.ok && lastData) {
            const item = lastData.results.find(r => r.id === resId);
            if (item) item.is_checked = nextStatus ? 1 : 0;
            updateResultsList(lastData, true); // 強制描画
        }
    } catch (err) {
        console.error("Check toggle failed:", err);
    }
}


// --- Custom Modal Logic ---
function openConfirmModal() {
    const modal = document.getElementById('confirmModal');
    if (modal) modal.style.display = 'flex';
}

function closeConfirmModal() {
    const modal = document.getElementById('confirmModal');
    if (modal) modal.style.display = 'none';
}

async function executeClearResults() {
    closeConfirmModal();
    
    // 即座にシャッターを閉める（サーバーからの古い定期便を無視開始）
    window.isResetting = true;
    setTimeout(() => { window.isResetting = false; }, 3000); // 念のため3秒に延長
    
    try {
        const response = await fetch('/results/clear?t=' + Date.now(), { method: 'POST' });
        const result = await response.json();
        if (result.status === 'success') {
            // Force clear UI immediately
            document.getElementById('resultsList').innerHTML = '';
            document.getElementById('resCount').textContent = '0件';
            const recList = document.getElementById('recommendationsList');
            recList.innerHTML = '<p style="color: #888; font-size: 0.9rem;">分析ヒントがここに表示されます。</p>';
            recList.dataset.lastRecData = "RESET_LOCKED";
            
            // ライブログもリセット
            const logBox = document.getElementById('logConsole');
            if (logBox) logBox.innerHTML = '';

            processedIds.clear();
            if (lastData) {
                lastData.results = [];
                lastData.recommendations = [];
                lastData.logs = [];
            }
            
            // フィードバック
            const btn = document.getElementById('resetBtn');
            const originalText = btn.innerHTML;
            btn.innerHTML = '✅ クリア完了';
            setTimeout(() => { btn.innerHTML = originalText; }, 2000);
        }
    } catch (e) {
        window.isResetting = false; // エラー時は解除
        console.error("Clear error:", e);
    }
}


async function clearResults() {
    openConfirmModal(); // ネイティブダイアログを廃止してモーダルに
}


// --- Filtering ---
let lastData = null;

function getPercentNumber(value) {
    const match = String(value || '').match(/-?\d+/);
    return match ? parseInt(match[0], 10) : 0;
}

function getResultSortComparator() {
    const sortMode = document.getElementById('sortMode')?.value || 'newest';
    return (a, b) => {
        if (sortMode === 'profit_desc') {
            return (b.profit || 0) - (a.profit || 0);
        }
        if (sortMode === 'roi_desc') {
            return getPercentNumber(b.roi) - getPercentNumber(a.roi);
        }
        if (sortMode === 'price_asc') {
            return (a.price || 0) - (b.price || 0);
        }

        const aCreated = Date.parse(a.created_at || 0);
        const bCreated = Date.parse(b.created_at || 0);
        return bCreated - aCreated;
    };
}

function sortVisibleResults(results) {
    const pinFavorites = document.getElementById('pinFavoritesToggle')?.checked;
    const baseSort = getResultSortComparator();

    return results.slice().sort((a, b) => {
        if (pinFavorites) {
            const favDiff = (b.is_favorite === 1) - (a.is_favorite === 1);
            if (favDiff !== 0) return favDiff;
        }

        const checkedDiff = (b.is_checked === 1) - (a.is_checked === 1);
        if (checkedDiff !== 0) return checkedDiff;

        const baseDiff = baseSort(a, b);
        if (baseDiff !== 0) return baseDiff;

        return String(a.id || '').localeCompare(String(b.id || ''));
    });
}

function getRestrictionCategory(item) {
    if (item.restriction && item.restriction.includes('出品可能')) {
        return 'eligible';
    }
    if (item.restriction_code === 'APPROVAL_REQUIRED') {
        return 'approval';
    }
    if (item.restriction_code === 'NOT_ELIGIBLE') {
        return 'not_eligible';
    }
    return 'other';
}

function matchesProfitFilter(item, profitFilterValue) {
    const profit = Number(item.profit || 0);

    if (profitFilterValue === 'all') {
        return true;
    }
    if (profitFilterValue === 'profit_100') {
        return profit >= 100;
    }
    if (profitFilterValue === 'watch_0_99') {
        return profit >= 0 && profit <= 99;
    }
    if (profitFilterValue === 'non_negative') {
        return profit >= 0;
    }

    const minProfit = parseInt(profitFilterValue, 10);
    return Number.isNaN(minProfit) ? true : profit >= minProfit;
}

function getProfitBand(item) {
    const profit = Number(item.profit || 0);
    if (profit >= 100) {
        return 'profit';
    }
    if (profit >= 0) {
        return 'watch';
    }
    return 'loss';
}

function filterResults() {
    if (!lastData) return;
    updateResultsList(lastData);
}

function updateResultsList(data, force = false) {
    const resultsList = document.getElementById('resultsList');
    const searchTerm = document.getElementById('tableSearch').value.toLowerCase();
    const profitFilterValue = document.getElementById('profitFilter')?.value || 'all';
    const resCount = document.getElementById('resCount');
    const resultMode = document.getElementById('resultModeFilter')?.value || 'all';
    const restrictionFilter = document.getElementById('restrictionFilter')?.value || 'all';
    const presetValue = document.getElementById('presetFilter')?.value || 'none';

    const matchedBeforeSystemFilter = data.results.filter(item => {
        const matchesSearch = item.title.toLowerCase().includes(searchTerm) || 
                            item.brand.toLowerCase().includes(searchTerm) ||
                            (item.jan && item.jan.includes(searchTerm));
        const matchesProfit = matchesProfitFilter(item, profitFilterValue);
        const isFavorite = item.is_favorite === 1;
        const isChecked = item.is_checked === 1;
        const matchesMode =
            resultMode === 'all' ||
            (resultMode === 'favorites' && isFavorite) ||
            (resultMode === 'checked' && isChecked);
        const matchesRestriction =
            restrictionFilter === 'all' ||
            getRestrictionCategory(item) === restrictionFilter;
        const matchesSelectedPreset = matchesPreset(item, presetValue);
        
        return matchesSearch && matchesProfit && matchesMode && matchesRestriction && matchesSelectedPreset;
    });

    const filtered = matchedBeforeSystemFilter.filter(item => {
        const isSystemExcluded = item.filter_status === 'filtered';
        if (isSystemExcluded && !showFiltered) return false;
        return true;
    });

    const sortedResults = sortVisibleResults(filtered);
    lastVisibleResults = sortedResults;

    // 除外件数のバッジ表示（「(10件を除外中)」のような表示）
    const filteredCountBadge = document.getElementById('filteredCount');
    if (filteredCountBadge) {
        const hiddenCount = matchedBeforeSystemFilter.length - filtered.length;
        if (hiddenCount > 0 && !showFiltered) {
            filteredCountBadge.textContent = `(${hiddenCount}件を除外中)`;
        } else if (resultMode !== 'all') {
            const modeLabel = resultMode === 'favorites' ? 'お気に入り' : 'チェック済み';
            filteredCountBadge.textContent = `(${modeLabel}表示)`;
        } else if (profitFilterValue === 'profit_100') {
            filteredCountBadge.textContent = '(利益100円以上)';
        } else if (profitFilterValue === 'watch_0_99') {
            filteredCountBadge.textContent = '(監視候補 0〜99円)';
        } else if (profitFilterValue === 'non_negative') {
            filteredCountBadge.textContent = '(赤字なし)';
        } else if (presetValue !== 'none') {
            const definitions = getPresetDefinitions();
            filteredCountBadge.textContent = `(${definitions[presetValue]?.label || '条件プリセット'})`;
        } else {
            filteredCountBadge.textContent = '';
        }
    }

    if (resCount) {
        resCount.innerText = `${sortedResults.length}件`;
    }

    const filteredIds = sortedResults.map(item => item.id).join(',');
    if (!force && resultsList.dataset.lastIds === filteredIds && !data.is_running) return;
    resultsList.dataset.lastIds = filteredIds;

    if (sortedResults.length === 0) {
        const emptyText =
            resultMode === 'favorites'
                ? 'お気に入り登録した商品はまだありません。'
                : resultMode === 'checked'
                    ? 'チェック済みの商品はまだありません。'
                    : '利益商品が見つかるまでお待ちください...';
        resultsList.innerHTML = `
            <tr>
                <td colspan="9" style="text-align: center; color: #aaa; padding: 4rem;">
                    ${emptyText}
                </td>
            </tr>
        `;
        return;
    }

    resultsList.innerHTML = sortedResults.map(item => {
        const profitBand = getProfitBand(item);

        if (profitBand === 'profit' && !processedIds.has(item.id)) {
            processedIds.add(item.id);
            playNotificationSound();
        }

        let restrictionHtml = `<span class="badge badge-dim">${item.restriction || '確認中'}</span>`;
        if (item.restriction && item.restriction.includes('出品可能')) {
            restrictionHtml = `<span class="badge badge-ok">✅ 出品可能</span>`;
        } else if (item.restriction_code === 'APPROVAL_REQUIRED') {
            restrictionHtml = `
                <div style="display: flex; flex-direction: column; align-items: center; gap: 4px;">
                    <span class="badge badge-approval">🚪 申請への入り口 (書類の可能性あり)</span>
                    ${item.approval_url ? `<a href="${item.approval_url}" target="_blank" class="approval-btn">出品申請ページへ</a>` : ''}
                </div>
            `;
        } else if (item.restriction_code === 'NOT_ELIGIBLE') {
            restrictionHtml = `<span class="badge badge-not-eligible">🏰 現在は解除不可</span>`;
        } else if (item.restriction && item.restriction.includes('制限')) {
            restrictionHtml = `
                <div style="display: flex; flex-direction: column; align-items: center; gap: 4px;">
                    <span class="badge badge-ng">⚠️ 制限（詳細な審査が必要）</span>
                    ${item.approval_url ? `<a href="${item.approval_url}" target="_blank" class="approval-btn">出品申請ページへ</a>` : ''}
                </div>
            `;
        }
        let profitBadge = '';
        if (profitBand === 'profit') {
            profitBadge = '<span class="badge badge-profit">利益品</span>';
        } else if (profitBand === 'watch') {
            profitBadge = '<span class="badge" style="background: #fff4db; color: #8a5a00; border: 1px solid #f3c96b;">監視候補</span>';
        }

        const profitSign = item.profit > 0 ? '+' : '';
        const profitColor = item.profit >= 100 ? '#2d5a27' : item.profit >= 0 ? '#8a5a00' : '#c62828';
        
        // Highlight good ranks (if numerical rank < 50k - simplified estimate)
        let rankClass = 'badge-dim';
        const rankMatch = (item.rank || '').match(/\d+/);
        if (rankMatch && parseInt(rankMatch[0]) < 50000) {
            rankClass = 'badge-rank';
        }

        const safeMsUrl = item.ms_url || '';
        const sourceLabel = safeMsUrl.includes('yodobashi') ? 'ヨドバシ' : '仕入先';
        const safeAmzUrl = item.amazon_url || `https://www.amazon.co.jp/dp/${item.asin}`;
        const safeKeepaUrl = item.keepa_url || `https://keepa.com/#!product/5-${item.asin}`;

        const amzListing = item.amazon_listing_price || 0;
        const amzShipping = item.amazon_shipping || 0;
        let amzPriceHtml = `<b>¥${(amzListing + amzShipping).toLocaleString()}</b>`;
        if (amzShipping > 0) {
            amzPriceHtml = `<b>¥${amzListing.toLocaleString()}</b> <span style="font-size: 0.7rem; color: #888;">(+送料¥${amzShipping.toLocaleString()})</span>`;
        } else if (item.amazon_price && amzListing === 0) {
            // Fallback to legacy field format if new format isn't present
            amzPriceHtml = `<b>¥${item.amazon_price.toLocaleString()}</b>`;
        }

        let filterReasonHtml = '';
        if (item.filter_status === 'filtered' && item.filter_reason) {
            filterReasonHtml = `<div style="font-size: 0.75rem; color: #c62828; margin-top: 4px; padding: 2px 4px; background: #ffebee; border-radius: 4px; display: inline-block;">⚠️ 除外理由: ${item.filter_reason}</div>`;
        }

        const isFavorite = item.is_favorite === 1;
        const isChecked = item.is_checked === 1;
        const itemFlags = [
            isFavorite ? '<span class="item-flag favorite">⭐ 監視中</span>' : '',
            isChecked ? '<span class="item-flag checked">✅ 確認メモ</span>' : ''
        ].filter(Boolean).join('');

        const matchInfoHtml = `
            <div class="match-info-row">
                <span class="match-badge">${item.match_label || '照合情報なし'}</span>
                ${item.match_score ? `<span class="match-score">score ${item.match_score}</span>` : ''}
            </div>
            <div class="match-note">${item.match_details || '照合根拠なし'}</div>
        `;

        let watchReasonHtml = '';
        if (profitBand === 'watch' && item.watch_reason) {
            watchReasonHtml = `<div class="watch-note">👀 監視理由: ${item.watch_reason}</div>`;
        }

        let changeHtml = '';
        if (item.change_summary) {
            const profitDelta = Number(item.profit_delta || 0);
            const deltaClass = profitDelta > 0 ? 'up' : profitDelta < 0 ? 'down' : 'flat';
            changeHtml = `<div class="change-note ${deltaClass}">↺ ${item.change_summary}</div>`;
        }

        return `
            <tr class="result-row ${isChecked ? 'checked-row' : ''} ${isFavorite ? 'favorite-row' : ''}">
                <td style="max-width: 250px;">
                    <div style="font-weight: 600; font-size: 0.9rem;">${item.title}</div>
                    <div style="font-size: 0.75rem; color: #777; margin-top: 0.3rem;">${item.brand} | ${item.jan && item.jan !== '—' ? 'JAN: ' + item.jan : 'キーワード照合'}</div>
                    <div style="font-size: 0.72rem; color: #6f7d90; margin-top: 0.2rem;">${item.source_site_label || '不明サイト'} / ${item.source_category_label || 'カテゴリ未設定'}</div>
                    ${matchInfoHtml}
                    ${itemFlags ? `<div class="item-meta-row">${itemFlags}</div>` : ''}
                    ${watchReasonHtml}
                    ${changeHtml}
                    ${filterReasonHtml}
                </td>
                <td>
                    <div style="font-size: 0.85rem; color: #555;">${sourceLabel}: <b>¥${(item.price || 0).toLocaleString()}</b></div>
                    <div style="font-size: 0.85rem; color: #555; white-space: nowrap;">AMZ: ${amzPriceHtml}</div>
                </td>
                <td>
                    <div class="profit-cell">
                        <div style="color: ${profitColor}; font-weight: 700; font-size: 1.1rem;">${profitSign}¥${(item.profit || 0).toLocaleString()}</div>
                        <div style="font-size: 0.75rem; color: #666; display: flex; align-items: center; gap: 0.4rem;">
                            ROI: ${item.roi || '0%'} ${profitBadge}
                        </div>
                    </div>
                </td>
                <td>
                    <div class="rank-cell">
                        <span class="badge ${rankClass}" style="font-size: 0.75rem;">${item.rank || '圏外'}</span>
                    </div>
                </td>
                <td style="text-align: center;">
                    <div style="font-weight: 700; font-size: 1rem; color: #444;">${item.monthly_sales || '—'}</div>
                </td>
                <td style="text-align: center;">
                    <div style="font-weight: 700; font-size: 1rem; color: #444;">${item.sellers || '—'}<span style="font-size: 0.7rem; font-weight: normal; margin-left: 2px;">人</span></div>
                </td>
                <td>${restrictionHtml}</td>
                <td>
                    <div class="link-group">
                        <a href="${safeAmzUrl}" target="_blank" class="link-icon">AMZ</a>
                        <a href="${safeKeepaUrl}" target="_blank" class="link-icon">Keepa</a>
                        <a href="${safeMsUrl}" target="_blank" class="link-icon">仕入先</a>
                    </div>
                </td>
                <td>
                    <div class="mgmt-group">
                        <button class="btn-mgmt btn-fav ${isFavorite ? 'active' : ''}" onclick="toggleFavorite('${item.id}', ${isFavorite})" title="お気に入り">⭐</button>
                        <button class="btn-mgmt btn-check ${isChecked ? 'active' : ''}" onclick="toggleChecked('${item.id}', ${isChecked})" title="チェック完了">✅</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

// --- SSE Event Listener ---
const eventSource = new EventSource('/events');

eventSource.onmessage = function(event) {
    const data = JSON.parse(event.data);
    updateUI(data);
};

function updateStepIndicator(currentStep, isRunning) {
    const steps = [1, 2, 3].map(num => document.getElementById(`step${num}`));
    const connectors = [1, 2].map(num => document.getElementById(`conn${num}`));

    steps.forEach((stepEl, index) => {
        if (!stepEl) return;
        stepEl.classList.remove('active', 'done');

        const stepNumber = index + 1;
        if (isRunning && currentStep === stepNumber) {
            stepEl.classList.add('active');
        } else if (currentStep > stepNumber) {
            stepEl.classList.add('done');
        }
    });

    connectors.forEach((connectorEl, index) => {
        if (!connectorEl) return;
        connectorEl.classList.remove('done');
        const completedStep = index + 1;
        if (currentStep > completedStep) {
            connectorEl.classList.add('done');
        }
    });

    if (!isRunning && currentStep === 0) {
        steps.forEach(stepEl => stepEl && stepEl.classList.remove('active', 'done'));
        connectors.forEach(connectorEl => connectorEl && connectorEl.classList.remove('done'));
    }
}

function formatEtaText(data) {
    if (!data.is_running) {
        return '';
    }

    const totalItems = Number(data.total_items || 0);
    const itemsProcessed = Number(data.items_processed || 0);
    const avgTimePerItem = Number(data.avg_time_per_item || 0);

    if (totalItems <= 0 || avgTimePerItem <= 0 || itemsProcessed >= totalItems) {
        return totalItems > 0 && itemsProcessed >= totalItems ? 'まもなく完了' : '終了目安を計算中';
    }

    const remainingItems = Math.max(totalItems - itemsProcessed, 0);
    const remainingSeconds = Math.round(remainingItems * avgTimePerItem);

    if (remainingSeconds < 60) {
        return `終了目安: 約${remainingSeconds}秒`;
    }

    const minutes = Math.floor(remainingSeconds / 60);
    const seconds = remainingSeconds % 60;
    return seconds > 0
        ? `終了目安: 約${minutes}分${seconds}秒`
        : `終了目安: 約${minutes}分`;
}

function renderRunSummary(data) {
    const cardsEl = document.getElementById('runSummaryCards');
    const highlightsEl = document.getElementById('runSummaryHighlights');
    if (!cardsEl || !highlightsEl) return;

    const cards = data.run_summary?.cards || [];
    const highlights = data.run_summary?.highlights || [];

    if (!cards.length) {
        cardsEl.innerHTML = '<div class="summary-empty">リサーチ後に件数サマリーが表示されます。</div>';
        highlightsEl.innerHTML = '';
        return;
    }

    cardsEl.innerHTML = cards.map(card => {
        const isPriorityCard = card.label === '最優先確認';
        if (isPriorityCard) {
            return `
                <div class="summary-stat-card summary-stat-card-priority">
                    <div class="summary-stat-label">${card.label}</div>
                    <div class="summary-priority-title">${card.value}</div>
                    <div class="summary-priority-sub">${card.subtext || ''}</div>
                </div>
            `;
        }

        return `
            <div class="summary-stat-card">
                <div class="summary-stat-label">${card.label}</div>
                <div class="summary-stat-value">${card.value}</div>
                <div class="summary-stat-sub">${card.subtext || ''}</div>
            </div>
        `;
    }).join('');

    highlightsEl.innerHTML = highlights
        .map(text => `<span class="summary-highlight-chip">${text}</span>`)
        .join('');
}

function renderSiteReport(data) {
    const listEl = document.getElementById('siteReportList');
    if (!listEl) return;

    const rows = data.site_report || [];
    if (!rows.length) {
        listEl.innerHTML = '<div class="summary-empty">サイト別の一致率と利益率がここに並びます。</div>';
        return;
    }

    listEl.innerHTML = rows.map(row => `
        <div class="site-report-row">
            <div class="site-report-main">
                <div class="site-report-title">${row.site_label}</div>
                <div class="site-report-meta">
                    <span>巡回 ${row.total}件</span>
                    <span>一致率 ${row.match_rate}%</span>
                    <span>利益率 ${row.profit_rate}%</span>
                </div>
            </div>
            <div class="site-report-side">
                <span class="site-mini-pill">監視 ${row.watch_count}</span>
                <span class="site-mini-pill muted">除外 ${row.filtered_count}</span>
            </div>
        </div>
    `).join('');
}

function updateUI(data) {
    if (window.isResetting) return; // リセット直後の残像を無視する
    lastData = data;

    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const progressBar = document.getElementById('progressBar');
    const statusText = document.getElementById('statusText');
    const etaText = document.getElementById('etaText');
    const logConsole = document.getElementById('logConsole');
    const recommendationsList = document.getElementById('recommendationsList');
    const resCount = document.getElementById('resCount');

    startBtn.disabled = data.is_running;
    startBtn.style.display = data.is_running ? 'none' : 'block';
    
    if (stopBtn) stopBtn.style.display = data.is_running ? 'inline-block' : 'none';

    if (data.is_running) {
        startBtn.innerText = 'リサーチ中...';
        document.getElementById('progressSection').style.display = 'block';
    } else {
        startBtn.innerText = 'リサーチ開始';
    }

    progressBar.style.width = data.progress + '%';
    statusText.innerText = data.current_status || '待機中';
    if (etaText) {
        etaText.innerText = formatEtaText(data);
    }
    updateStepIndicator(Number(data.current_step || 0), Boolean(data.is_running));

    logConsole.innerHTML = data.logs.map(log => `<div class="log-entry">${log}</div>`).join('');

    const recommendationEmoji = {
        restriction: '🏆',
        profit: '💎',
        watch: '👀',
        csv_gap: '🧩',
        next_action: '💡',
    };
    const recHtml = data.recommendations.map(rec => `
        <div class="recommendation-card">
            <div style="font-weight: 600; font-size: 0.85rem; color: #2d5a27;">
                ${recommendationEmoji[rec.type] || '💡'} ${rec.title || 'AI戦略アドバイス'}
            </div>
            <div style="font-size: 0.9rem; margin-top: 0.3rem;">${rec.message}</div>
        </div>
    `).join('');
    
    // Use data comparison to prevent flicker
    const recDataStr = JSON.stringify(data.recommendations);
    if (recommendationsList.dataset.lastRecData !== recDataStr) {
        recommendationsList.dataset.lastRecData = recDataStr;
        recommendationsList.innerHTML = recHtml || (data.is_running ? '' : '<p style="color: #888; font-size: 0.9rem;">分析ヒントがここに表示されます。</p>');
    }

    populateConditionPresetOptions(data.condition_presets || []);
    renderRunSummary(data);
    renderSiteReport(data);

    updateResultsList(data);
}

// Initial load
document.addEventListener('DOMContentLoaded', async () => {
    fetchBrands();
    await loadSiteConfigs();
    await fetchKeepaCsvStatus();
    updateCategories();
    updateMatchModeUI();
    updateResearchScopeControls();
    
    const resetBtn = document.getElementById('resetBtn');
    if (resetBtn) {
        resetBtn.addEventListener('click', clearResults);
    }
});
