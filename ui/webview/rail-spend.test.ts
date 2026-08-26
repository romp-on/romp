// API-KEY SPEND on the rail (redesigned 2026-08-08, with per-session auth): spend is NUMBERS, never
// bars — the old hover graph scaled each window's bar to the largest window, a shape that told the
// reader nothing, and the budget-fill tracks on the web rail died with it. A host's payload can now
// carry a login's WINDOWS and its key's SPEND at once (`spend` beside the bars, keyed-only sums), so
// presence of the spend windows — not the legacy apiKey flag — is what turns the dollars on. The
// collapsed web rail shows one API cell (constant 'API' label + 5h/month dollars — no key material,
// not even a last-4 tail, the user 2026-08-08 evening); the hover breaks spend down
// per window per host. Spend accumulates per ResultMessage (total_cost_usd + usage tokens) into
// spend.json's day AND hour buckets, each bucket carrying a `key` sub-count for key-billed turns.
// No jsdom harness → source pins (the repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const STRIP = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.ts"), "utf8");
const STRIPCSS = fs.readFileSync(path.join(ROOT, "ui", "webview", "strip.css"), "utf8");
const BACKEND = fs.readFileSync(path.join(ROOT, "kernel", "sdk_backend.py"), "utf8");

test("the kernel serves spend windows for BOTH payload shapes, keyed-only beside bars", () => {
  // the spend-only view arms on the legacy apiKey marker OR a login-less machine with recorded spend,
  // and keeps TOTAL sums (everything there bills the key; legacy files predate the split)
  assert.ok(KERNEL.includes('if o.get("apiKey") or (not _claude_account() and (jd.STATE / "spend.json").exists()):'));
  assert.ok(KERNEL.includes('out = {"apiKey": True, "spend": _spend_windows(),'));
  // the hover's spend rows lead with the rolling hour (the user 2026-08-15); the collapsed cell keeps day+month
  assert.ok(KERNEL.includes("var SPEND_WINS=[['hour','1 hour'],['day','1 day'],['week','1 week'],['month','1 month']];"));
  assert.ok(KERNEL.includes('"hour": _rolling(1),'));
  // …and the $/hour series rides beside the windows for the hover graph (the user 2026-08-13)
  assert.ok(KERNEL.includes('out["spendSeries"] = ss'));
  // the bars payload attaches the KEYED split only — a login turn's computed cost there would be
  // dollars nobody is billed — and only when key turns actually exist (the user 2026-08-08)
  assert.ok(KERNEL.includes("def _spend_windows(keyed_only=False):"));
  assert.ok(KERNEL.includes("ksp = _spend_windows(keyed_only=True)"));
  assert.match(KERNEL, /if any\(\(ksp\.get\(k\) or \{\}\)\.get\("turns"\) for k in \("day", "week", "month"\)\):/);
  // no fragment of the key rides ANY payload (the user 2026-08-08, evening): the tail plumbing is
  // gone from the kernel wholesale, and the keyed-spend gate is a plain existence check
  assert.ok(KERNEL.includes("if _auth_key_present():"));
  assert.ok(!KERNEL.includes("apiTail"), "no key material in any usage payload");
  assert.ok(!KERNEL.includes("authTail"), "no key material in the status payload either");
  // rolling day/week read the HOUR buckets (192h = 8 days fits both); month-to-date reads the day
  // ledger. fiveHour/sevenDay stay emitted ONE release for version skew (the user 2026-08-13).
  assert.match(KERNEL, /"day": _rolling\(24\), "week": _rolling\(7 \* 24\)/);
  assert.match(KERNEL, /"fiveHour": _rolling\(5\), "sevenDay": _rolling\(7 \* 24\)/);
  assert.ok(KERNEL.includes("k.startswith(month)"));
  // the accumulator: cumulative-per-process DELTAS, and each bucket splits out the key's own turns
  assert.ok(BACKEND.includes("delta = total - self._last_cost_total if total >= self._last_cost_total else total"));
  assert.ok(BACKEND.includes("turn_u[k] = v - last if v >= last else v"));
  assert.ok(BACKEND.includes("self.backend._record_spend(delta, turn_u, keyed=self.api_key_auth, sid=self.sid)"),
    "the settle threads the session sid — per-session attribution (T100)");
  assert.ok(BACKEND.includes("if keyed or ke:   # carry an existing key split forward even on a login turn"));
  assert.ok(BACKEND.includes("_fold(days, day, 90)"));
  assert.ok(BACKEND.includes("_fold(hours, hour, 192)"), "8 days of hour buckets feed the rolling windows");
});

