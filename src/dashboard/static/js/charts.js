// Root path for reverse proxy support
const chartsRp = (typeof ROOT_PATH !== 'undefined') ? ROOT_PATH : '';

// Chart instances
let priceChart, volumeChart, spreadChart, basisChart;
let priceSeries, volumeSeries, spreadSeries, basisSeries;
let autoRefreshTimer = null;
let currentSymbol = '';

const PRICE_PRECISION = {
    'BTCUSDT': 1,
    'ETHUSDT': 2,
    'ADAUSDT': 4,
};

const chartOptions = {
    layout: {
        background: { color: '#161b22' },
        textColor: '#7d8590',
        fontSize: 12,
    },
    grid: {
        vertLines: { color: 'rgba(48,54,61,0.5)' },
        horzLines: { color: 'rgba(48,54,61,0.5)' },
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: '#30363d',
    },
    rightPriceScale: { borderColor: '#30363d' },
};

function destroyCharts() {
    if (priceChart) { priceChart.remove(); priceChart = null; priceSeries = null; }
    if (volumeChart) { volumeChart.remove(); volumeChart = null; volumeSeries = null; }
    if (spreadChart) { spreadChart.remove(); spreadChart = null; spreadSeries = null; }
    if (basisChart) { basisChart.remove(); basisChart = null; basisSeries = null; }
}

function initCharts(symbol) {
    const prec = PRICE_PRECISION[symbol] || 2;

    const priceEl = document.getElementById('price-chart');
    if (priceEl) {
        priceChart = LightweightCharts.createChart(priceEl, { ...chartOptions, height: 400 });
        priceSeries = priceChart.addCandlestickSeries({
            upColor: '#3fb950',
            downColor: '#f85149',
            borderUpColor: '#3fb950',
            borderDownColor: '#f85149',
            wickUpColor: '#3fb950',
            wickDownColor: '#f85149',
            priceFormat: { type: 'price', precision: prec, minMove: Math.pow(10, -prec) },
        });
    }

    const volEl = document.getElementById('volume-chart');
    if (volEl) {
        volumeChart = LightweightCharts.createChart(volEl, { ...chartOptions, height: 200 });
        volumeSeries = volumeChart.addHistogramSeries({
            color: '#58a6ff',
            priceFormat: { type: 'volume' },
        });
    }

    const spreadEl = document.getElementById('spread-chart');
    if (spreadEl) {
        spreadChart = LightweightCharts.createChart(spreadEl, { ...chartOptions, height: 250 });
        spreadSeries = spreadChart.addLineSeries({
            color: '#d29922',
            lineWidth: 1,
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
        });
    }

    const basisEl = document.getElementById('basis-chart');
    if (basisEl) {
        basisChart = LightweightCharts.createChart(basisEl, { ...chartOptions, height: 250 });
        basisSeries = basisChart.addLineSeries({
            color: '#db6d28',
            lineWidth: 1,
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
        });
    }
}

function toTimestamp(isoStr) {
    return Math.floor(new Date(isoStr).getTime() / 1000);
}

async function loadPriceChart(symbol, tf) {
    const resp = await fetch(`${chartsRp}/api/metrics/ohlcv?symbol=${symbol}&timeframe=${tf}`);
    const data = await resp.json();
    const candles = (data.candles || []).map(c => ({
        time: toTimestamp(c.time),
        open: Number(c.open),
        high: Number(c.high),
        low: Number(c.low),
        close: Number(c.close),
    }));
    if (priceSeries && candles.length) priceSeries.setData(candles);

    const volumes = (data.candles || []).map(c => ({
        time: toTimestamp(c.time),
        value: Number(c.volume),
        color: Number(c.close) >= Number(c.open) ? 'rgba(63,185,80,0.5)' : 'rgba(248,81,73,0.5)',
    }));
    if (volumeSeries && volumes.length) volumeSeries.setData(volumes);
}

async function loadSpreadChart(symbol, tf) {
    const resp = await fetch(`${chartsRp}/api/metrics/spread?symbol=${symbol}&timeframe=${tf}`);
    const data = await resp.json();
    const points = (data.data || []).map(d => ({
        time: toTimestamp(d.time),
        value: Number(d.avg_spread_bps),
    }));
    if (spreadSeries && points.length) spreadSeries.setData(points);
}

async function loadBasisChart(symbol, tf) {
    const resp = await fetch(`${chartsRp}/api/metrics/basis?symbol=${symbol}&timeframe=${tf}`);
    const data = await resp.json();
    const points = (data.data || []).map(d => ({
        time: toTimestamp(d.time),
        value: Number(d.avg_basis_bps),
    }));
    if (basisSeries && points.length) basisSeries.setData(points);
}

async function reloadCharts() {
    const symbol = document.getElementById('chart-symbol').value;
    const tf = document.getElementById('chart-tf').value;

    if (symbol !== currentSymbol) {
        destroyCharts();
        currentSymbol = symbol;
    }

    if (!priceChart) {
        initCharts(symbol);
    }

    await Promise.all([
        loadPriceChart(symbol, tf),
        loadSpreadChart(symbol, tf),
        loadBasisChart(symbol, tf),
    ]);
}

function toggleAutoRefresh() {
    const checked = document.getElementById('auto-refresh').checked;
    if (checked) {
        autoRefreshTimer = setInterval(reloadCharts, 30000);
    } else {
        if (autoRefreshTimer) clearInterval(autoRefreshTimer);
        autoRefreshTimer = null;
    }
}

document.addEventListener('DOMContentLoaded', reloadCharts);
