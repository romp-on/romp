// The ALL-KEYED box's rail read (the user 2026-08-15; notice deleted 2026-08-24): under API-key
// auth the usage windows are structurally absent — both usage.json writers skip keyed sessions —
// and the rail must not read as broken: the hover renders the spend it advertises even when no
// host has window bars. The old telemetryUnavailable flag + its "rate-limit telemetry unavailable
// under API-key auth" hover line were DELETED entirely (the user 2026-08-24: they know which
// machines are key-only and want the spend without a notice about rate limits that don't apply).
// No jsdom harness → source pins (the repo convention; rail-spend.test.ts is the pattern).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];

test("the hover renders the spend section even when NO host has window bars", () => {
  // the old tipHTML returned '' when every setHTML block was empty — BEFORE appending the spend
  // section, so an all-keyed box's API cell had an empty hover exactly when spend was all there
  // was to show. blocks are optional now; the spend section always appends.
  assert.ok(usageJS.includes("var h=blocks.length?"), "empty blocks no longer short-circuit");
  assert.ok(usageJS.includes("h+=fleetSpendHTML(sets);"));
  assert.ok(!usageJS.includes("if(!blocks.length)return '';"), "the early return is gone");
  assert.ok(!usageJS.includes("return h+fleetSpendHTML(sets);"),
    "the return-time append (unreachable on empty blocks) is gone with it");
});

test("the telemetry-unavailable notice is GONE end to end (the user 2026-08-24)", () => {
  // deleted, not relocated: they know which machines are key-only and want the spend without a
  // notice about rate limits that don't apply. No flag in the payload, no capture, no hover line.
  assert.ok(!KERNEL.includes('out["telemetryUnavailable"] = True'));
  assert.ok(!usageJS.includes("_telemUnavail"));
  assert.ok(!usageJS.includes("rate-limit telemetry unavailable"));
});
