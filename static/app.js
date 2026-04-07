let processedIds = new Set();
let isSoundEnabled = true;

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
function updateCategories() {
    const targetSite = document.getElementById('targetSite').value;
    const catSelect = document.getElementById('category');
    const sortSelect = document.getElementById('sortOrder');
    const customUrlInput = document.getElementById('customUrl');

    if (targetSite === 'yodobashi') {
        catSelect.innerHTML = `
            <option value="pet">🐾 ペット用品・フード</option>
            <option value="appliances">🏠 生活家電</option>
            <option value="pc">💻 パソコン・周辺機器</option>
            <option value="camera">📷 カメラ・写真</option>
            <option value="audio">🎧 オーディオ</option>
            <option value="kitchen">🍳 キッチン用品・食器</option>
            <option value="health">💊 ヘルス＆ビューティー</option>
            <option value="toys">🧸 おもちゃ・ホビー</option>
            <option value="food">🍙 食品・飲料・お酒</option>
        `;
        sortSelect.innerHTML = `
            <option value="new_arrival">新着順</option>
            <option value="price_asc">価格が安い順</option>
            <option value="price_desc">価格が高い順</option>
            <option value="score">人気順</option>
        `;
        customUrlInput.placeholder = 'https://www.yodobashi.com/category/...';
    } else if (targetSite === 'netsea') {
        catSelect.innerHTML = `
            <option value="beauty">美容・コスメ</option>
            <option value="health">健康・サプリ</option>
            <option value="food">食品・飲料</option>
            <option value="daily">日用品</option>
        `;
        sortSelect.innerHTML = `
            <option value="new_arrival">新着順</option>
            <option value="price_asc">価格が安い順</option>
            <option value="price_desc">価格が高い順</option>
        `;
        customUrlInput.placeholder = 'https://www.netsea.jp/search/...';
    } else {
        catSelect.innerHTML = `
            <option value="makeup">メイク（通常）</option>
            <option value="skincare">スキンケア（通常）</option>
            <option value="sale">セール品のみ抽出 🔥</option>
            <option value="all">全カテゴリ（全頭）</option>
        `;
        sortSelect.innerHTML = `
            <option value="disp_from_datetime">新着順</option>
            <option value="selling_price0_min">価格が安い順</option>
            <option value="selling_price0_max">価格が高い順</option>
            <option value="review">クチコミが多い順</option>
        `;
        customUrlInput.placeholder = 'https://www.make-up-solution.com/...';
    }
}

// --- Start Research ---
async function startResearch() {
    const targetSite = document.getElementById('targetSite').value;
    const category = document.getElementById('category').value;
    const customUrl = document.getElementById('customUrl').value;
    const maxItems = document.getElementById('maxItems').value;
    const startPage = document.getElementById('startPage').value;
    const endPage = document.getElementById('endPage').value;
    const sortOrder = document.getElementById('sortOrder').value;
    const focusMode = document.getElementById('focusMode').checked;
    const skipHistory = document.getElementById('skipHistory').checked;
    const startBtn = document.getElementById('startBtn');

    startBtn.disabled = true;
    document.getElementById('progressSection').style.display = 'block';
    document.getElementById('resultsList').innerHTML = '';
    processedIds.clear();

    const response = await fetch('/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
            target_site: targetSite,
            category, 
            custom_url: customUrl,
            max_items: parseInt(maxItems), 
            start_page: parseInt(startPage),
            end_page: parseInt(endPage),
            sort_order: sortOrder,
            focus_mode: focusMode,
            skip_history: skipHistory
        })
    });

    const result = await response.json();
    if (result.status === 'error') {
        alert(result.message);
        startBtn.disabled = false;
    }
}

// --- Stop Research ---
async function stopResearch() {
    await fetch('/stop', { method: 'POST' });
}

// --- Filtering ---
let lastData = null;
function filterResults() {
    if (!lastData) return;
    updateResultsList(lastData);
}

