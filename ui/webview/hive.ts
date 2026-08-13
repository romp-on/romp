// Hive — the game-feel command view (plans/hive.md): a 3D honeycomb, one hex pad + one
// little character per session, acting out that session's live state. Rides the feed
// payload over its own app=hive socket (ledgers + asks — see hive-model.ts); the scene is
// built ONCE and then mutated only by the model's diff events, never rebuilt per push, so
// nothing here can flap without new information (CLAUDE.md ## Design).
//
// Status colors keep their standing meanings (styles.css --st-*); the romp accent #9cd2ff
// is reserved for selection/hover chrome. All geometry is procedural — no runtime assets.
import * as THREE from "three";
import { assignSlots, axialToXZ, frameRadius, spiralSlot } from "./hive-layout";
import { buildSessions, diffSessions, HiveSession, HiveState } from "./hive-model";

const vscodeApi =
  typeof (window as any).acquireVsCodeApi === "function" ? (window as any).acquireVsCodeApi() : undefined;

// ── status palette (mirrors styles.css --st-*; hive draws in WebGL so the CSS vars can't
//    reach it — the values are pinned by hive-wiring.test.ts against styles.css instead) ──
const ST: Record<HiveState, number> = {
  working: 0xe0b020,     // gold — actively in a turn
  ready: 0x2b7fb8,       // calm blue — idle, nothing owed
  awaiting: 0xc0392b,    // needs YOU: a live permission/picker prompt
  blocked: 0xe5484d,     // alarm red — stopped on an API error
  retrying: 0xe08020,    // amber — riding an api-retry storm
  awaitingBg: 0x54b204,  // green — idle main thread, waiting on background work
  compacting: 0x14b8a6,  // teal — context operation in flight
  clearing: 0x14b8a6,
  interrupting: 0x8a8a8a,
  opening: 0x9aa0a6,     // pale — CLI still coming up
};
const ACCENT = 0x9cd2ff;
const HEX_SIZE = 2.05;        // axial size: neighbour centers sit √3·size apart
const PAD_R = 1.62;           // pad circumradius (< √3/2·size, so pads never touch)
const PAD_H = 0.42;

// exponential smoothing toward a target — frame-rate independent; `rate`/s is the snap speed
function ease(cur: number, target: number, dt: number, rate: number): number {
  return cur + (target - cur) * (1 - Math.exp(-rate * dt));
}

// ── one session's pad: hex prism + status ring + label + its dweller ─────────────────────
class Pad {
  group = new THREE.Group();
  private padMesh: THREE.Mesh;
  private ring: THREE.Mesh;
  private ringMat: THREE.MeshBasicMaterial;
  private sonar: THREE.Mesh;
  private sonarMat: THREE.MeshBasicMaterial;
  private label: THREE.Sprite;
  private bang: THREE.Sprite;              // the ❗ that bobs over a needs-you pad
  private guy: Dweller;
  private baseY = 0;
  lift = 0;                                 // hover/press target offset, eased in update()
  private liftCur = 0;
  private ringColor = new THREE.Color(ST.ready);
  private ringTarget = new THREE.Color(ST.ready);
  private t = Math.random() * 100;          // free-running clock, de-synced per pad
  private spawnT = 0;                       // 0→1 arrival pop
  dyingT = -1;                              // ≥0 → departure animation clock
  sess: HiveSession;

