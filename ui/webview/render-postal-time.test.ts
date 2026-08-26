// Postal message timestamp (the user 2026-06-13): a postal card no longer stamps the time inside
// its own box; it rides the left rail time-marker (HH:MM to the left of the dot) like every other
// event. The chat renderer has no jsdom harness, so — like the feed-*.test.ts files — pin it at the
// source level: the in-card postal-service-time span is gone, and the rail marker no longer excludes postal.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("postal cards no longer render an in-card time", () => {
  assert.doesNotMatch(RENDER, /"postal-service-time"/, "the in-card postal-service-time span must be gone");
  assert.doesNotMatch(CSS, /\.postal-service-time\b/, "the dead .postal-service-time rule must be removed");
});

test("the rail time-marker is applied to postal turns too (the postal exclusion is gone)", () => {
  assert.doesNotMatch(RENDER, /kind !== "postal-service"/, "the rail marker must not exclude postal cards");
  assert.match(RENDER, /if \(epoch != null && !renderingIntoThread && turn\.querySelector\(".dot"\)\) turn\.insertBefore\(timeMarker/);
});