function updateResultsList(data) {
    const resultsList = document.getElementById('resultsList');
    const searchTerm = document.getElementById('tableSearch').value.toLowerCase();
    const minProfit = parseInt(document.getElementById('profitFilter').value);

    const filtered = data.results.filter(item => {
        const matchesSearch = item.title.toLowerCase().includes(searchTerm) || 
                            item.brand.toLowerCase().includes(searchTerm) ||
                            (item.jan && item.jan.includes(searchTerm));
        const matchesProfit = item.profit >= minProfit;
        return matchesSearch && matchesProfit;
    });

    const filteredIds = filtered.map(item => item.id).join(',');
    if (resultsList.dataset.lastIds === filteredIds && !data.is_running) return;
    resultsList.dataset.lastIds = filteredIds;

    if (filtered.length === 0) {
        resultsList.innerHTML = `
            <tr>
                <td colspan="7" style="text-align: center; color: #aaa; padding: 4rem;">
                    利益商品が見つかるまでお待ちください...
                </td>
            </tr>
        `;
        return;
    }

    resultsList.innerHTML = filtered.slice().reverse().map(item => {
        if (item.judgment.includes('利益商品') && !processedIds.has(item.id)) {
            processedIds.add(item.id);
            playNotificationSound();
        }

        const badgeClass = item.restriction.includes('出品可') ? 'badge-ok' : 'badge-ng';
        const profitBadge = item.judgment.includes('利益商品') ? '<span class="badge badge-profit">利益品</span>' : '';

        const profitSign = item.profit > 0 ? '+' : '';
        const profitColor = item.profit > 0 ? '#2d5a27' : '#c62828';
        
        // Highlight good ranks (if numerical rank < 50k - simplified estimate)
        let rankClass = 'badge-dim';
        const rankMatch = (item.rank || '').match(/\d+/);
        if (rankMatch && parseInt(rankMatch[0]) < 50000) {
            rankClass = 'badge-rank';
        }

        // Defensive check for ms_url to prevent crash
        const safeMsUrl = item.ms_url || '';
        const sourceLabel = safeMsUrl.includes('yodobashi') ? 'ヨドバシ' : '仕入先';
        const safeAmzUrl = item.amazon_url || `https://www.amazon.co.jp/dp/${item.asin}`;
        const safeKeepaUrl = item.keepa_url || `https://keepa.com/#!product/5-${item.asin}`;

        return `
            <tr class="result-row">
                <td style="max-width: 250px;">
                    <div style="font-weight: 600; font-size: 0.9rem;">${item.title}</div>
                    <div style="font-size: 0.75rem; color: #777; margin-top: 0.3rem;">${item.brand} | ${item.jan && item.jan !== '—' ? 'JAN: ' + item.jan : 'キーワード照合'}</div>
                </td>
                <td>
                    <div style="font-size: 0.85rem; color: #555;">${sourceLabel}: <b>¥${(item.price || 0).toLocaleString()}</b></div>
                    <div style="font-size: 0.85rem; color: #555;">AMZ: <b>¥${(item.amazon_price || 0).toLocaleString()}</b></div>
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
                    <div style="font-weight: 700; font-size: 1.1rem; color: #444;">${item.sellers || '—'}<span style="font-size: 0.7rem; font-weight: normal; margin-left: 2px;">人</span></div>
                </td>
                <td><span class="badge ${badgeClass}">${item.restriction || '確認中'}</span></td>
                <td>
                    <div class="link-group">
                        <a href="${safeAmzUrl}" target="_blank" class="link-icon">AMZ</a>
                        <a href="${safeKeepaUrl}" target="_blank" class="link-icon">Keepa</a>
                        <a href="${safeMsUrl}" target="_blank" class="link-icon">仕入先</a>
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
    lastData = data;
    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const progressBar = document.getElementById('progressBar');
    const statusText = document.getElementById('statusText');
    const logConsole = document.getElementById('logConsole');
    const recommendationsList = document.getElementById('recommendationsList');
    const resCount = document.getElementById('resCount');

    startBtn.disabled = data.is_running;
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
    
    if (recommendationsList.innerHTML !== recHtml) {
        recommendationsList.innerHTML = recHtml || (data.is_running ? '' : '<p style="color: #888; font-size: 0.9rem;">分析ヒントがここに表示されます。</p>');
    }

    resCount.innerText = `${data.results.length}件`;
    updateResultsList(data);
}

// Initial brand load
fetchBrands();