test("VS Code strip: spend is ONE API cell keyed on the windows' PRESENCE — the rail's twin", () => {
  // the strip mirrors apiCellHTML (the user 2026-08-11: the rail moved to the cell and the strip must
  // reflect it): constant 'API' label, 5-hour burn + month-to-date as designator → dollars·tokens
  // pairs, the full breakdown (7 days, turn counts) on the hover title. Spend-as-rows is gone.
  assert.match(STRIP, /export function apiCell\(usage: any\): ApiCell \| null/);
  // presence, not the apiKey flag: a mixed host's payload carries bars AND spend at once — and the
  // 5h window is the whole test, the same hasSpend branch the rail runs
  assert.ok(STRIP.includes("const sp = usage && usage.spend;"));
  // day||fiveHour: an older kernel ships no 'day' yet (version skew) — its 5h burn stands in
  assert.ok(STRIP.includes("if (!sp || !(sp.day || sp.fiveHour)) return null;"));
  assert.ok(STRIP.includes("const daySeg = sp.day || sp.fiveHour;"));
  assert.doesNotMatch(STRIP, /usage\.apiKey && usage\.spend/);
  assert.doesNotMatch(STRIP, /spendWindows/, "spend never renders as window rows any more");
  // the collapsed cell carries 1 day + 1 month (the user 2026-08-13: pay-per-token has no reset
  // windows); one display name per window, dollars AND tokens beside each other
  assert.match(STRIP, /\[\["day", "1 day", "1d", daySeg\],\s*\n\s*\["month", "1 month", "1mo", sp\.month\]\]/);
  assert.match(STRIP, /tok\.textContent = " · " \+ fmtTok\(s\.tok\) \+ " tok";/);
  // the old one-off chip is gone, and with it any minted style
  assert.doesNotMatch(STRIP, /spendChip/);
  assert.doesNotMatch(STRIPCSS, /\.ru-spend/);
});

test("the web rail's API cell is numbers under a constant label — no spend bars anywhere", () => {
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  // one compact cell: a bare 'API' label (never any key fragment), then per-window pairs in the
  // window cells' own grammar — the window's ONE display name, name-font, LEFT of its value, with
  // dollars AND tokens (the user 2026-08-09: no more '$12 5h' second vocabulary trailing the number)
  assert.ok(usageJS.includes("function apiCellHTML(live)"));
  assert.ok(usageJS.includes("'<div class=ru-name>API</div>'"));
  assert.ok(!usageJS.includes("_tail"), "no tail plumbing survives in the rail JS");
  assert.ok(usageJS.includes("seg('day','1 day')+seg('month','1 month')"));
  assert.ok(usageJS.includes("var d=sp.day||sp.fiveHour,m=sp.month;"), "older remote kernels stay visible");
  assert.ok(usageJS.includes("var seg=function(k,lbl){return '<div class=ru-name>'+lbl+'</div>'"));
  assert.ok(usageJS.includes("'<div class=ru-pct>'+fmtUsd(sum[k].usd)+' \\u00b7 '+fmtTok(sum[k].tok)+' tok</div>'"));
  // the graph and the budget fills are gone: no spend track, no spend color ramp, no shared scale
  assert.ok(!usageJS.includes("spendColor"), "the budget-fill ramp died with the spend bars");
  assert.ok(!usageJS.includes("spendWinsHTML"), "spend never renders as window rows with tracks");
  assert.ok(!usageJS.includes("var mx=1;"), "the token auto-scale graph is gone");
  // presence-keyed, like the strip
  assert.ok(usageJS.includes("function hasSpend(u){return !!(u&&u.spend&&(u.spend.day||u.spend.fiveHour));}"));
});