  constructor(sess: HiveSession, slot: number) {
    this.sess = sess;
    const { x, z } = axialToXZ(spiralSlot(slot), HEX_SIZE);
    this.group.position.set(x, 0, z);

    const tint = new THREE.Color(sess.color?.bg || "#8a8a8a");
    const top = new THREE.Color(0x2f3136).lerp(tint, 0.22);
    const side = new THREE.Color(0x232427).lerp(tint, 0.10);
    // CylinderGeometry with 6 radial segments IS the hex prism; thetaStart π/6 turns an
    // edge (not a corner) toward each axial neighbour so the honeycomb reads as tiling.
    const geo = new THREE.CylinderGeometry(PAD_R, PAD_R * 1.06, PAD_H, 6, 1, false, Math.PI / 6);
    this.padMesh = new THREE.Mesh(geo, [
      new THREE.MeshStandardMaterial({ color: side, roughness: 0.9, metalness: 0 }),
      new THREE.MeshStandardMaterial({ color: top, roughness: 0.82, metalness: 0 }),
      new THREE.MeshStandardMaterial({ color: side, roughness: 0.9, metalness: 0 }),
    ]);
    this.padMesh.position.y = PAD_H / 2;
    this.group.add(this.padMesh);

    // status ring: a hex annulus laid flat on the pad top. thetaStart -π/3 lines its corners
    // up with the cylinder's (thetaStart π/6) once RingGeometry's XY maps into XZ below —
    // the two parametrise their angles from different axes.
    this.ringMat = new THREE.MeshBasicMaterial({ color: ST[sess.state], transparent: true, opacity: 0.9 });
    const ringGeo = new THREE.RingGeometry(PAD_R * 0.74, PAD_R * 0.82, 6, 1, -Math.PI / 3);
    this.ring = new THREE.Mesh(ringGeo, this.ringMat);
    this.ring.rotation.x = -Math.PI / 2;
    this.ring.position.y = PAD_H + 0.015;
    this.group.add(this.ring);

    // sonar ping: an expanding, fading copy — the needs-you beacon (awaiting only)
    this.sonarMat = new THREE.MeshBasicMaterial({ color: ST.awaiting, transparent: true, opacity: 0, side: THREE.DoubleSide });
    this.sonar = new THREE.Mesh(new THREE.RingGeometry(PAD_R * 0.78, 0.045 + PAD_R * 0.78, 6, 1, -Math.PI / 3), this.sonarMat);
    this.sonar.rotation.copy(this.ring.rotation);
    this.sonar.position.y = this.ring.position.y;
    this.group.add(this.sonar);

    this.label = makeTextSprite(sess.name, sess.color?.bg || "#cccccc");
    this.label.position.y = 3.1;
    this.group.add(this.label);

    this.bang = makeTextSprite("!", "#ffffff", "#c0392b");
    this.bang.position.y = 2.35;
    this.bang.visible = false;
    this.group.add(this.bang);

    this.guy = new Dweller(sess.color?.bg || "#9cd2ff");
    this.guy.group.position.y = PAD_H;
    this.group.add(this.guy.group);
    this.guy.setState(sess.state, sess.faded);

    this.group.scale.setScalar(0.001);      // arrival pop plays from ~zero
    this.ringColor.setHex(ST[sess.state]);
    this.ringTarget.setHex(ST[sess.state]);
  }

  // a real state/name change arrived (diff event) — retarget; update() animates the morph
  apply(sess: HiveSession, stateChanged: boolean) {
    const prevName = this.sess.name, prevColor = this.sess.color?.bg;
    this.sess = sess;
    if (stateChanged) {
      this.ringTarget.setHex(ST[sess.state]);
      this.guy.setState(sess.state, sess.faded);
    }
    if (sess.name !== prevName || sess.color?.bg !== prevColor) {
      this.group.remove(this.label);
      disposeSprite(this.label);
      this.label = makeTextSprite(sess.name, sess.color?.bg || "#cccccc");
      this.label.position.y = 3.1;
      this.group.add(this.label);
    }
  }

  hitMeshes(): THREE.Object3D[] { return [this.padMesh]; }

