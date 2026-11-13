import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';

const source = await fs.readFile(new URL('../daily-history.js', import.meta.url), 'utf8');
const fields = ['t', 'o', 'h', 'l', 'c', 'v', 'a'];
function context(fetch) {
  const sandbox = { window: {}, fetch, AbortController, setTimeout, clearTimeout };
  vm.runInNewContext(source, sandbox);
  return sandbox.window.StockHistory;
}
function response(symbol = 'HTFL') {
  return { ok: true, status: 200, json: async () => ({ symbol, fields, data: [['2026-11-12', 10, 12, 9, 11, 1000, 5.5]] }) };
}

let calls = [];
let loader = context(async (url) => { calls.push(url); return response(); });
let [a, b] = await Promise.all([loader.load('htfl'), loader.load('HTFL')]);
assert.deepEqual(calls, ['./data/daily/HTFL.json']);
assert.equal(a, b);
assert.equal(a[0].a, 5.5);
assert.equal(a[0].v, 1000);

calls = [];
loader = context(async (url) => { calls.push(url); return response('BRK.B'); });
await loader.load('BRK.B');
assert.deepEqual(calls, ['./data/daily/BRK.B.json']);
await assert.rejects(loader.load('../secret'));

let attempt = 0;
loader = context(async () => ++attempt === 1 ? { ok: false, status: 404 } : response());
await assert.rejects(loader.load('HTFL'), /缓存暂不可用/);
await loader.load('HTFL');
assert.equal(attempt, 2, 'A failed load must be retryable');

loader = context(async () => ({ ok: false, status: 503 }));
await assert.rejects(loader.load('HTFL'), /HTTP 503/);
loader = context(async () => { const e = new Error('timeout'); e.name = 'AbortError'; throw e; });
await assert.rejects(loader.load('HTFL'), /超时/);
loader = context(async () => ({ ok: true, status: 200, json: async () => ({ symbol: 'HTFL', fields, data: [] }) }));
await assert.rejects(loader.load('HTFL'), /为空或无效/);

// Compile both inline entry points, then exercise the real detail-page startup
// with fetched HTFL/NVDA histories, including the chip calculations and rendering.
const root = new URL('../', import.meta.url);
for (const name of ['stock.html', 'index.html']) {
  const html = await fs.readFile(new URL(name, root), 'utf8');
  for (const match of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
}
for (const symbol of ['HTFL', 'NVDA']) {
  const payload = JSON.parse(await fs.readFile(new URL(`data/daily/${symbol}.json`, root), 'utf8'));
  const html = await fs.readFile(new URL('stock.html', root), 'utf8');
  const source = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1];
  const elements = new Map();
  const el = (id) => {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', innerHTML: '', classList: { toggle() {} }, style: {},
      addEventListener() {}, setAttribute() {}, querySelectorAll() { return []; },
      clientWidth: 800, clientHeight: 440,
    });
    return elements.get(id);
  };
  const urls = [], candles = [], volumes = [];
  const sandbox = {
    URLSearchParams, AbortController, setTimeout, clearTimeout,
    console: { log: console.log, error() {} }, // Optional intraday data is intentionally unavailable here.
    LightweightCharts: { createChart: () => ({
      addCandlestickSeries: () => ({ setData: (rows) => candles.push(...rows) }),
      addHistogramSeries: () => ({ setData: (rows) => volumes.push(...rows) }),
      priceScale: () => ({ applyOptions() {} }), timeScale: () => ({ fitContent() {} }),
    }) },
    location: { search: `?s=${symbol}` },
    document: { getElementById: el },
    window: { addEventListener() {} },
    fetch: async (url) => {
      urls.push(url);
      if (url === `./data/daily/${symbol}.json`) return { ok: true, status: 200, json: async () => payload };
      if (url === './data/manifest.json') return { json: async () => ({ dates: ['2026-11-13'] }) };
      if (url === './data/2026-11-13.json') return { json: async () => [{ s: symbol, n: symbol, float: 1e8 }] };
      if (url === './data/intraday.json') return { json: async () => ({ stocks: {} }) };
      return { ok: false, status: 404 };
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(await fs.readFile(new URL('daily-history.js', root), 'utf8'), sandbox);
  sandbox.StockHistory = sandbox.window.StockHistory;
  await vm.runInContext(source, sandbox);
  assert.match(el('status').textContent, /统计窗口/);
  assert.doesNotMatch(el('status').textContent, /加载失败/);
  assert.match(el('m-profit').textContent, /\d.*%/);
  assert.match(el('chip-svg').innerHTML, /<rect/);
  assert.equal(candles.length, Math.min(payload.data.length, 252));
  assert.equal(volumes.length, candles.length);
  assert.ok(urls.every((url) => url.startsWith('./data/')));
  console.log(`${symbol}: ${payload.data.length} daily bars, chip metrics rendered, all data requests same-origin`);
}

// Exercise ranking popup range selection with the real five-year NVDA cache.
const index = await fs.readFile(new URL('index.html', root), 'utf8');
const fn = index.match(/    async function fetchOHLC\(sym, range\) \{[\s\S]*?\n    \}/)[0];
const nvda = JSON.parse(await fs.readFile(new URL('data/daily/NVDA.json', root), 'utf8'));
const rangeContext = { StockHistory: context(async () => ({ ok: true, status: 200, json: async () => nvda })) };
vm.createContext(rangeContext);
vm.runInContext('const ohlcCache = new Map();\n' + fn, rangeContext);
const three = await vm.runInContext('fetchOHLC("NVDA", "3M")', rangeContext);
const five = await vm.runInContext('fetchOHLC("NVDA", "5Y")', rangeContext);
assert.ok(three.length >= 50 && three.length <= 70);
assert.ok(five.length > 1000);
assert.equal(five.at(-1).volume, nvda.data.at(-1)[5]);
console.log('Daily-history JavaScript checks passed');
