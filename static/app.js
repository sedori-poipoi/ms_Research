let processedIds = new Set();
let isSoundEnabled = true;
let showFiltered = false; // 除外商品を表示するかどうかのフラグ
let siteConfigs = {};

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

function updateCategories() {
    const targetSite = document.getElementById('targetSite').value;
    const sortSelect = document.getElementById('sortOrder');
    const customUrlInput = document.getElementById('customUrl');
    const catContainer = document.getElementById('categoryContainer');
    if (!catContainer || !sortSelect || !customUrlInput) return;

    const config = siteConfigs[targetSite];
    if (!config) return;

    const categories = config.categories || [];
    const defaultCategories = new Set(config.default_categories || []);

    sortSelect.innerHTML = (config.sort_options || []).map(opt =>
        `<option value="${opt.value}">${opt.label}</option>`
    ).join('');
    customUrlInput.placeholder = config.placeholder || 'https://...';

    catContainer.innerHTML = categories.map(c => `
        <label class="chip-checkbox ${targetSite}">
            <input type="checkbox" name="category" value="${c.value}" ${defaultCategories.has(c.value) ? 'checked' : ''}>
            <span>${c.label}</span>
        </label>`).join('');
}

// --- Start Research ---
async function startResearch() {
    const targetSite = document.getElementById('targetSite').value;
    const customUrl = document.getElementById('customUrl').value;
    const maxItems = document.getElementById('maxItems').value;
    const startPage = document.getElementById('startPage').value;
    const endPage = document.getElementById('endPage').value;
    const sortOrder = document.getElementById('sortOrder').value;
    const focusMode = document.getElementById('focusMode').checked;
    const skipHistory = document.getElementById('skipHistory').checked;
    const monitorMode = document.getElementById('monitorMode').checked;
    const startBtn = document.getElementById('startBtn');
    
    const checkboxes = document.querySelectorAll('input[name="category"]:checked');
    // カスタムURLがある場合は、チェックボックスのカテゴリーを完全に無視する（最優先ルール）
    const categories = (customUrl && customUrl.trim().startsWith('http')) ? [] : Array.from(checkboxes).map(cb => cb.value);

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
            categories: categories, 
            custom_url: customUrl || null,
            max_items: parseInt(maxItems), 
            start_page: parseInt(startPage),
            end_page: parseInt(endPage),
            sort_order: sortOrder,
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

function filterResults() {
    if (!lastData) return;
    updateResultsList(lastData);
}

function updateResultsList(data, force = false) {
    const resultsList = document.getElementById('resultsList');
    const searchTerm = document.getElementById('tableSearch').value.toLowerCase();
    const minProfit = parseInt(document.getElementById('profitFilter').value);
    const resCount = document.getElementById('resCount');
    const resultMode = document.getElementById('resultModeFilter')?.value || 'all';
    const restrictionFilter = document.getElementById('restrictionFilter')?.value || 'all';

    const matchedBeforeSystemFilter = data.results.filter(item => {
        const matchesSearch = item.title.toLowerCase().includes(searchTerm) || 
                            item.brand.toLowerCase().includes(searchTerm) ||
                            (item.jan && item.jan.includes(searchTerm));
        const matchesProfit = item.profit >= minProfit;
        const isFavorite = item.is_favorite === 1;
        const isChecked = item.is_checked === 1;
        const matchesMode =
            resultMode === 'all' ||
            (resultMode === 'favorites' && isFavorite) ||
            (resultMode === 'checked' && isChecked);
        const matchesRestriction =
            restrictionFilter === 'all' ||
            getRestrictionCategory(item) === restrictionFilter;
        
        return matchesSearch && matchesProfit && matchesMode && matchesRestriction;
    });

    const filtered = matchedBeforeSystemFilter.filter(item => {
        const isSystemExcluded = item.filter_status === 'filtered';
        if (isSystemExcluded && !showFiltered) return false;
        return true;
    });

    const sortedResults = sortVisibleResults(filtered);

    // 除外件数のバッジ表示（「(10件を除外中)」のような表示）
    const filteredCountBadge = document.getElementById('filteredCount');
    if (filteredCountBadge) {
        const hiddenCount = matchedBeforeSystemFilter.length - filtered.length;
        if (hiddenCount > 0 && !showFiltered) {
            filteredCountBadge.textContent = `(${hiddenCount}件を除外中)`;
        } else if (resultMode !== 'all') {
            const modeLabel = resultMode === 'favorites' ? 'お気に入り' : 'チェック済み';
            filteredCountBadge.textContent = `(${modeLabel}表示)`;
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
        if (item.judgment.includes('利益商品') && !processedIds.has(item.id)) {
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
        const profitBadge = item.judgment.includes('利益商品') ? '<span class="badge badge-profit">利益品</span>' : '';

        const profitSign = item.profit > 0 ? '+' : '';
        const profitColor = item.profit > 0 ? '#2d5a27' : '#c62828';
        
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

        return `
            <tr class="result-row ${isChecked ? 'checked-row' : ''} ${isFavorite ? 'favorite-row' : ''}">
                <td style="max-width: 250px;">
                    <div style="font-weight: 600; font-size: 0.9rem;">${item.title}</div>
                    <div style="font-size: 0.75rem; color: #777; margin-top: 0.3rem;">${item.brand} | ${item.jan && item.jan !== '—' ? 'JAN: ' + item.jan : 'キーワード照合'}</div>
                    ${itemFlags ? `<div class="item-meta-row">${itemFlags}</div>` : ''}
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

function updateUI(data) {
    if (window.isResetting) return; // リセット直後の残像を無視する
    lastData = data;

    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const progressBar = document.getElementById('progressBar');
    const statusText = document.getElementById('statusText');
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

    logConsole.innerHTML = data.logs.map(log => `<div class="log-entry">${log}</div>`).join('');

    const recHtml = data.recommendations.map(rec => `
        <div class="recommendation-card">
            <div style="font-weight: 600; font-size: 0.85rem; color: #2d5a27;">🏆 制限解除推奨</div>
            <div style="font-size: 0.9rem; margin-top: 0.3rem;">${rec.message}</div>
        </div>
    `).join('');
    
    // Use data comparison to prevent flicker
    const recDataStr = JSON.stringify(data.recommendations);
    if (recommendationsList.dataset.lastRecData !== recDataStr) {
        recommendationsList.dataset.lastRecData = recDataStr;
        recommendationsList.innerHTML = recHtml || (data.is_running ? '' : '<p style="color: #888; font-size: 0.9rem;">分析ヒントがここに表示されます。</p>');
    }

    updateResultsList(data);
}

// Initial load
document.addEventListener('DOMContentLoaded', async () => {
    fetchBrands();
    await loadSiteConfigs();
    updateCategories();
    
    const resetBtn = document.getElementById('resetBtn');
    if (resetBtn) {
        resetBtn.addEventListener('click', clearResults);
    }
});