  update(dt: number, camYaw: number): boolean {
    this.t += dt;
    if (this.spawnT < 1) {
      this.spawnT = Math.min(1, this.spawnT + dt / 0.5);
      const s = this.spawnT;
      const overshoot = 1 + 0.28 * Math.sin(s * Math.PI) * (1 - s);   // pop past 1, settle back
      this.group.scale.setScalar(Math.max(0.001, s * overshoot));
    }
    if (this.dyingT >= 0) {
      // departure: a small farewell hop, then sink through the floor and fade
      this.dyingT += dt;
      const d = this.dyingT;
      this.group.position.y = d < 0.25 ? Math.sin(d / 0.25 * Math.PI) * 0.3 : -(d - 0.25) * 2.2;
      const sc = Math.max(0.001, 1 - Math.max(0, d - 0.25) * 1.1);
      this.group.scale.setScalar(sc);
      return d > 1.15;                      // done → caller disposes
    }
    this.liftCur = ease(this.liftCur, this.lift, dt, 14);
    this.group.position.y = this.liftCur;

    this.ringColor.lerp(this.ringTarget, 1 - Math.exp(-8 * dt));
    this.ringMat.color.copy(this.ringColor);
    const st = this.sess.state;
    // pulse only the states that are genuinely in motion; steady states hold steady
    if (st === "working") this.ringMat.opacity = 0.62 + 0.3 * (0.5 + 0.5 * Math.sin(this.t * 3.6));
    else if (st === "awaiting") this.ringMat.opacity = 0.55 + 0.45 * (0.5 + 0.5 * Math.sin(this.t * 7));
    else if (st === "retrying") this.ringMat.opacity = 0.5 + 0.5 * (Math.sin(this.t * 11) > 0.2 ? 1 : 0.35);
    else this.ringMat.opacity = 0.85;

    if (st === "awaiting") {
      // sonar ping: 1.4s loop, ring swells to ~1.8× and fades — visible from any zoom
      const p = (this.t % 1.4) / 1.4;
      this.sonarMat.opacity = (1 - p) * 0.5;
      this.sonar.scale.setScalar(1 + p * 0.85);
      this.bang.visible = true;
      this.bang.position.y = 2.35 + 0.14 * Math.abs(Math.sin(this.t * 5));
    } else {
      this.sonarMat.opacity = 0;
      this.bang.visible = false;
    }
    this.guy.update(dt, this.t, camYaw);
    return false;
  }

  dispose() {
    this.group.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.geometry) m.geometry.dispose();
      const mats = Array.isArray(m.material) ? m.material : m.material ? [m.material] : [];
      for (const mat of mats) { const t = (mat as THREE.MeshBasicMaterial).map; if (t) t.dispose(); mat.dispose(); }
    });
  }
}

// ── the little guy (v1 rig: capsule + eyes, per-state posture/motion; deeper costumes and
//    props land with the character pass — plans/hive.md "State → performance") ────────────
class Dweller {
  group = new THREE.Group();
  private body: THREE.Mesh;
  private bodyMat: THREE.MeshStandardMaterial;
  private eyeL: THREE.Group; private eyeR: THREE.Group;
  private aura: THREE.Mesh;                 // compacting swirl / awaitingBg marker, recolored per state
  private auraMat: THREE.MeshBasicMaterial;
  private state: HiveState = "ready";
  private faded = false;
  private blinkAt = 2 + Math.random() * 4;
  private blinkT = -1;
  private phase = Math.random() * Math.PI * 2;
  private baseColor: THREE.Color;
  private pop = 0;                          // hatch flourish clock (opening → live)

