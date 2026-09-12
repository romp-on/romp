// Several Claude logins (T346, the user 2026-09-11): every Billing surface lists every login the host knows plus the
// API key, names WHICH login a session bills, and posts the pick as "login" | "key" | "login:<id>". render.ts has no
// jsdom harness, so these are source pins (the auth-selector.test.ts idiom); the kernel's halves are pinned in
// tests/test_login_records.py. The picker's rebuild is EXECUTED here on a small DOM stand-in.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const requireCjs = createRequire(__filename);

const ROOT = path.resolve(process.cwd(), "..");
const RENDER = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
const STYLES = fs.readFileSync(path.join(ROOT, "ui", "webview", "styles.css"), "utf8");
const GEAR = fs.readFileSync(path.join(ROOT, "ui", "webview", "gear.js"), "utf8");
const GEARCSS = fs.readFileSync(path.join(ROOT, "ui", "webview", "gear.css"), "utf8");
const FEED = fs.readFileSync(path.join(ROOT, "ui", "webview", "feed.ts"), "utf8");

test("the status and the availability reply carry WHICH login, typed", () => {
  assert.match(RENDER, /interface AuthLogin \{ id\?: string; value\?: string; label\?: string; machine\?: boolean; available\?: boolean; why\?: string; expiresSoon\?: boolean \}/);
  assert.match(RENDER, /interface AuthAvail \{ login\?: boolean; key\?: boolean; loginWhy\?: string; keyWhy\?: string; acct\?: string; default\?: string; defaultExplicit\?: boolean; logins\?: AuthLogin\[\] \}/);
  assert.match(RENDER, /authAcct\?: string; authLogin\?: string; authLabel\?: string; authLoginLive\?: string \| null; ctx\?: string;/);
});

test("a stored login the CLI did not use is said on the hover and the sub-line, from the init's evidence", () => {
  // authLoginLive "" = the CLI signed in with the machine's own login instead of the stored one's helper (the
  // kernel's _note_auth_source reads the init's source word); absent before an init, the record id when it answered
  assert.match(RENDER, /: \(s\.status\.auth === "login" && s\.status\.authLogin && s\.status\.authLoginLive === ""\)\s*\n\s*\? `⚠ Login \(\$\{loginName\(s\.status\)\}\) picked, but the CLI signed in with another credential: this session bills that`/);
  assert.match(RENDER, /: \(st\.auth === "login" && st\.authLogin && st\.authLoginLive === ""\)\s*\n\s*\? "⚠ CLI used another credential"/);
});

test("the tab menu's Billing submenu lists every login plus the key, the current one by WHICH login", () => {
  // the kernel's list when it sends one; the two-entry list for an older kernel, its literals intact
  assert.match(RENDER, /const choices = avail\.logins && avail\.logins\.length \? authLoginChoices\(avail\)\s*\n\s*: \[\{ label: st\.authAcct \? `Login \(\$\{st\.authAcct\}\)` : "Login", value: "login", why: avail\.login \? "" : \(avail\.loginWhy \|\| "no Claude login signed in on this machine"\) \},/);
  assert.match(RENDER, /for \(const c of choices\) \{\s*\n\s*const cur = authChoiceCurrent\(st, c\.value\);/);
  // every login the kernel knows, `Login (<label>)`, greyed with its reason; then the key
  assert.match(RENDER, /label: l\.label \? `Login \(\$\{l\.label\}\)` : "Login",\s*\n\s*value: l\.value \|\| \(l\.id \? `login:\$\{l\.id\}` : "login"\),/);
  assert.match(RENDER, /out\.push\(\{ label: "API key", value: "key", why: avail\.key \? "" : \(avail\.keyWhy \|\| "no apiKeyHelper configured"\) \}\);/);
  // the current mark: the key, or a login by st.authLogin ("" = the machine's own)
  assert.match(RENDER, /return st\.auth === "login" && \("login" \+ \(st\.authLogin \? `:\$\{st\.authLogin\}` : ""\)\) === value;/);
  // the sub-line and the hover name the login by the kernel's label, the account name as the fallback
  assert.match(RENDER, /: \(st\.auth === "key" \? "API key" : \(loginName\(st\) \? `Login \(\$\{loginName\(st\)\}\)` : "Login"\)\);/);
  assert.match(RENDER, /: \(loginName\(s\.status\) \? `Login \(\$\{loginName\(s\.status\)\}\)` : "Login"\)\]\);/);
});