test("one display name per window, worn everywhere: bars, hover sections, API cell, notices", () => {
  // the user 2026-08-09: '5 hours' on the bars, '5h' on the API numbers and 'Session (5h)' in the
  // hover were three vocabularies for one thing — WINS now carries the ONE display name per window
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  assert.ok(usageJS.includes("var WINS=[['fiveHour',5*3600,'5 hours'],"));
  assert.ok(usageJS.includes("['sevenDay',7*86400,'7 days'],"));
  assert.ok(usageJS.includes("['fable',7*86400,'Fable 5']];"));
  assert.ok(usageJS.includes("'<div class=ru-name>'+w[2]+'</div>'"), "the collapsed bars wear it");
  // the hover window heading is the bare name — no '(5h)' span, no Session/Weekly third vocabulary
  assert.ok(usageJS.includes("'<div class=ru-tip-win><div class=ru-tip-name><span>'+esc(v.name)+'</span>'"));
  assert.ok(!usageJS.includes("'Session'"), "the Session/Weekly names are retired");
  assert.ok(!usageJS.includes("Weekly (7d)"));
  // the limit notice speaks the same windows, prose-shaped
  assert.ok(usageJS.includes("names.push('5-hour')"));
  assert.ok(usageJS.includes("names.push('7-day')"));
});

test("dollars are WHOLE everywhere — no cents on any spend surface", () => {
  // the user 2026-08-09: cents are noise at these magnitudes; the rail, its hover, and the VS Code
  // strip all round to whole dollars through their one formatter each
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  assert.ok(usageJS.includes("function fmtUsd(v){return '$'+String(Math.round(v));}"));
  assert.ok(!usageJS.includes("toFixed(2)"), "no cents anywhere in the rail JS");
  assert.match(STRIP, /export function fmtUsd\(v: number\): string \{ return "\$" \+ String\(Math\.round\(v\)\); \}/,
    "the strip rounds through the same one formatter");
  assert.doesNotMatch(STRIP, /usd\.toFixed\(2\)/);
});