  constructor(tint: string) {
    this.baseColor = new THREE.Color(tint).lerp(new THREE.Color(0xffffff), 0.12);
    this.bodyMat = new THREE.MeshStandardMaterial({ color: this.baseColor.clone(), roughness: 0.6 });
    this.body = new THREE.Mesh(new THREE.CapsuleGeometry(0.34, 0.42, 6, 14), this.bodyMat);
    this.body.position.y = 0.56;
    this.group.add(this.body);
    const mkEye = () => {
      const g = new THREE.Group();
      const white = new THREE.Mesh(new THREE.SphereGeometry(0.085, 10, 10), new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.35 }));
      const pupil = new THREE.Mesh(new THREE.SphereGeometry(0.038, 8, 8), new THREE.MeshStandardMaterial({ color: 0x1a1a1a, roughness: 0.3 }));
      pupil.position.z = 0.062;
      g.add(white, pupil);
      return g;
    };
    this.eyeL = mkEye(); this.eyeR = mkEye();
    this.eyeL.position.set(-0.125, 0.78, 0.28);
    this.eyeR.position.set(0.125, 0.78, 0.28);
    this.group.add(this.eyeL, this.eyeR);
    this.auraMat = new THREE.MeshBasicMaterial({ color: ST.compacting, transparent: true, opacity: 0 });
    this.aura = new THREE.Mesh(new THREE.TorusGeometry(0.52, 0.035, 6, 24), this.auraMat);
    this.aura.position.y = 0.62;
    this.aura.rotation.x = Math.PI / 2.4;
    this.group.add(this.aura);
  }

  setState(s: HiveState, faded: boolean) {
    if (this.state === "opening" && s !== "opening") {
      this.pop = 0.45;                       // hatched! — one pop, then normal life
      this.bodyMat.color.copy(this.baseColor);
    }
    this.state = s; this.faded = faded;
  }

  update(dt: number, t: number, camYaw: number) {
    const s = this.state;
    // blink (life for every state except the egg)
    if (s !== "opening") {
      this.blinkAt -= dt;
      if (this.blinkAt <= 0) { this.blinkT = 0.12; this.blinkAt = 3 + Math.random() * 4; }
      if (this.blinkT > 0) this.blinkT -= dt;
      const bl = this.blinkT > 0 ? 0.12 : 1;
      this.eyeL.scale.y = bl; this.eyeR.scale.y = bl;
    }
    const breathe = 1 + 0.02 * Math.sin(t * 2.1 + this.phase);
    this.body.scale.set(1, breathe, 1);
    if (this.pop > 0) {
      this.pop = Math.max(0, this.pop - dt);
      const p = this.pop / 0.45;             // 1→0: a quick overshoot pulse on the whole body
      this.body.scale.multiplyScalar(1 + 0.3 * Math.sin(p * Math.PI));
    }

    let y = 0, rotX = 0, rotZ = 0, yaw = 0, sx = 0;
    let aura = 0;
    switch (s) {
      case "working": {
        // typing bursts: bob fast for ~1.4s, rest ~0.8s — the rhythm reads as real keys
        const burst = Math.sin(t * 2.8 + this.phase) > -0.35;
        rotX = 0.14;
        y = burst ? 0.02 * Math.abs(Math.sin(t * 11)) : 0;
        break;
      }
      case "awaiting":                       // they need YOU: face the camera and wave
        yaw = camYaw;
        y = 0.2 * Math.abs(Math.sin(t * 4.6));
        rotZ = 0.14 * Math.sin(t * 6.5);
        break;
      case "blocked":
        rotZ = 0.42; y = -0.04; rotX = 0.1;  // slumped against the wreckage
        break;
      case "retrying":
        sx = 0.34 * Math.sin(t * 1.6);       // pacing the pad
        yaw = Math.cos(t * 1.6) > 0 ? Math.PI / 2 : -Math.PI / 2;
        break;
      case "awaitingBg":
        rotX = -0.12;                        // leaning back, watching its dispatched work
        aura = 0.4; this.auraMat.color.setHex(ST.awaitingBg);
        this.aura.rotation.z = t * 0.9;
        break;
      case "compacting": case "clearing":
        y = 0.16 + 0.05 * Math.sin(t * 2.4); // levitating meditation, teal swirl orbiting
        yaw = t * 0.8;
        aura = 0.75; this.auraMat.color.setHex(ST.compacting);
        this.aura.rotation.z = t * 2.2;
        break;
      case "interrupting":
        this.body.scale.y = 0.85;            // freeze-frame squash; no motion at all
        break;
      case "opening":
        // the egg: eyes hidden, whole body wobbling toward the hatch
        this.eyeL.scale.setScalar(0.001); this.eyeR.scale.setScalar(0.001);
        this.bodyMat.color.lerp(new THREE.Color(0xf2ead9), 0.2);
        rotZ = 0.09 * Math.sin(t * 9);
        break;
      default:                               // ready
        if (this.faded) { rotX = -0.3; y = -0.06; }   // dozing off after an hour idle
        else rotZ = 0.03 * Math.sin(t * 1.3 + this.phase);
    }
    if (s !== "opening") { this.eyeL.scale.x = 1; this.eyeR.scale.x = 1; this.eyeL.scale.z = 1; this.eyeR.scale.z = 1; }
    this.group.position.x = sx;
    this.group.position.y = PAD_H + y;
    this.group.rotation.set(rotX, yaw, rotZ);
    this.auraMat.opacity = ease(this.auraMat.opacity, aura, dt, 6);
  }
}

