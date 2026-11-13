/* Same-origin five-year OHLCV, shared by stock details and ranking chart popups. */
window.StockHistory = (() => {
  const cache = new Map();
  async function load(symbol) {
    const sym = String(symbol).trim().toUpperCase();
    if (!/^[A-Z0-9][A-Z0-9.-]*$/.test(sym)) throw new Error("股票代码无效");
    if (cache.has(sym)) return cache.get(sym);
    const pending = (async () => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 20000);
      try {
        const response = await fetch(`./data/daily/${encodeURIComponent(sym)}.json`, {
          cache: "no-cache", signal: controller.signal,
        });
        if (response.status === 404) throw new Error("该股的日线缓存暂不可用，请稍后重试");
        if (!response.ok) throw new Error(`日线数据加载失败（HTTP ${response.status}）`);
        const payload = await response.json();
        if (payload.symbol !== sym || JSON.stringify(payload.fields) !== JSON.stringify(["t", "o", "h", "l", "c", "v", "a"]) || !Array.isArray(payload.data)) {
          throw new Error("日线数据格式无效");
        }
        const rows = payload.data.map((row) => Object.fromEntries(payload.fields.map((field, i) => [field, row[i]])));
        if (!rows.length || rows.some((r) => !/^\d{4}-\d{2}-\d{2}$/.test(r.t) ||
          ![r.o, r.h, r.l, r.c, r.v, r.a].every(Number.isFinite) ||
          Math.min(r.o, r.h, r.l, r.c, r.a) <= 0 || r.v < 0 ||
          r.h < Math.max(r.o, r.l, r.c) || r.l > Math.min(r.o, r.c)) ||
          rows.some((r, i) => i > 0 && r.t <= rows[i - 1].t)) {
          throw new Error("日线数据为空或无效");
        }
        return rows;
      } catch (error) {
        if (error.name === "AbortError") throw new Error("日线数据加载超时，请稍后重试");
        if (error instanceof TypeError) throw new Error("无法连接日线数据，请检查网络后重试");
        throw error;
      } finally {
        clearTimeout(timer);
      }
    })();
    cache.set(sym, pending);
    try { return await pending; }
    catch (error) { cache.delete(sym); throw error; }
  }
  return { load };
})();
