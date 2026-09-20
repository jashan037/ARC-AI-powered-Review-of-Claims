#!/usr/bin/env node
// Screenshots of the 8 demo steps in docs/DEMO.md, taken from the OFFICER console (/officer) with headless Chrome. No dependencies (Node 22+ has WebSocket and fetch).
//
//   scripts/run_demo.sh                                        # in one terminal: the app on port 8765, real agent only
//   node scripts/take_screenshots.mjs                          # in another: writes docs/screenshots/step1-....png ... step8-....png
//   node scripts/take_screenshots.mjs --base http://127.0.0.1:8765 --out docs/screenshots
//
// The questions come from the table in docs/DEMO.md (terminal A = no claim, B = TC07, C = TC02), so this cannot drift from the demo script.
// It refuses to run against the offline stand-in (GET /health must say live) unless you pass --allow-offline (for testing the script itself).
// Each screenshot shows the header (with the Live agent badge), the claim panel, and the question with its answer as an officer sees it.
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
const opt = (name, dflt) => (args.includes(name) ? args[args.indexOf(name) + 1] : dflt);
const BASE = opt("--base", "http://127.0.0.1:8765").replace(/\/$/, "");
const OUT = path.resolve(ROOT, opt("--out", "docs/screenshots"));
const CHROME = opt("--chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
const PORT = Number(opt("--debug-port", "9333"));
const WIDTH = 1280;
const ANSWER_TIMEOUT_MS = 100000;
const CLAIM_FOR_TERMINAL = { A: "", B: "TC07", C: "TC02" };
const SLUGS = ["knee-replacement", "settlement-ratio", "waiting-period-dates", "assess-tc07", "why-room-rent", "missing-documents", "what-if-room-rent", "not-payable-tc02"];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function demoSteps() {
  const md = readFileSync(path.join(ROOT, "docs", "DEMO.md"), "utf8");
  const steps = [...md.matchAll(/^\| ([1-8]) \| ([A-C]) \| `([^`]+)` \|/gm)].map((m) => ({ n: Number(m[1]), claim: CLAIM_FOR_TERMINAL[m[2]], q: m[3] }));
  if (steps.length !== 8) throw new Error(`expected 8 steps in docs/DEMO.md, found ${steps.length}`);
  return steps;
}

class Page {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    ws.addEventListener("message", (e) => {
      const msg = JSON.parse(e.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async eval(expression) {
    const r = await this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  }
  async until(expression, what, timeoutMs = 30000) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
      if (await this.eval(expression)) return;
      await sleep(300);
    }
    throw new Error(`timed out waiting for ${what}`);
  }
  async shot(file, { hideOlder = true } = {}) {
    if (hideOlder) {   // only the newest question and answer, so each picture is about one step (element.style is allowed by the page's CSP; an injected <style> is not)
      await this.eval(`[...document.getElementById('transcript').children].slice(0, -2).forEach((c) => { c.dataset.shotHid = '1'; c.style.display = 'none'; })`);
    }
    await this.eval("window.scrollTo(0, 0)");
    // The page stretches to fill the viewport (footer at the bottom), so scrollHeight only ever grows. Add up the real content instead.
    const h = Math.min(3400, Math.max(640, await this.eval(`(() => { const q = (s) => document.querySelector(s); const vis = (e) => (e && !e.hidden ? e.offsetHeight : 0);
      return Math.ceil(vis(q('.site-header')) + vis(q('#offline-banner')) + 40 + Math.max(q('.side').offsetHeight, q('.chat').offsetHeight) + vis(q('.site-footer')) + 4); })()`)));
    await this.send("Emulation.setDeviceMetricsOverride", { width: WIDTH, height: h, deviceScaleFactor: 1, mobile: false });
    await sleep(250);
    const { data } = await this.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    writeFileSync(file, Buffer.from(data, "base64"));
    await this.eval("document.querySelectorAll('[data-shot-hid]').forEach((c) => { c.style.display = ''; delete c.dataset.shotHid; })");
  }
}

async function launchChrome(userDir) {
  const proc = spawn(CHROME, [`--remote-debugging-port=${PORT}`, `--user-data-dir=${userDir}`, "--headless=new", "--hide-scrollbars", "--no-first-run",
    "--no-default-browser-check", "--disable-gpu", `--window-size=${WIDTH},900`, "about:blank"], { stdio: "ignore" });
  for (let i = 0; i < 60; i++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = targets.find((t) => t.type === "page");
      if (page) return { proc, wsUrl: page.webSocketDebuggerUrl };
    } catch { /* not up yet */ }
    await sleep(250);
  }
  proc.kill();
  throw new Error("Chrome did not start. Is it installed at " + CHROME + " ? (use --chrome <path>)");
}

async function main() {
  const health = await fetch(`${BASE}/health`).then((r) => r.json()).catch(() => null);
  if (!health) throw new Error(`Cannot reach ${BASE}. Start it first with scripts/run_demo.sh`);
  if (!health.live && !args.includes("--allow-offline")) {
    throw new Error("The server is the OFFLINE stand-in, not the real agent. Start it with scripts/run_demo.sh (or pass --allow-offline to test this script).");
  }
  const steps = demoSteps();
  mkdirSync(OUT, { recursive: true });
  const userDir = mkdtempSync(path.join(tmpdir(), "arc-shots-"));
  const { proc, wsUrl } = await launchChrome(userDir);
  const ws = new WebSocket(wsUrl);
  await new Promise((resolve, reject) => { ws.addEventListener("open", resolve); ws.addEventListener("error", reject); });
  const page = new Page(ws);
  const written = [];
  try {
    await page.send("Page.enable");
    await page.send("Emulation.setDeviceMetricsOverride", { width: WIDTH, height: 900, deviceScaleFactor: 1, mobile: false });
    await page.send("Page.navigate", { url: BASE + "/officer" });
    await page.until("!!document.getElementById('send') && !document.getElementById('send').disabled", "the page to be ready");
    let claim = "";
    for (const step of steps) {
      if (step.claim !== claim) {   // pick the claim in the dropdown, exactly as an officer would
        await page.eval(`(() => { const s = document.getElementById('claim-select'); s.value = ${JSON.stringify(step.claim)}; s.dispatchEvent(new Event('change')); })()`);
        await page.until("!document.getElementById('send').disabled && /(is loaded|Policy questions only)/.test(document.getElementById('status').textContent)", "the claim to load");
        claim = step.claim;
      }
      const before = await page.eval("document.querySelectorAll('.answer').length");
      const clicked = await page.eval(`(() => { const b = [...document.querySelectorAll('.q-btn')].find(x => x.title === ${JSON.stringify(step.q)}); if (b) b.click(); return !!b; })()`);
      if (!clicked) throw new Error(`step ${step.n}: no quick-question button for: ${step.q}`);
      await page.until(`document.querySelectorAll('.answer').length > ${before} && !document.querySelector('.loading')`, `the answer to step ${step.n}`, ANSWER_TIMEOUT_MS);
      const status = await page.eval("(() => { const a = [...document.querySelectorAll('.answer')].pop(); return a.classList.contains('warn') || a.classList.contains('error') ? 'NOT OK' : 'ok'; })()");
      if (status !== "ok") throw new Error(`step ${step.n} did not produce a normal answer (timeout or error card); not saving a misleading screenshot`);
      await sleep(400);
      const file = path.join(OUT, `step${step.n}-${SLUGS[step.n - 1]}.png`);
      await page.shot(file);
      written.push(file);
      console.log(`step ${step.n}: ${path.relative(ROOT, file)}   (${step.q.slice(0, 60)})`);
      if (step.n === 4) {   // the same answer with every detail section open and one citation chip expanded
        await page.eval(`(() => { const a = [...document.querySelectorAll('.answer')].pop(); a.querySelector('.link-toggle')?.click(); a.querySelectorAll('.chip')[2]?.click(); })()`);
        await sleep(300);
        const f2 = path.join(OUT, "step4b-details-expanded.png");
        await page.shot(f2);
        written.push(f2);
        console.log(`step 4 (expanded): ${path.relative(ROOT, f2)}`);
        await page.eval(`(() => { const a = [...document.querySelectorAll('.answer')].pop(); a.querySelector('.link-toggle')?.click(); a.querySelectorAll('.chip')[2]?.click(); })()`);
      }
    }
  } finally {
    ws.close();
    proc.kill();
    await sleep(600);   // let Chrome finish writing to its profile folder before it is removed
    try { rmSync(userDir, { recursive: true, force: true }); } catch { /* a temp folder; the OS will clean it */ }
  }
  console.log(`\nSaved ${written.length} screenshots in ${path.relative(ROOT, OUT)}${health.live ? "" : "  (OFFLINE stand-in: test only, do not publish)"}`);
}

main().catch((e) => { console.error("\nERROR:", e.message); process.exit(1); });
