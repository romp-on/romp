// The scripted LOGIN loop (T157): the gear's Billing login element end to end on the real stack,
// against the MOCKED login CLI (ROMP_CLAUDE_BIN — never a real account; the lab kernel spends no
// model turns in this phase). Asserts: click → the code=true URL renders as a link; the code input
// passes through (the mock proves arrival by hash); success surfaces; and the SECRECY pin — the
// fixture code appears nowhere in the kernel log.
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const require2 = createRequire(path.join(ROOT, "vscode-extension", "package.json"));
const { chromium } = require2("playwright");

const PORT = process.env.PORT, TOKEN = process.env.TOKEN, LAB = process.env.LAB_DIR;
const shots = path.join(LAB, "shots");
const fails = [];
let n = 0;
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "PASS" : "FAIL"} login:${name}${detail ? " — " + detail : ""}`);
  if (!ok) fails.push(name);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const b = await chromium.launch();
const page = await b.newPage({ viewport: { width: 900, height: 800 } });
page.on("pageerror", (e) => console.log("PAGEERR", String(e)));
const shot = async (name) => page.screenshot({ path: path.join(shots, `lg${String(++n).padStart(2, "0")}-${name}.png`) });

// the FEED page hosts the gear (the openSettings listener lives in gear.js, required by feed.ts;
// the palette routes openSettings to the feed pane) — the login block rides it
await page.goto(`http://127.0.0.1:${PORT}/feed?token=${TOKEN}`);
await page.waitForSelector("#feed-foot", { timeout: 20000, state: "attached" });   // an empty lab feed keeps it hidden
await page.evaluate(() => window.postMessage({ romp: "openSettings" }, "*"));
await page.waitForSelector("#rs-login-btn", { timeout: 15000, state: "attached" });
await page.waitForFunction(() => { const b = document.getElementById("rs-login-btn"); return b && b.offsetParent; }, { timeout: 15000 });
await shot("gear-open");
check("element-present", true);
await page.click("#rs-login-btn");
// the flow: starting → url (the mock walks the gates in under a second; the gear polls /version)
const url = await (async () => {
  const t0 = Date.now();
  while (Date.now() - t0 < 30000) {
    const u = await page.evaluate(() => {
      const d = document.getElementById("rs-login-url");
      const a = d && d.querySelector("a");
      return a ? a.href : "";
    });
    if (u) return u;
    await sleep(400);
  }
  return "";
})();
check("url-streams-as-link", url.startsWith("https://claude.com/cai/oauth/authorize?code=true"),
  url || "no link rendered");
await shot("url-shown");
// Clicking to paste must retain settings: closing it also drops the shell's full-window
// feed iframe, which makes the login dialog disappear under the pointer.
await page.click("#rs-login-input");
check("input-click-keeps-settings-open", await page.isVisible("#rsettings"));
check("input-keeps-focus", await page.evaluate(() => document.activeElement?.id === "rs-login-input"));
await page.fill("#rs-login-input", "LAB-SYNTH-CODE");
await page.click("#rs-login-send");
check("submit-keeps-settings-open", await page.isVisible("#rsettings"));
const done = await (async () => {
  const t0 = Date.now();
  while (Date.now() - t0 < 20000) {
    const s = await page.evaluate(() => {
      const acct = document.getElementById("rs-login-acct");
      const st = document.getElementById("rs-login-state");
      return { acct: acct ? acct.textContent : "", state: st ? st.textContent : "" };
    });
    if (!s.state && !(await page.evaluate(() => { const c = document.getElementById("rs-login-code"); return c && !c.hidden; }))) return s;
    if (/failed|error/i.test(s.state)) return s;
    await sleep(400);
  }
  return { acct: "", state: "timeout" };
})();
check("flow-completes", !/failed|error|timeout/i.test(done.state), JSON.stringify(done));
await shot("done");
// the mock proves the code ARRIVED (by hash), and the kernel log must never carry it
const marker = process.env.LOGIN_MOCK_MARKER || "";
check("code-passed-through", marker && fs.existsSync(marker), marker);
const klog = fs.readFileSync(path.join(LAB, "kernel.log"), "utf8");
check("secrecy-kernel-log", !klog.includes("LAB-SYNTH-CODE"),
  "the code must exist nowhere but the PTY write");
await b.close();
if (fails.length) { console.log("LOGIN LOOP FAILURES:", fails.join(", ")); process.exit(1); }
console.log("login loop: all green");