test("the rich tip is the ONE hover surface: no native titles, per-host sections, numbers-only spend", () => {
  // NO native title attributes anywhere on the rail (the user 2026-08-08, who got the browser's flat
  // yellow box on top of the rich hover): the usage JS may mention titles only in comments
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  for (const line of usageJS.split("\n")) {
    const code = line.split("//")[0];
    assert.ok(!/\btitle\s*=/.test(code) && !code.includes(".title="), `native title in usage JS: ${line.trim()}`);
  }
  // host sections carry WINDOWS only now — spend is ONE fleet-level section (the user 2026-08-13:
  // one shared key reads as one number; each host records only its own turns, so the sum IS the number)
  assert.ok(usageJS.includes("if(!keys.length)return '';"));
  assert.ok(!usageJS.includes("spendOnly"), "spend renders for ANY host that has it");
  assert.ok(usageJS.includes("function fleetSpendHTML(sets)"));
  // the spend section renders even when NO host has window blocks (an all-keyed box, 2026-08-15) —
  // the appended form replaced `return h+fleetSpendHTML(sets);`, whose empty-blocks short-circuit
  // left the API cell's hover empty exactly when spend was all there was to show
  assert.ok(usageJS.includes("h+=fleetSpendHTML(sets);"));
  // …with the summed $/hour area graph and its peak beside it — labelled PER-HOUR (the user
  // 2026-08-13 read a bare 'peak $311' and had to ask whether that was one hour)
  assert.ok(usageJS.includes("moneyGraph(wk,'#9cd2ff',series.h0+st)"));
  assert.ok(usageJS.includes("'<span class=ru-tip-v>peak '+fmtUsd(mx)+'/h</span>"));
  // …and every machine in the sum BY NAME, largest first (the user 2026-08-13: the devbox — spend,
  // no login — vanished from the hover when per-host spend rows collapsed into the fleet section)
  assert.ok(usageJS.includes("per.push({host:e.host,usd:sp.week.usd})"));
  assert.ok(usageJS.includes("by machine \\u00b7 1 week"));
  assert.ok(usageJS.includes("per.sort(function(a,b){return b.usd-a.usd;})"));
  // numbers only: dollars · tokens · turns per window, under a plain 'API spend' heading
  assert.ok(usageJS.includes("function spendDet(u,det)"));
  assert.ok(usageJS.includes("API spend'+(hosts>1?' \\u00b7 '+hosts+' machines':'')"),
    "one fleet-level section — one shared key, one number (the user 2026-08-13)");
  assert.ok(usageJS.includes("fmtUsd(v.usd)+' \\u00b7 '+fmtTok(v.tok)+' tok \\u00b7 '+(v.turns||0)+' turns</span>"));
  // the tip anchors ABOVE the rail, centered on the CURSOR — never pinned to the container edge
  assert.ok(usageJS.includes("var x=(ev&&typeof ev.clientX==='number')?ev.clientX:(r.left+r.width/2);"));
  assert.ok(usageJS.includes("x-tip.offsetWidth/2"));
  assert.ok(usageJS.includes("r.top-tip.offsetHeight-8"));
  // (the footer click hint is gone — refresh is automatic; see the 2026-08-14 chart test below)
});

test("same-account hosts share the FRESHEST window reading — one truth per login", () => {
  // the user 2026-08-09: the windows are account-wide allowances, so a host that hadn't polled in
  // hours sat beside the live number contradicting it. Grouped on the acct digest, freshest t wins,
  // window fields shared; each host keeps its OWN key spend (dollars are host-local).
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  assert.ok(usageJS.includes("function shareFreshest(live)"));
  assert.ok(usageJS.includes("var a=r.usage&&r.usage.acct;if(a)(by[a]=by[a]||[]).push(r);"), "grouped on the digest; key-only hosts (no acct) stand alone");
  assert.ok(usageJS.includes("if(tr>tb)best=r;"), "freshest reading wins the group");
  assert.ok(usageJS.includes("['fiveHour','sevenDay','fable','t','limited','acctLabel'].forEach"), "window fields shared — spend deliberately not");
  assert.ok(usageJS.includes("shareFreshest(live);"), "…and it runs on every render");
});

test("the hover names WHICH login the window bars belong to (the tab hover's label)", () => {
  // the user 2026-08-09: the usage tip says whose account the windows are, like the tab hover;
  // the cross-host dedup stays on the opaque acct digest — acctLabel is display only
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  assert.ok(usageJS.includes("if(u.acctLabel)det._acct=u.acctLabel;"));
  assert.ok(usageJS.includes("if(d._acct&&keys.length)h+='<div class=ru-tip-acct>'+esc(d._acct)+'</div>';"),
    "…and only beside actual window sections — key-only hosts' spend already says whose dollars");
  assert.ok(KERNEL.includes('"acctLabel": _claude_account_label(),'));
  assert.ok(KERNEL.includes(".ru-tip-acct{"));
});