test("the picker's Billing row keeps the two-option path and adds the many-login rebuild", () => {
  // the pinned two-option gate is untouched; a stored login can show the row when the machine has neither side
  assert.match(RENDER, /const show = !pickMode && !!\(a && \(a\.login \|\| a\.key\)\) && pickerBackendChoice\(\) === "sdk";/);
  assert.match(RENDER, /const stored = \(a && a\.logins \? a\.logins : \[\]\)\.filter\(\(l\) => !l\.machine\);/);
  assert.match(RENDER, /if \(stored\.length\) \{ syncPickerAuthMany\(wrap, a!\); return; \}/);
  // up to three choices: buttons (shrinking, an unavailable one disabled with its reason); beyond: one dropdown,
  // rebuilt only when the choice set changes; the pick kept by value; one usable choice written out
  assert.match(RENDER, /if \(choices\.length <= 3\) \{/);
  assert.match(RENDER, /b\.dataset\.auth = c\.value; b\.dataset\.many = "1";/);
  assert.match(RENDER, /if \(c\.why\) b\.disabled = true;/);
  assert.match(RENDER, /const sig = choices\.map\(\(c\) => c\.value \+ "\|" \+ c\.label \+ "\|" \+ c\.why\)\.join\("\\n"\);/);
  assert.match(RENDER, /if \(!dd \|\| dd\.dataset\.sig !== sig\) \{/);
  assert.match(RENDER, /if \(usable\.length <= 1\) \{/);
  // the create payload reads the dropdown when there is one
  assert.match(RENDER, /const dd = wrap\.querySelector\("select\.picker-auth-select"\) as HTMLSelectElement \| null;\s*\/\/ several logins \(T346\)\s*\n\s*if \(dd\) return dd\.value \|\| "";/);
  // styles: the buttons shrink with an ellipsis; the dropdown wears the menu tokens (dark literals as fallbacks only)
  assert.match(STYLES, /\.picker-auth \.picker-be-opt \{ flex: 0 1 auto; min-width: 0; max-width: 42%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; \}/);
  assert.match(STYLES, /\.picker-auth-select \{[^}]*background: var\(--menu-bg, #252526\); color: var\(--menu-fg, #cccccc\);/);
});

test("the picker rebuild runs: three choices are buttons, four are a dropdown, one usable choice is written out", () => {
  // lift authLoginChoices + syncPickerAuthMany + pickerAuthDropSelect + pickerAuthChoice, run them on a tiny DOM
  const lift = (name: string) => {
    const i = RENDER.indexOf(`function ${name}(`);
    assert.ok(i > 0, name);
    // the body's brace is the first one that opens a line (a return type's `{ label: ... }` sits inline)
    let depth = 0, j = RENDER.indexOf("{\n", i);
    for (; j < RENDER.length; j++) { if (RENDER[j] === "{") depth++; else if (RENDER[j] === "}") { depth--; if (!depth) break; } }
    return RENDER.slice(i, j + 1);
  };
  // a minimal element: classList, dataset, style, children, querySelector(All) over class + attribute selectors used here
  class El {
    tag: string; cls: Set<string>; dataset: Record<string, string> = {}; style: Record<string, string> = {}; children: El[] = [];
    textContent = ""; title = ""; type = ""; disabled = false; value = ""; parent: El | null = null;
    constructor(tag: string, cls = "") { this.tag = tag; this.cls = new Set(cls.split(" ").filter(Boolean)); }
    get classList() { const c = this.cls; return { toggle: (n: string, on: boolean) => { if (on) c.add(n); else c.delete(n); }, add: (n: string) => c.add(n), remove: (n: string) => c.delete(n), contains: (n: string) => c.has(n) }; }
    appendChild(e: El) { e.parent = this; this.children.push(e); return e; }
    insertBefore(e: El, ref: El | null) { e.parent = this; const i = ref ? this.children.indexOf(ref) : -1; if (i < 0) this.children.push(e); else this.children.splice(i, 0, e); return e; }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((x) => x !== this); }
    addEventListener() { /* no-op */ }
    all(): El[] { return this.children.flatMap((c) => [c, ...c.all()]); }
    matches(sel: string): boolean {
      // "select.picker-auth-select", ".picker-be-opt", ".picker-be-opt.sel", ".picker-be-opt[data-many]", ".picker-be-opt:not([data-many])", ".picker-auth-fixed", ".picker-be-opt[data-auth=\"login\"]"
      const m = /^([a-z]*)((?:\.[\w-]+)*)(\[data-([\w-]+)(?:="([^"]*)")?\])?(:not\(\[data-([\w-]+)\]\))?$/.exec(sel);
      if (!m) throw new Error("selector " + sel);
      if (m[1] && this.tag !== m[1]) return false;
      for (const c of (m[2] || "").split(".").filter(Boolean)) if (!this.cls.has(c)) return false;
      if (m[3]) { const v = this.dataset[m[4]]; if (v === undefined) return false; if (m[5] !== undefined && v !== m[5]) return false; }
      if (m[6] && this.dataset[m[7]] !== undefined) return false;
      return true;
    }
    querySelector(sel: string) { return this.all().find((e) => e.matches(sel)) || null; }
    querySelectorAll(sel: string) { return this.all().filter((e) => e.matches(sel)); }
  }
  const wrap = new El("div", "picker-backend picker-auth");
  wrap.style.display = "";
  const fixed = new El("span", "picker-auth-fixed"); fixed.style.display = "none";
  const b1 = new El("button", "picker-be-opt"); b1.dataset.auth = "login";
  const b2 = new El("button", "picker-be-opt"); b2.dataset.auth = "key";
  wrap.appendChild(b1); wrap.appendChild(b2); wrap.appendChild(fixed);
  const doc = { querySelector: (sel: string) => (sel === "#picker .picker-auth" ? wrap : null),
                createElement: (tag: string) => new El(tag) };
  const el = (tag: string, cls: string) => new El(tag, cls);
  const ts = [lift("authLoginChoices"), lift("syncPickerAuthMany"), lift("pickerAuthDropSelect"), lift("pickerAuthChoice")].join("\n");
  const src = requireCjs("esbuild").transformSync(ts, { loader: "ts", target: "es2020" }).code;   // the types go, the logic runs as shipped
  const fn = new Function("document", "el", src + "\nreturn { authLoginChoices, syncPickerAuthMany, pickerAuthChoice };");
  const api = fn(doc, el);
  const logins = [{ id: "", value: "login", label: "user@example.com · Acme · enterprise", machine: true, available: true },
                  { id: "0123456789ab", value: "login:0123456789ab", label: "Work · Acme · enterprise", available: true }];
  // three choices: buttons, the host's default selected, the static two hidden
  api.syncPickerAuthMany(wrap, { login: true, key: true, logins, default: "login:0123456789ab" });
  const many = wrap.querySelectorAll(".picker-be-opt[data-many]");
  assert.deepEqual(many.map((b) => b.textContent), ["Login (user@example.com · Acme · enterprise)", "Login (Work · Acme · enterprise)", "API key"]);
  assert.equal(b1.style.display, "none"); assert.equal(fixed.style.display, "none");
  assert.equal(api.pickerAuthChoice(), "login:0123456789ab", "the host's remembered stored login seeds the pick");
  // the pick survives a re-sync by value
  many[2].classList.add("sel"); many[1].classList.remove("sel");
  api.syncPickerAuthMany(wrap, { login: true, key: true, logins, default: "login:0123456789ab" });
  assert.equal(api.pickerAuthChoice(), "key");
  // an unavailable login is disabled with its reason and never seeded
  const refused = [...logins, { id: "abcdefabcdef", value: "login:abcdefabcdef", label: "Old", available: false, why: "the Old login was refused: expired" }];
  api.syncPickerAuthMany(wrap, { login: true, key: true, logins: refused, default: "login:abcdefabcdef" });
  const dd = wrap.querySelector("select.picker-auth-select");
  assert.ok(dd, "four choices: a dropdown");
  assert.equal(dd!.children.length, 4);
  assert.equal(dd!.children[2].disabled, true);
  assert.match(dd!.children[2].textContent, /unavailable: the Old login was refused/);
  assert.equal(api.pickerAuthChoice(), "key", "the held pick stands; a refused default is never seeded");
  assert.equal(wrap.querySelectorAll(".picker-be-opt[data-many]").length, 0, "buttons gave way to the dropdown");
  const sig = dd!.dataset.sig;
  api.syncPickerAuthMany(wrap, { login: true, key: true, logins: refused, default: "login:abcdefabcdef" });
  assert.equal(wrap.querySelector("select.picker-auth-select"), dd, "an unchanged choice set keeps the open dropdown");
  assert.equal(dd!.dataset.sig, sig);
  // one usable choice: written out, the reasons in the hover
  api.syncPickerAuthMany(wrap, { login: false, loginWhy: "no Claude login signed in on this machine", key: false, keyWhy: "no apiKeyHelper configured",
    logins: [{ id: "", value: "login", label: "", machine: true, available: false }, logins[1]] });
  assert.equal(fixed.style.display, "");
  assert.equal(fixed.textContent, "Login (Work · Acme · enterprise)");
  assert.match(fixed.title, /Login unavailable: no Claude login signed in on this machine/);
  assert.equal(wrap.querySelector("select.picker-auth-select"), null);
});

test("the gear lists the stored logins with a Remove and the feed names a refused login", () => {
  assert.match(GEAR, /<div id=rs-logins class=rs-logins style='margin-top:8px'><\/div>/);
  assert.match(GEAR, /fetch\(ku\('\/logins'\), \{ cache: 'no-store' \}\)\.then\(function \(r\) \{ return r\.json\(\); \}\)\.then\(lgLogins\)/);
  assert.match(GEAR, /post\(\{ type: 'loginRemove', id: r\.id \}\);\s*\n\s*row\.remove\(\);/);
  assert.match(GEAR, /lgFetchLogins\(\);\s*\/\/ …and the stored logins beside it \(T346\)/);
  assert.match(GEAR, /'No other Claude logins stored on this machine\. Add one with romp login add <label>\.'/);
  assert.match(GEARCSS, /\.rs-logins \.rs-login-row \{ display: flex; align-items: center; gap: 8px;/);
  assert.match(FEED, /it\.blocked\.login \? `⚠ Can't analyze · \$\{it\.blocked\.login\}`\s*\n\s*: it\.blocked\.mode === "key" \? "⚠ Can't analyze · API key" : "⚠ Can't analyze · login";/);
  assert.match(FEED, /mode\?: string; login\?: string; since\?: number;/);
});
