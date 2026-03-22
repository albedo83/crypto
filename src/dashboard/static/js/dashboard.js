// Root path for reverse proxy support
const rp = (typeof ROOT_PATH !== 'undefined') ? ROOT_PATH : '';

// Clock
function updateClock() {
    const el = document.getElementById('clock');
    if (el) el.textContent = new Date().toLocaleTimeString();
}
setInterval(updateClock, 1000);
updateClock();

// WebSocket status
let ws = null;
let wsReconnectDelay = 1000;

function connectWs() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}${rp}/ws/live`);

    ws.onopen = () => {
        const el = document.getElementById('ws-status');
        if (el) { el.textContent = 'WS: Live'; el.className = 'ws-indicator connected'; }
        wsReconnectDelay = 1000;
    };

    ws.onmessage = (evt) => {
        try {
            const data = JSON.parse(evt.data);
            if (data.type === 'status' && window.onWsStatus) {
                window.onWsStatus(data);
            }
        } catch(e) {}
    };

    ws.onclose = () => {
        const el = document.getElementById('ws-status');
        if (el) { el.textContent = 'WS: --'; el.className = 'ws-indicator disconnected'; }
        setTimeout(connectWs, wsReconnectDelay);
        wsReconnectDelay = Math.min(wsReconnectDelay * 2, 30000);
    };

    ws.onerror = () => { ws.close(); };
}

connectWs();