test("a multi-host breakdown lays hosts SIDE BY SIDE, one column each", () => {
  // the user 2026-08-09: the per-host breakdown used to stack every host into one tall pillar;
  // now each host is a flex column, and flex-wrap folds the mobile modal back to a stack on its own
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  assert.ok(usageJS.includes("'<div class=ru-tip-cols>'+blocks.map(function(b){return '<div class=ru-tip-col>'+b+'</div>';}).join('')+'</div>'"));
  // a single host keeps its plain un-columned layout — the column wrapper exists only when more
  // than one host reports (blocks.length? guards the all-keyed box, whose hosts have NO window
  // blocks at all — see expected-auth.test.ts)
  assert.ok(usageJS.includes("var h=blocks.length?(many?"));
  assert.ok(KERNEL.includes(".ru-tip-cols{display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap}"));
  assert.ok(KERNEL.includes(".ru-tip-col{flex:0 1 auto;min-width:200px}"));   // 200px floor since the sparks span the column (the user 2026-08-14)
});

test("every tip string carries data — the narration is gone and stays gone", () => {
  // The de-inking pass (the user 2026-08-08, who found the tip overly verbose): the host name alone
  // heads a section, the spend rows label themselves, config hints live in the docs, and the reader
  // is already hovering the bars when the refresh hint shows.
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  const code = usageJS.split("\n").map((l) => l.split("//")[0]).join("\n");
  assert.ok(!code.includes("its own allowance"), "the host heading is the host name, bare");
  assert.ok(!code.includes("one scale"), "the spend rows are their own labels");
  assert.ok(!code.includes("no budget set"), "no config instructions in a glance surface");
  assert.ok(!code.includes("current usage unknown"), "the ? row already says unknown");
  assert.ok(!code.includes("click the bars"), "the short hint replaced it");
  assert.ok(code.includes("window reset '+esc(v.ago)+'; no reading since"), "the rolled note keeps only its facts");
});

test("token counts carry 3 significant figures, and both fmtTok twins share the formula (the user 2026-08-13)", () => {
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  // one adaptive-decimals helper in each copy — 1.32B / 13.2B / 132B, trailing zeros kept
  assert.ok(usageJS.includes("function fmtSig3(v){return v.toFixed(v>=100?0:v>=10?1:2);}"));
  assert.ok(usageJS.includes("if(n>=1e9)return fmtSig3(n/1e9)+'B';"));
  assert.match(STRIP, /return v\.toFixed\(v >= 100 \? 0 : v >= 10 \? 1 : 2\);/);
  assert.match(STRIP, /if \(n >= 1e9\) return fmtSig3\(n \/ 1e9\) \+ "B";/);
  // the old 1-decimal + strip-trailing-zero form is gone from both
  assert.ok(!usageJS.includes("toFixed(1).replace"));
  assert.ok(!STRIP.includes('toFixed(1).replace'));
});

test("the $/h graph spans the tip, and the per-window sparks are gone and stay gone (the user 2026-08-14)", () => {
  // The graph sat 120px wide between its label and the peak with empty space either side; the peak
  // now rides the label row's right-aligned value slot and the graph gets its own full-width line.
  assert.match(KERNEL, /peak '\+fmtUsd\(mx\)\+'\/h<\/span><\/div>'\s*\n\+moneyGraph\(wk,'#9cd2ff',series\.h0\+st\);/,
    "the chart follows the closed label+peak row, full-width, x-aligned to h0");
  // The faint plate frames the plot area: sparse hours (lone dots — unknown ≠ 0) read as data.
  assert.ok(KERNEL.includes("height:56px;background:rgba(255,255,255,0.04);"),
    "the backing plate frames the $/h plot");
  // The per-window utilization sparklines under each window's bars are REMOVED (the user 2026-08-14:
  // the one fleet $/h graph is the graph; nothing per window). No payload, renderer, or style
  // remains; usage-history.json keeps recording so a future graph starts with history.
  assert.ok(!KERNEL.includes('out["winSeries"]'), "no per-window series rides the usage payload");
  assert.ok(!KERNEL.includes("_winSeries"), "the client neither captures nor reads a window series");
  assert.ok(!KERNEL.includes("sparkHTML"), "the per-window sparkline renderer is gone");
  assert.ok(!KERNEL.includes("ru-tip-spark"), "no spark markup or styles remain");
  assert.ok(BACKEND.includes("def _record_usage_history"), "the utilization ledger keeps recording");
});

