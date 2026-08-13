// Nameplates lie flat ON their cell, map-label style (the user 2026-08-13, after the
// floating billboards — camera-scaled, bloom-fed — stacked into unreadable fog). A label
// that belongs to its cell can geometrically never overlap a neighbour's: the projection
// of disjoint coplanar regions stays disjoint under any camera. Source-pinned plus the fit
// arithmetic against the real cell dimensions from hive-layout.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { HEX_SIZE } from "./hive-layout";

const HIVE = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "hive.ts"), "utf8");
const decal = HIVE.slice(HIVE.indexOf("function makeNameDecal"), HIVE.indexOf("function disposeDecal"));
const num = (re: RegExp) => parseFloat((HIVE.match(re) || ["", "NaN"])[1]);

test("the nameplate FITS its cell, so names can never leave it or pile up", () => {
  const W = num(/const LABEL_W_MAX = ([\d.]+)/);
  const H = num(/const LABEL_H = ([\d.]+)/);
  const F = num(/const LABEL_FRONT = ([\d.]+)/);
  assert.ok(W > 0 && H > 0 && F > 0, "the constants exist");
  const apothem = (Math.sqrt(3) / 2) * HEX_SIZE;
  assert.ok(W <= Math.sqrt(3) * HEX_SIZE, "plate width within the across-flats span");
  const zFar = F + H / 2;
  assert.ok(zFar <= apothem, "the plate's far edge stays inside the apothem");
  // past the corner ring (z > size/2) the hex narrows linearly to its tip — still fits
  const xAt = zFar <= HEX_SIZE / 2 ? apothem : (apothem * (HEX_SIZE - zFar)) / (HEX_SIZE / 2);
  assert.ok(W / 2 <= xAt, `half-width ${W / 2} exceeds the cell's ${xAt} at z=${zFar}`);
});

test("flat on the tile, camera-yawed, ellipsized to width — never inflated by distance", () => {
  assert.match(decal, /mesh\.rotation\.x = -Math\.PI \/ 2;/, "the plate lies flat");
  assert.match(decal, /rest \+= "…";/, "long names ellipsize instead of spilling");
  assert.match(HIVE, /this\.labelYaw\.rotation\.y = camYaw;/, "yawed to face the camera");
  assert.ok(!HIVE.includes("camDist / 13"), "the old distance-upscaling is gone");
});

test("zooming out fades names away; hover/selection keeps yours readable", () => {
  assert.match(HIVE, /focus \? 1 : Math\.min\(1, Math\.max\(0, \(42 - camDist\) \/ 12\)\)/);
  assert.match(HIVE, /psid === this\.hovered \|\| psid === this\.selected/,
    "focus is the hovered or selected pad — an event of the user's pointer, not a heuristic");
});

test("crisp and bloom-safe: no glow, quiet host prefix, identity color dimmed", () => {
  assert.ok(!decal.includes("shadowBlur"), "nameplates carry NO glow — glow was the fog");
  assert.match(decal, /hostPrefix\(name, sid\)/, "a remote's host renders as the quiet prefix");
  assert.match(decal, /"#8a8a8a"/, "…in the standing quiet gray");
  assert.match(decal, /multiplyScalar\(0\.82\)/, "identity color held under the bloom threshold");
});