// name/❗ sprites: canvas-drawn, crisp at 2× — the one text surface WebGL owns; everything
// readable-at-length (the fly-in card) stays DOM
function makeTextSprite(text: string, color: string, bubble?: string): THREE.Sprite {
  const c = document.createElement("canvas");
  const ctx = c.getContext("2d")!;
  const font = "600 44px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
  ctx.font = font;
  const w = Math.min(560, Math.max(bubble ? 72 : 120, ctx.measureText(text).width + (bubble ? 44 : 28)));
  c.width = Math.ceil(w); c.height = bubble ? 96 : 64;
  const ctx2 = c.getContext("2d")!;
  ctx2.font = font;
  ctx2.textAlign = "center"; ctx2.textBaseline = "middle";
  if (bubble) {
    ctx2.fillStyle = bubble;
    const r = 26, cw = c.width, ch = c.height;
    ctx2.beginPath();
    ctx2.roundRect(cw / 2 - 34, 6, 68, 68, r);
    ctx2.fill();
    ctx2.moveTo(cw / 2, ch - 2); ctx2.lineTo(cw / 2 - 12, ch - 22); ctx2.lineTo(cw / 2 + 12, ch - 22);
    ctx2.fill();
  } else {
    ctx2.shadowColor = "rgba(0,0,0,0.85)"; ctx2.shadowBlur = 8;
  }
  ctx2.fillStyle = color;
  ctx2.fillText(text, c.width / 2, bubble ? 40 : c.height / 2, c.width - 16);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
  sp.scale.set(c.width / 110, c.height / 110, 1);
  return sp;
}
function disposeSprite(s: THREE.Sprite) { s.material.map?.dispose(); s.material.dispose(); }

// ── confetti / puffs: one pooled particle system for every burst ─────────────────────────
class Particles {
  points: THREE.Points;
  private geo = new THREE.BufferGeometry();
  private max = 600;
  private pos = new Float32Array(this.max * 3);
  private col = new Float32Array(this.max * 3);
  private vel: Float32Array = new Float32Array(this.max * 3);
  private life = new Float32Array(this.max);
  private n = 0;

  constructor() {
    this.geo.setAttribute("position", new THREE.BufferAttribute(this.pos, 3));
    this.geo.setAttribute("color", new THREE.BufferAttribute(this.col, 3));
    this.points = new THREE.Points(this.geo, new THREE.PointsMaterial({
      size: 0.14, vertexColors: true, transparent: true, opacity: 0.95, depthWrite: false,
    }));
    this.points.frustumCulled = false;
    this.geo.setDrawRange(0, 0);
  }

  burst(at: THREE.Vector3, colors: number[], count: number, speed: number) {
    for (let i = 0; i < count && this.n < this.max; i++, this.n++) {
      const j = this.n * 3;
      this.pos[j] = at.x; this.pos[j + 1] = at.y; this.pos[j + 2] = at.z;
      const th = Math.random() * Math.PI * 2, up = 0.5 + Math.random() * 0.9;
      this.vel[j] = Math.cos(th) * speed * (0.4 + Math.random() * 0.6);
      this.vel[j + 1] = up * speed;
      this.vel[j + 2] = Math.sin(th) * speed * (0.4 + Math.random() * 0.6);
      const c = new THREE.Color(colors[i % colors.length]);
      this.col[j] = c.r; this.col[j + 1] = c.g; this.col[j + 2] = c.b;
      this.life[this.n] = 1.1 + Math.random() * 0.5;
    }
  }

  update(dt: number) {
    let w = 0;
    for (let i = 0; i < this.n; i++) {
      this.life[i] -= dt;
      if (this.life[i] <= 0) continue;
      const j = i * 3, k = w * 3;
      this.vel[j + 1] -= 7.5 * dt;
      this.pos[k] = this.pos[j] + this.vel[j] * dt;
      this.pos[k + 1] = this.pos[j + 1] + this.vel[j + 1] * dt;
      this.pos[k + 2] = this.pos[j + 2] + this.vel[j + 2] * dt;
      if (w !== i) {
        this.vel[k] = this.vel[j]; this.vel[k + 1] = this.vel[j + 1]; this.vel[k + 2] = this.vel[j + 2];
        this.col[k] = this.col[j]; this.col[k + 1] = this.col[j + 1]; this.col[k + 2] = this.col[j + 2];
        this.life[w] = this.life[i];
      }
      w++;
    }
    this.n = w;
    this.geo.setDrawRange(0, this.n);
    (this.geo.attributes.position as THREE.BufferAttribute).needsUpdate = true;
    (this.geo.attributes.color as THREE.BufferAttribute).needsUpdate = true;
  }
}