test("the $/h chart is a real chart, and refresh is automatic with no stale hint (the user 2026-08-14)", () => {
  const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
  // y-axis scaled to the ceiling of the nearest $50; rules thin to $100/$200/... past four
  assert.ok(usageJS.includes("var top=50*Math.ceil(mx/50),step=50;"), "y ceiling = nearest $50");
  // the ceiling's line + its ONE label draw unconditionally, OFF the step loop (the user 2026-08-15:
  // step-doubling made top=450 miss the $200-step loop and the chart lost its only y label)
  assert.ok(usageJS.includes("for(var g=step;g<top;g+=step)"), "interior gridlines stop below the ceiling");
  assert.ok(usageJS.includes("var ty=Y(top);"), "the ceiling edge is computed once");
  assert.ok(usageJS.includes(`ylab='<span class=ru-tip-gy style="top:'+(ty/H*56).toFixed(0)+'px">$'+top+'</span>';`),
    "the one y label is the ceiling, always present");
  assert.ok(usageJS.includes("while(top/step>4)step*=2;"), "at most four horizontal rules");
  // x-axis: midnight ticks placed in LOCAL time off the series' epoch-hour base, weekday initials under
  assert.ok(usageJS.includes("if(d.getHours()===0)"), "midnight ticks");
  assert.ok(usageJS.includes("['S','M','T','W','T','F','S'][d.getDay()]"), "weekday initials");
  // the stretch to full width fattened every stroke (aspect distortion): strokes pin their screen
  // width, and a lone reading is a round-capped zero-length stroke, not a distortable <circle>
  assert.ok(usageJS.includes('stroke-width="1.5" vector-effect="non-scaling-stroke"'));
  assert.ok(usageJS.includes('stroke-linecap="round" vector-effect="non-scaling-stroke"'));
  assert.ok(!usageJS.includes("<circle"), "no distortable circles remain");
  // "click to refresh" misread on a hover: the hint is gone, the 60s auto-pull stands, and an
  // already-open hover tip re-renders in place when fresh data lands
  assert.ok(!usageJS.includes("click to refresh"), "no refresh hint on a hover surface");
  assert.ok(usageJS.includes("setInterval(function(){pull(false);},60000);"), "the 60s auto-pull stands");
  assert.ok(usageJS.includes("if(tip.style.display==='block'&&!tip.classList.contains('ru-modal'))"),
    "an open tip follows data landings");
});

test("the mobile usage modal is height-capped with a scroll pane, and taps on it don't fall through", () => {
  // The user 2026-08-14: the $/h chart + multi-host rows outgrew a phone screen, and the centered
  // translate(-50%,-50%) pushed the TOP off-screen with nothing to scroll. The modal variant caps its
  // height to the DYNAMIC viewport (dvh overrides vh where supported — mobile browser chrome collapses)
  // and scrolls; pointer-events comes back on (the base #ru-tip is a hover tooltip, pointer-events:none),
  // which is what makes scrolling possible AND stops a tap on the panel closing it through the backdrop.
  const modal = KERNEL.split("#ru-tip.ru-modal{")[1].split("}")[0];
  assert.ok(modal.includes("max-height:84vh"), "a vh cap for browsers without dvh");
  assert.ok(modal.includes("max-height:84dvh"), "the dvh override tracks mobile chrome");
  assert.ok(modal.indexOf("max-height:84vh") < modal.indexOf("max-height:84dvh"),
    "the dvh declaration must come second to win where supported");
  assert.ok(modal.includes("overflow-y:auto"), "the scroll pane");
  assert.ok(modal.includes("pointer-events:auto"), "scrollable — and panel taps no longer dismiss");
});
