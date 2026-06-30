// Gallery driver — loaded in gallery.html alongside the real render.ts bundle.
// Builds a sidebar of scenes (from fixtures.ts), posts each scene's messages
// into the webview via postMessage when clicked, and wires SSE live-reload.
import { SCENES } from "./fixtures";

const sidebar = document.getElementById("gallery-sidebar")!;
const groups = new Map<string, HTMLElement>();

function selectScene(id: string) {
  document.querySelectorAll(".gal-item").forEach((el) =>
    el.classList.toggle("active", (el as HTMLElement).dataset.id === id));
  const scene = SCENES.find((s) => s.id === id);
  if (!scene) return;
  scene.messages.forEach((m) => window.postMessage(m, "*"));
  history.replaceState(null, "", `#${id}`);
}

for (const scene of SCENES) {
  let group = groups.get(scene.group);
  if (!group) {
    const h = document.createElement("div");
    h.className = "gal-group";
    h.textContent = scene.group;
    sidebar.appendChild(h);
    group = document.createElement("div");
    sidebar.appendChild(group);
    groups.set(scene.group, group);
  }
  const item = document.createElement("div");
  item.className = "gal-item";
  item.dataset.id = scene.id;
  item.textContent = scene.title;
  item.addEventListener("click", () => selectScene(scene.id));
  group.appendChild(item);
}

// Auto-select from hash or the first scene
const initial = location.hash.slice(1) || SCENES[0]?.id;
if (initial) selectScene(initial);

// SSE live-reload
const es = new EventSource("/sse");
es.addEventListener("reload", () => location.reload());
