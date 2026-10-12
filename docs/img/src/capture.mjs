// Captura quadros de uma página via DevTools (Chrome headless) em intervalos regulares.
// uso: node capture.mjs <url> <segundos> <pasta> <largura> <altura> [fps] [js-a-rodar-depois-de-carregar]
import fs from 'node:fs';
const [url, secs, out, W, H, fpsArg, extraJs] = process.argv.slice(2);
const fps = Number(fpsArg || 10);
const port = 9333;
fs.mkdirSync(out, { recursive: true });
const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0; const pending = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: Number(W), height: Number(H), deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url });
await new Promise((r) => setTimeout(r, 1500));
if (extraJs) await send('Runtime.evaluate', { expression: extraJs });
const t0 = Date.now(); const times = []; let n = 0;
const interval = 1000 / fps;
while ((Date.now() - t0) / 1000 < Number(secs)) {
  const tick = Date.now();
  const r = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(`${out}/f_${String(n).padStart(5, '0')}.png`, Buffer.from(r.result.data, 'base64'));
  times.push((Date.now() - t0) / 1000); n++;
  const wait = interval - (Date.now() - tick);
  if (wait > 0) await new Promise((r2) => setTimeout(r2, wait));
}
fs.writeFileSync(`${out}/times.json`, JSON.stringify(times));
await fetch(`http://127.0.0.1:${port}/json/close/${target.id}`);
console.log(`frames: ${n}`);
process.exit(0);
