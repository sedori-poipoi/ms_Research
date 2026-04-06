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
        fetchBrands(); // Refresh brands when going to settings
    }
}

// --- Brand Management ---
async function fetchBrands() {
    const res = await fetch('/brands');
    const brands = await res.json();
    const list = document.getElementById('brandList');
    
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

// --- Start Research ---
async function startResearch() {
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
                            item.jan.includes(searchTerm);
        const matchesProfit = item.profit >= minProfit;
        return matchesSearch && matchesProfit;
    });

    // Check if anything actually changed in the filtered list to avoid re-rendering
    const filteredIds = filtered.map(item => item.id).join(',');
    if (resultsList.dataset.lastIds === filteredIds && !data.is_running) return;
    resultsList.dataset.lastIds = filteredIds;

    if (filtered.length === 0) {
        resultsList.innerHTML = `
            <tr>
                <td colspan="5" style="text-align: center; color: #aaa; padding: 4rem;">
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
        
        // Correct formatting for + / - amounts
        const profitSign = item.profit > 0 ? '+' : '';
        const profitColor = item.profit > 0 ? '#2d5a27' : '#c62828';

        return `
            <tr class="result-row">
                <td style="max-width: 250px;">
                    <div style="font-weight: 600; font-size: 0.9rem;">${item.title}</div>
                    <div style="font-size: 0.75rem; color: #777; margin-top: 0.3rem;">${item.brand} | JAN: ${item.jan}</div>
                </td>
                <td>
                    <div style="font-size: 0.85rem; color: #555;">MS: <b>¥${item.price.toLocaleString()}</b></div>
                    <div style="font-size: 0.85rem; color: #555;">AMZ: <b>¥${item.amazon_price.toLocaleString()}</b></div>
                </td>
                <td>
                    <div class="profit-cell">
                        <div style="color: ${profitColor}; font-weight: 700; font-size: 1.1rem;">${profitSign}¥${item.profit.toLocaleString()}</div>
                        <div style="font-size: 0.75rem; color: #666; display: flex; align-items: center; gap: 0.4rem;">
                            ROI: ${item.roi} ${profitBadge}
                        </div>
                    </div>
                </td>
                <td><span class="badge ${badgeClass}">${item.restriction}</span></td>
                <td>
                    <div class="link-group">
                        <a href="${item.amazon_url}" target="_blank" class="link-icon">AMZ</a>
                        <a href="${item.keepa_url}" target="_blank" class="link-icon">Keepa</a>
                        <a href="${item.ms_url}" target="_blank" class="link-icon">MS</a>
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
    lastData = data; // Save for filtering
    const startBtn = document.getElementById('startBtn');
    const progressBar = document.getElementById('progressBar');
    const statusText = document.getElementById('statusText');
    const logConsole = document.getElementById('logConsole');
    const recommendationsList = document.getElementById('recommendationsList');
    const resCount = document.getElementById('resCount');

    startBtn.disabled = data.is_running;
    if (data.is_running) {
        startBtn.innerText = 'リサーチ中...';
        document.getElementById('progressSection').style.display = 'block';
    } else {
        startBtn.innerText = 'リサーチ開始';
    }

    progressBar.style.width = data.progress + '%';
    statusText.innerText = data.current_status || '待機中';

    // Update Logs
    logConsole.innerHTML = data.logs.map(log => `<div class="log-entry">${log}</div>`).join('');

    // Update Recommendations
    const recHtml = data.recommendations.map(rec => `
        <div class="recommendation-card">
            <div style="font-weight: 600; font-size: 0.85rem; color: #2d5a27;">🏆 制限解除推奨</div>
            <div style="font-size: 0.9rem; margin-top: 0.3rem;">${rec.message}</div>
        </div>
    `).join('');
    
    if (recommendationsList.innerHTML !== recHtml) {
        recommendationsList.innerHTML = recHtml || (data.is_running ? '' : '<p style="color: #888; font-size: 0.9rem;">分析ヒントがここに表示されます。</p>');
    }


    // Results List & Count
    resCount.innerText = `${data.results.length}件`;
    updateResultsList(data); // Call filtered renderer
}


// Initial brand load
fetchBrands();