// ── the world ────────────────────────────────────────────────────────────────────────────
class HiveWorld {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private pads = new Map<string, Pad>();
  private slots = new Map<string, number>();
  private particles = new Particles();
  private raycaster = new THREE.Raycaster();
  private pointer = new THREE.Vector2(-2, -2);
  private hovered: string | null = null;
  selected: string | null = null;
  // camera rig: yaw/pitch/dist orbit around an eased target — every value glides, per the
  // "everything springs, nothing teleports" rule
  private yaw = 0.0; private yawCur = 0.0;
  private pitch = 0.72; private pitchCur = 0.72;
  private dist = 26; private distCur = 30;
  private target = new THREE.Vector3(); private targetCur = new THREE.Vector3();
  private idleT = 99;                        // seconds since last user camera input
  private running = false;
  private visible = true;
  private lastFrame = 0;
  private dragging: { mode: "orbit" | "pan"; x: number; y: number } | null = null;
  private clock = 0;

  constructor(private root: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setClearColor(0x1e1e1e);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    root.appendChild(this.renderer.domElement);
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.1, 400);
    this.scene.fog = new THREE.FogExp2(0x1e1e1e, 0.016);
    this.scene.add(new THREE.HemisphereLight(0xbfd4e6, 0x2a2622, 1.15));
    const key = new THREE.DirectionalLight(0xfff2dd, 1.7);
    key.position.set(7, 12, 5);
    this.scene.add(key);
    const rim = new THREE.DirectionalLight(ACCENT, 0.5);
    rim.position.set(-6, 6, -8);
    this.scene.add(rim);
    this.scene.add(this.particles.points);

    const cv = this.renderer.domElement;
    cv.addEventListener("pointermove", (e) => this.onPointerMove(e));
    cv.addEventListener("pointerdown", (e) => this.onPointerDown(e));
    window.addEventListener("pointerup", () => { this.dragging = null; });
    cv.addEventListener("wheel", (e) => {
      e.preventDefault();
      this.dist = Math.min(70, Math.max(7, this.dist * Math.exp(e.deltaY * 0.0012)));
      this.idleT = 0;
    }, { passive: false });
    cv.addEventListener("dblclick", () => {
      const sid = this.hovered;
      if (sid) { vscodeApi?.postMessage({ type: "openSession", id: sid }); }
    });
    window.addEventListener("keydown", (e) => { if (e.key === "Escape") this.deselect(); });

    const fit = () => {
      const w = root.clientWidth || 1, h = root.clientHeight || 1;
      this.renderer.setSize(w, h, false);
      cv.style.width = "100%"; cv.style.height = "100%";
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
    };
    fit();
    new ResizeObserver(fit).observe(root);

    // render only while there's a viewer: page visible AND the pane on screen (a pane
    // toggled off is display:none → not intersecting). Paused = zero GPU work.
    const io = new IntersectionObserver((es) => {
      this.visible = es.some((e) => e.isIntersecting);
      this.ensureLoop();
    });
    io.observe(root);
    document.addEventListener("visibilitychange", () => this.ensureLoop());
    this.ensureLoop();
  }

  private canvasPoint(e: PointerEvent) {
    const r = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  }

  private onPointerMove(e: PointerEvent) {
    this.canvasPoint(e);
    if (this.dragging) {
      const dx = e.clientX - this.dragging.x, dy = e.clientY - this.dragging.y;
      this.dragging.x = e.clientX; this.dragging.y = e.clientY;
      this.idleT = 0;
      if (this.dragging.mode === "orbit") {
        this.yaw -= dx * 0.0052;
        this.pitch = Math.min(1.32, Math.max(0.32, this.pitch + dy * 0.004));
      } else {
        // pan in the ground plane, screen-aligned
        const s = this.distCur * 0.0016;
        const right = new THREE.Vector3(Math.cos(this.yawCur), 0, -Math.sin(this.yawCur));
        const fwd = new THREE.Vector3(-Math.sin(this.yawCur), 0, -Math.cos(this.yawCur));
        this.target.addScaledVector(right, -dx * s).addScaledVector(fwd, dy * s);
      }
    }
  }

  private onPointerDown(e: PointerEvent) {
    this.canvasPoint(e);
    const sid = this.pick();
    if (e.button === 2 || e.shiftKey) { this.dragging = { mode: "pan", x: e.clientX, y: e.clientY }; return; }
    if (sid) {
      // acknowledge on the DOWN, before anything async: the pad dips under the press
      const pad = this.pads.get(sid);
      if (pad) { pad.lift = -0.07; setTimeout(() => { if (this.pads.get(sid) === pad) pad.lift = this.hovered === sid ? 0.12 : 0; }, 130); }
      this.select(sid);
    } else {
      this.dragging = { mode: "orbit", x: e.clientX, y: e.clientY };
    }
  }

  private pick(): string | null {
    this.raycaster.setFromCamera(this.pointer, this.camera);
    let best: { sid: string; d: number } | null = null;
    for (const [sid, pad] of this.pads) {
      if (pad.dyingT >= 0) continue;
      const hits = this.raycaster.intersectObjects(pad.hitMeshes(), false);
      if (hits.length && (!best || hits[0].distance < best.d)) best = { sid, d: hits[0].distance };
    }
    return best ? best.sid : null;
  }

  select(sid: string) {
    this.selected = sid;
    const pad = this.pads.get(sid);
    if (pad) {
      this.target.copy(pad.group.position).setY(0.6);
      this.dist = 10.5;
      this.pitch = 0.62;
      this.idleT = 0;
    }
  }
  deselect() {
    if (this.selected === null) return;
    this.selected = null;
    this.frameAll();
  }

  frameAll() {
    const occupied = [...this.pads.values()].filter((p) => p.dyingT < 0);
    const slots = occupied.map((p) => this.slots.get(p.sess.sid) ?? 0);
    const r = Math.max(6, frameRadius(slots, HEX_SIZE));
    let cx = 0, cz = 0;
    for (const p of occupied) { cx += p.group.position.x; cz += p.group.position.z; }
    const n = Math.max(1, occupied.length);
    this.target.set(cx / n, 0, cz / n);
    this.dist = Math.min(70, Math.max(12, (r / Math.tan((this.camera.fov * Math.PI) / 360)) * 0.62));
    this.pitch = 0.72;
  }

  // apply one payload's worth of change — called with the model's diff, never per frame
  sync(sessions: HiveSession[], first: boolean) {
    const prevSessions = [...this.pads.values()].map((p) => p.sess);
    const diff = diffSessions(first ? null : prevSessions, sessions);
    const stored = loadSlots(this.slots);
    this.slots = assignSlots(stored, sessions.map((s) => s.sid));
    // persist the present sessions PLUS absent sids' remembered homes (a revived session
    // returns to its old hex), dropping the memories only if the map outgrows 200 entries
    const keep = new Map(this.slots);
    if (stored.size <= 200) for (const [k, v] of stored) if (!keep.has(k) && ![...keep.values()].includes(v)) keep.set(k, v);
    saveSlots(keep);
    const bySid = new Map(sessions.map((s) => [s.sid, s] as const));
    // a session that comes BACK while its pad is mid-departure gets a fresh pad — the dying
    // one can't be rewound (its death already told the true story of the earlier exit)
    for (const s of sessions) {
      const pad = this.pads.get(s.sid);
      if (pad && pad.dyingT >= 0) {
        this.scene.remove(pad.group);
        pad.dispose();
        this.pads.delete(s.sid);
        if (!diff.added.includes(s.sid)) diff.added.push(s.sid);
      }
    }

    for (const sid of diff.removed) {
      const pad = this.pads.get(sid);
      if (pad && pad.dyingT < 0) pad.dyingT = 0;   // departure plays; disposal in the loop
      if (this.selected === sid) this.deselect();
      if (this.hovered === sid) this.hovered = null;
    }
    for (const sid of diff.added) {
      const s = bySid.get(sid)!;
      const pad = new Pad(s, this.slots.get(sid) ?? 0);
      this.pads.set(sid, pad);
      this.scene.add(pad.group);
    }
    const changed = new Set(diff.stateChanged.map((c) => c.sid));
    for (const s of sessions) {
      const pad = this.pads.get(s.sid);
      if (pad && !diff.added.includes(s.sid)) pad.apply(s, changed.has(s.sid));
    }
    for (const sid of diff.goalDone) {
      const pad = this.pads.get(sid);
      if (pad) {
        const at = pad.group.position.clone().setY(PAD_H + 1.1);
        const tint = new THREE.Color(pad.sess.color?.bg || "#9cd2ff").getHex();
        this.particles.burst(at, [tint, 0xffffff, ACCENT, 0xffd700], 70, 4.2);
      }
    }
    if (first || diff.added.length || diff.removed.length) {
      if (this.selected === null) this.frameAll();
    }
    this.ensureLoop();
  }

  private ensureLoop() {
    const want = this.visible && !document.hidden;
    if (want && !this.running) {
      this.running = true;
      this.lastFrame = performance.now();
      requestAnimationFrame(this.frame);
    } else if (!want) {
      this.running = false;                  // the in-flight rAF sees this and stops
    }
  }

  private frame = (now: number) => {
    if (!this.running) return;
    const dt = Math.min(0.05, (now - this.lastFrame) / 1000);
    this.lastFrame = now;
    this.clock += dt;
    this.idleT += dt;

    // hover pick once per frame (not per pointermove — cheaper and steadier)
    const sid = this.pick();
    if (sid !== this.hovered) {
      const old = this.hovered ? this.pads.get(this.hovered) : null;
      if (old) old.lift = 0;
      this.hovered = sid;
      const nw = sid ? this.pads.get(sid) : null;
      if (nw) nw.lift = 0.12;
      this.renderer.domElement.style.cursor = sid ? "pointer" : "default";
    }

    // idle drift: after 6s hands-off the whole board breathes on a slow orbital sway
    const driftYaw = this.idleT > 6 ? Math.sin(this.clock * 0.1) * 0.05 : 0;
    this.yawCur = ease(this.yawCur, this.yaw + driftYaw, dt, 5);
    this.pitchCur = ease(this.pitchCur, this.pitch, dt, 5);
    this.distCur = ease(this.distCur, this.dist, dt, 5);
    this.targetCur.lerp(this.target, 1 - Math.exp(-5 * dt));
    this.camera.position.set(
      this.targetCur.x + this.distCur * Math.cos(this.pitchCur) * Math.sin(this.yawCur),
      this.targetCur.y + this.distCur * Math.sin(this.pitchCur),
      this.targetCur.z + this.distCur * Math.cos(this.pitchCur) * Math.cos(this.yawCur),
    );
    this.camera.lookAt(this.targetCur);

    const dead: string[] = [];
    for (const [psid, pad] of this.pads) if (pad.update(dt, this.yawCur)) dead.push(psid);
    for (const psid of dead) {
      const pad = this.pads.get(psid)!;
      this.scene.remove(pad.group);
      pad.dispose();
      this.pads.delete(psid);
    }
    this.particles.update(dt);

    this.renderer.render(this.scene, this.camera);
    requestAnimationFrame(this.frame);
  };
}

// slot persistence: the board must look the same after a reload — spatial memory is the
// point of the hex layout. Plain sid→slot map; entries for sids gone from the payload are
// kept (a revived session returns HOME) until the map grows past 200, then absentees drop.
const SLOTS_KEY = "romp:hiveSlots";
function loadSlots(live: Map<string, number>): Map<string, number> {
  try {
    const d = JSON.parse(localStorage.getItem(SLOTS_KEY) || "null");
    const m = new Map<string, number>();
    if (d && typeof d === "object") for (const k of Object.keys(d)) if (Number.isInteger(d[k])) m.set(k, d[k]);
    for (const [k, v] of live) m.set(k, v);
    return m;
  } catch { return new Map(live); }
}
function saveSlots(m: Map<string, number>) {
  try {
    const o: Record<string, number> = {};
    for (const [k, v] of m) o[k] = v;
    localStorage.setItem(SLOTS_KEY, JSON.stringify(o));
  } catch { /* private mode etc — the board still works, it just re-deals on reload */ }
}

// ── boot ─────────────────────────────────────────────────────────────────────────────────
let world: HiveWorld | null = null;
let firstPayload = true;

window.addEventListener("message", (e: MessageEvent) => {
  const m = e.data;
  if (!m || m.type !== "feed") return;
  const sessions = buildSessions(m);
  if (sessions === null) return;             // ledgers not built yet → loader stays up
  const root = document.getElementById("hive-root");
  if (!root) return;
  if (!world) world = new HiveWorld(root);   // first real data: mount → _pane_spin fades
  world.sync(sessions, firstPayload);
  firstPayload = false;
});

vscodeApi?.postMessage({ type: "ready" });   // ask the kernel for the connect-time push

export {};
