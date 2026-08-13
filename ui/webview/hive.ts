// Hive — the game-feel command view (plans/hive.md): a 3D honeycomb, one hex pad + one
// little character per session, acting out that session's live state. Rides the feed
// payload over its own app=hive socket (ledgers + asks — see hive-model.ts); the scene is
// built ONCE and then mutated only by the model's diff events, never rebuilt per push, so
// nothing here can flap without new information (CLAUDE.md ## Design).
//
// Status colors keep their standing meanings (styles.css --st-*); the romp accent #9cd2ff
// is reserved for selection/hover chrome. All geometry is procedural — no runtime assets.
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { delegate } from "./actions";
import { hostPrefix } from "./host-prefix";
import { assignSlots, axialToXZ, frameDt, frameRadius, HEX_SIZE, hexCorner, hexDistance, latticeSegments, PAD_R, PAD_THETA, RIM_THETA, ringOf, slotOfAxial, spiralSlot, xzToAxial } from "./hive-layout";
import { buildSessions, diffSessions, HiveSession, HiveState, hiveAge, stateLine } from "./hive-model";

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
const PAD_H = 0.06;           // a hair of thickness so a lifted tile isn't paper; the board
                              // reads as ONE flat layer (HEX_SIZE/PAD_R: hive-layout.ts)
// The nameplate is written flat ON the tile, map-label style, sized to FIT ITS CELL — so a
// name can geometrically never leave its hex or pile onto a neighbour's (the user
// 2026-08-13, after the floating billboards stacked into fog). Fit math against the cell:
// across-flats width √3·HEX_SIZE ≈ 3.55 ≥ LABEL_W_MAX, and the plate's far edge
// LABEL_FRONT + LABEL_H/2 = 1.12 stays inside the apothem (≈ 1.78).
const LABEL_W_MAX = 2.7;
const LABEL_H = 0.4;
const LABEL_FRONT = 0.92;     // parked in the camera-side half of the cell, clear of the bean
// Tron world (the user 2026-08-13): near-black glossy ground with a faint accent grid, the
// pads dark slabs whose STATUS light is their glowing rim, bloom doing the neon work. The
// beans stay cute (session-colored, softly self-lit) with dark visors and glowing eyes.
const WORLD_BG = 0x090b10;

// exponential smoothing toward a target — frame-rate independent; `rate`/s is the snap speed
function ease(cur: number, target: number, dt: number, rate: number): number {
  return cur + (target - cur) * (1 - Math.exp(-rate * dt));
}

// one thin hex outline in the XZ plane (hexCorner — the corners the prism uses), for a
// LineLoop: THE line treatment of the board. Per-instance so Pad.dispose() can dispose it.
function hexLineGeo(r: number): THREE.BufferGeometry {
  const pos = new Float32Array(18);
  for (let k = 0; k < 6; k++) {
    const c = hexCorner(0, 0, r, k);
    pos[k * 3] = c.x; pos[k * 3 + 1] = 0; pos[k * 3 + 2] = c.z;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  return g;
}

// ── one session's pad: hex prism + status ring + label + its dweller ─────────────────────
class Pad {
  group = new THREE.Group();
  private padMesh: THREE.Mesh;
  private ring: THREE.LineLoop;
  private ringMat: THREE.LineBasicMaterial;
  private sonar: THREE.LineLoop;
  private sonarMat: THREE.LineBasicMaterial;
  private labelYaw = new THREE.Group();     // yaws to face the camera; the nameplate rides its +z
  private labelMesh: THREE.Mesh;
  private bang: THREE.Sprite;              // the ❗ that bobs over a needs-you pad
  private guy: Dweller;
  carrier = new THREE.Group();              // rides the pointer while the user carries them
  private carryTarget: THREE.Vector3 | null = null;
  private carryWant = new THREE.Vector3(); // scratch — no per-frame allocation
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
    // Tron slab: near-black glossy top with only a whisper of the identity color; the
    // pad's LIGHT is its rim. CylinderGeometry with 6 radial segments IS the hex prism;
    // PAD_THETA turns an edge (not a corner) toward each axial neighbour, so flush
    // neighbours meet edge-to-edge (the derivation lives with the constant).
    const top = new THREE.Color(0x0e1116).lerp(tint, 0.06);
    const side = new THREE.Color(0x080a0d).lerp(tint, 0.03);
    const geo = new THREE.CylinderGeometry(PAD_R, PAD_R * 1.04, PAD_H, 6, 1, false, PAD_THETA);
    this.padMesh = new THREE.Mesh(geo, [
      new THREE.MeshStandardMaterial({ color: side, roughness: 0.55, metalness: 0.35 }),
      new THREE.MeshStandardMaterial({ color: top, roughness: 0.3, metalness: 0.45 }),
      new THREE.MeshStandardMaterial({ color: side, roughness: 0.55, metalness: 0.35 }),
    ]);
    this.padMesh.position.y = PAD_H / 2;
    this.group.add(this.padMesh);

    // the status LIGHT: ONE thin neon line tracing the cell's EXACT boundary — the same
    // corners (hexCorner) and radius the lattice draws, so a used cell's walls ARE segments
    // of the shared web, connected to the empty cells and to its neighbours (the user
    // 2026-08-13, twice: connected, never floating inside its cell). Where two live
    // sessions share a wall the additive lines blend — that wall belongs to both. Bloom
    // does the glow; no halo, no thickness.
    this.ringMat = new THREE.LineBasicMaterial({
      color: ST[sess.state], transparent: true, opacity: 0.95,
      blending: THREE.AdditiveBlending, depthWrite: false,
    });
    this.ring = new THREE.LineLoop(hexLineGeo(PAD_R), this.ringMat);
    this.ring.position.y = PAD_H + 0.012;
    this.group.add(this.ring);

    // sonar ping: an expanding, fading copy of the line — the needs-you beacon (awaiting only)
    this.sonarMat = new THREE.LineBasicMaterial({
      color: ST.awaiting, transparent: true, opacity: 0,
      blending: THREE.AdditiveBlending, depthWrite: false,
    });
    this.sonar = new THREE.LineLoop(hexLineGeo(PAD_R), this.sonarMat);
    this.sonar.position.y = this.ring.position.y;
    this.group.add(this.sonar);

    this.labelMesh = makeNameDecal(sess.name, sess.sid, sess.color?.bg || "#cccccc");
    this.labelMesh.position.z = LABEL_FRONT;
    this.labelYaw.position.y = PAD_H + 0.02;
    this.labelYaw.add(this.labelMesh);
    this.group.add(this.labelYaw);

    this.bang = makeTextSprite("!", "#ffffff", "#c0392b");
    this.bang.position.y = 2.05;             // floats over the bean — the one deliberate float
    this.bang.visible = false;
    this.group.add(this.bang);

    this.guy = new Dweller(sess.color?.bg || "#9cd2ff");
    this.guy.group.position.y = PAD_H;
    // the CARRIER wraps the dweller: the bean's own animation moves guy.group, a user
    // drag moves the carrier — so picking them up never fights the idle animation
    this.carrier.add(this.guy.group);
    this.group.add(this.carrier);
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
      this.labelYaw.remove(this.labelMesh);
      disposeDecal(this.labelMesh);
      this.labelMesh = makeNameDecal(sess.name, sess.sid, sess.color?.bg || "#cccccc");
      this.labelMesh.position.z = LABEL_FRONT;
      this.labelYaw.add(this.labelMesh);
    }
  }

  hitMeshes(): THREE.Object3D[] { return [this.padMesh]; }
  beanMeshes(): THREE.Object3D[] { return [this.guy.hit]; }
  pokeBean() { this.guy.poke(); }

  // world-space carry target while the user drags them; null → spring home (update() eases)
  carryTo(p: THREE.Vector3 | null) { this.carryTarget = p ? p.clone() : null; }
  beanWorldPos(): THREE.Vector3 { return this.carrier.getWorldPosition(new THREE.Vector3()); }
  consumeBean() {
    this.guy.group.visible = false;
    this.carrier.position.set(0, 0, 0);
    this.carryTarget = null;
  }

  update(dt: number, camYaw: number, camDist: number, focus: boolean): boolean {
    this.t += dt;
    // map-label behavior: the plate faces the camera in yaw and FADES with altitude —
    // zoomed out, the board is shapes and colors; zoom in (or hover/select) to read names.
    // Never scaled up: an inflated label is how the old billboards piled into fog.
    this.labelYaw.rotation.y = camYaw;
    const lmat = this.labelMesh.material as THREE.MeshBasicMaterial;
    lmat.opacity = ease(lmat.opacity, focus ? 1 : Math.min(1, Math.max(0, (42 - camDist) / 12)), dt, 10);
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

    // carried: the bean rides the pointer (world target → pad-local); released, it
    // springs home — the same everything-springs rule as the rest of the board
    if (this.carryTarget) this.carryWant.copy(this.carryTarget).sub(this.group.position);
    else this.carryWant.set(0, 0, 0);
    this.carrier.position.lerp(this.carryWant, 1 - Math.exp(-16 * dt));
    const cs = this.carryTarget ? 1.12 : 1;
    this.carrier.scale.x = ease(this.carrier.scale.x, cs, dt, 12);
    this.carrier.scale.y = this.carrier.scale.z = this.carrier.scale.x;

    this.ringColor.lerp(this.ringTarget, 1 - Math.exp(-8 * dt));
    // a 1px line carries less light than the old fat rim, so overdrive the color past 1 —
    // that's what pushes it over the bloom threshold into neon
    this.ringMat.color.copy(this.ringColor).multiplyScalar(1.6);
    const st = this.sess.state;
    // pulse only the states that are genuinely in motion; steady states hold steady
    if (st === "working") this.ringMat.opacity = 0.62 + 0.3 * (0.5 + 0.5 * Math.sin(this.t * 3.6));
    else if (st === "awaiting") this.ringMat.opacity = 0.55 + 0.45 * (0.5 + 0.5 * Math.sin(this.t * 7));
    else if (st === "retrying") this.ringMat.opacity = 0.5 + 0.5 * (Math.sin(this.t * 11) > 0.2 ? 1 : 0.35);
    else this.ringMat.opacity = 0.95;

    if (st === "awaiting") {
      // sonar ping: 1.4s loop, ring swells to ~1.8× and fades — visible from any zoom
      const p = (this.t % 1.4) / 1.4;
      this.sonarMat.opacity = (1 - p) * 0.5;
      this.sonar.scale.setScalar(1 + p * 0.85);
      this.bang.visible = true;
      this.bang.position.y = 2.05 + 0.14 * Math.abs(Math.sin(this.t * 5));
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

// ── the bean (the user 2026-08-13: cute and blobby, Fall Guys / PEAK — not angular
//    fantasy). A lathe-profile bean in the session's identity color, cream face plate with
//    big dot eyes, stubby nub arms, squash-and-stretch on every landing. The TORSO group
//    pivots at the feet so leans read as body language, not sliding. ────────────────────────
class Dweller {
  group = new THREE.Group();
  private torso = new THREE.Group();        // feet-pivot: body + face + arms live here
  private body: THREE.Mesh;
  private bodyMat: THREE.MeshStandardMaterial;
  private face: THREE.Mesh;
  private eyeL: THREE.Mesh; private eyeR: THREE.Mesh;
  private armL: THREE.Mesh; private armR: THREE.Mesh;
  hit!: THREE.Mesh;                         // invisible click body — see the ctor
  private aura: THREE.Mesh;                 // compacting swirl / awaitingBg marker, recolored per state
  private auraMat: THREE.MeshBasicMaterial;
  private orb!: THREE.Mesh;                 // awaitingBg: spinning gem overhead
  private orbMat!: THREE.MeshBasicMaterial;
  private desk: THREE.Group;                // tiny desk + glowing laptop — the "working" silhouette
  private screenMat: THREE.MeshStandardMaterial;
  private state: HiveState = "ready";
  private faded = false;
  private blinkAt = 2 + Math.random() * 4;
  private blinkT = -1;
  private phase = Math.random() * Math.PI * 2;
  private baseColor: THREE.Color;
  private pop = 0;                          // hatch flourish clock (opening → live)
  private squash = 1;                       // landing squash factor, springs back to 1
  private prevY = 0;

  constructor(tint: string) {
    this.baseColor = new THREE.Color(tint).lerp(new THREE.Color(0xffffff), 0.1);
    // the suit self-lights a little (Tron creature), so beans stay cute against the dark
    this.bodyMat = new THREE.MeshStandardMaterial({
      color: this.baseColor.clone(), roughness: 0.45, metalness: 0.1,
      emissive: this.baseColor.clone(), emissiveIntensity: 0.22,
    });
    // the bean silhouette: chubby low waist, rounded shoulders, narrow rounded crown
    const prof: [number, number][] = [
      [0.001, 0], [0.30, 0.03], [0.42, 0.14], [0.475, 0.32], [0.48, 0.52],
      [0.44, 0.72], [0.375, 0.90], [0.29, 1.04], [0.18, 1.13], [0.001, 1.17],
    ];
    this.body = new THREE.Mesh(
      new THREE.LatheGeometry(prof.map(([r, y]) => new THREE.Vector2(r, y)), 26),
      this.bodyMat,
    );
    this.torso.add(this.body);
    // dark glossy visor sunk into the front (the Fall Guys face zone, program-grade),
    // with two GLOWING eyes inside it — bloom turns them into the character's light
    this.face = new THREE.Mesh(
      new THREE.SphereGeometry(0.27, 18, 14),
      new THREE.MeshStandardMaterial({ color: 0x0b0d12, roughness: 0.15, metalness: 0.6 }),
    );
    this.face.scale.set(1, 1.28, 0.45);
    this.face.position.set(0, 0.78, 0.29);
    this.torso.add(this.face);
    const mkEye = () => new THREE.Mesh(
      new THREE.SphereGeometry(0.05, 10, 10),
      new THREE.MeshBasicMaterial({ color: 0xdff1ff }),
    );
    this.eyeL = mkEye(); this.eyeR = mkEye();
    this.eyeL.position.set(-0.095, 0.83, 0.405);
    this.eyeR.position.set(0.095, 0.83, 0.405);
    this.torso.add(this.eyeL, this.eyeR);
    const armGeo = new THREE.CapsuleGeometry(0.085, 0.2, 4, 10);
    armGeo.translate(0, -0.12, 0);          // hang from the shoulder joint, so rotation swings
    this.armL = new THREE.Mesh(armGeo, this.bodyMat);
    this.armR = new THREE.Mesh(armGeo, this.bodyMat);
    this.armL.position.set(-0.44, 0.72, 0.05);
    this.armR.position.set(0.44, 0.72, 0.05);
    this.armL.rotation.z = 0.35; this.armR.rotation.z = -0.35;
    this.torso.add(this.armL, this.armR);
    this.group.add(this.torso);

    // the bean's HIT BODY: an invisible capsule over the whole character (arms, face, all
    // of them), so clicking THEM is easy — colorWrite:false draws nothing but still
    // raycasts. Clicking the bean is the direct line to their chat (the user 2026-08-13).
    this.hit = new THREE.Mesh(
      new THREE.CapsuleGeometry(0.55, 0.55, 4, 8),
      new THREE.MeshBasicMaterial({ colorWrite: false, depthWrite: false }),
    );
    this.hit.position.y = 0.62;
    this.group.add(this.hit);

    this.auraMat = new THREE.MeshBasicMaterial({ color: ST.compacting, transparent: true, opacity: 0 });
    this.aura = new THREE.Mesh(new THREE.TorusGeometry(0.58, 0.035, 6, 24), this.auraMat);
    this.aura.position.y = 0.66;
    this.aura.rotation.x = Math.PI / 2.4;
    this.group.add(this.aura);
    // the awaitingBg marker: a little hourglass-ish gem spinning overhead — "my work is
    // out there running" — green like its status
    this.orbMat = new THREE.MeshBasicMaterial({ color: ST.awaitingBg, transparent: true, opacity: 0 });
    this.orb = new THREE.Mesh(new THREE.OctahedronGeometry(0.1), this.orbMat);
    this.orb.position.y = 1.55;
    this.group.add(this.orb);

    // the desk: tabletop + laptop with an emissive screen, parked in front of the bean;
    // shown only while working (the strongest one-glance "busy" silhouette there is)
    this.desk = new THREE.Group();
    const slab = new THREE.MeshStandardMaterial({ color: 0x141920, roughness: 0.35, metalness: 0.5 });
    const top = new THREE.Mesh(new THREE.BoxGeometry(0.78, 0.05, 0.42), slab);
    top.position.y = 0.5;
    this.desk.add(top);
    // a hairline of accent light along the desk's front edge — the Tron detail line
    const edge = new THREE.Mesh(
      new THREE.BoxGeometry(0.78, 0.012, 0.012),
      new THREE.MeshBasicMaterial({ color: ACCENT, transparent: true, opacity: 0.8, blending: THREE.AdditiveBlending, depthWrite: false }),
    );
    edge.position.set(0, 0.527, 0.21);
    this.desk.add(edge);
    for (const [lx, lz] of [[-0.34, -0.16], [0.34, -0.16], [-0.34, 0.16], [0.34, 0.16]]) {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.5, 0.05), slab);
      leg.position.set(lx, 0.25, lz);
      this.desk.add(leg);
    }
    const shell = new THREE.MeshStandardMaterial({ color: 0x3a3d42, roughness: 0.4, metalness: 0.3 });
    const base = new THREE.Mesh(new THREE.BoxGeometry(0.34, 0.02, 0.24), shell);
    base.position.set(0, 0.535, 0.02);
    this.screenMat = new THREE.MeshStandardMaterial({
      color: 0x10151c, roughness: 0.3, emissive: 0x9fd8ff, emissiveIntensity: 0.9,
    });
    const screen = new THREE.Mesh(new THREE.BoxGeometry(0.34, 0.24, 0.015), this.screenMat);
    screen.position.set(0, 0.65, 0.13);
    screen.rotation.x = -0.22;
    this.desk.add(base, screen);
    this.desk.position.set(0, 0, 0.58);
    this.desk.rotation.y = Math.PI;         // screen faces the bean
    this.desk.visible = false;
    this.group.add(this.desk);
  }

  setState(s: HiveState, faded: boolean) {
    if (this.state === "opening" && s !== "opening") {
      this.pop = 0.45;                       // hatched! — one pop, then normal life
      this.bodyMat.color.copy(this.baseColor);
    }
    if (s !== this.state) this.squash = 1.18;   // every real transition lands with a squash beat
    this.state = s; this.faded = faded;
  }

  // a click landed on them — the hatch pop doubles as the immediate acknowledgement
  poke() {
    this.pop = Math.max(this.pop, 0.45);
  }

  update(dt: number, t: number, camYaw: number) {
    const s = this.state;
    // blink (life for every state except the egg)
    if (s !== "opening") {
      this.blinkAt -= dt;
      if (this.blinkAt <= 0) { this.blinkT = 0.12; this.blinkAt = 3 + Math.random() * 4; }
      if (this.blinkT > 0) this.blinkT -= dt;
      const bl = this.blinkT > 0 ? 0.12 : 1;
      this.eyeL.scale.set(1, bl, 1); this.eyeR.scale.set(1, bl, 1);
    }

    let y = 0, rotX = 0, rotZ = 0, yaw = 0, sx = 0;
    let aura = 0, desk = false;
    // resting arm pose; states override
    let armLZ = 0.35, armRZ = -0.35, armLX = 0, armRX = 0;
    switch (s) {
      case "working": {
        desk = true;
        rotX = 0.1;
        this.bodyMat.emissiveIntensity = 0.3;   // the screen lights the bean up a touch
        // hands over the keys, tapping in bursts; the screen glow flickers with the keys
        const burst = Math.sin(t * 2.8 + this.phase) > -0.35;
        armLX = -1.15 + (burst ? 0.18 * Math.sin(t * 13) : 0);
        armRX = -1.15 + (burst ? 0.18 * Math.sin(t * 13 + Math.PI) : 0);
        armLZ = 0.12; armRZ = -0.12;
        this.screenMat.emissiveIntensity = 0.75 + (burst ? 0.3 * Math.abs(Math.sin(t * 9)) : 0.1);
        break;
      }
      case "awaiting": {                     // they need YOU: face the camera, big both-arms wave
        yaw = camYaw;
        y = 0.22 * Math.abs(Math.sin(t * 4.6));
        armLZ = 2.5 + 0.4 * Math.sin(t * 9);
        armRZ = -2.5 - 0.4 * Math.sin(t * 9 + 1);
        break;
      }
      case "blocked":
        desk = true;                          // the wreck stays on the desk, screen dead, smoking
        this.screenMat.emissiveIntensity = 0.04;
        rotX = 0.55; y = -0.05;              // folded forward over it, arms hanging dead
        armLZ = 0.05; armRZ = -0.05; armLX = -0.4; armRX = -0.4;
        break;
      case "retrying":
        sx = 0.34 * Math.sin(t * 1.6);       // pacing the pad, arms swinging with the waddle
        yaw = Math.cos(t * 1.6) > 0 ? Math.PI / 2 : -Math.PI / 2;
        rotZ = 0.08 * Math.sin(t * 7);
        armLX = 0.5 * Math.sin(t * 7); armRX = -0.5 * Math.sin(t * 7);
        break;
      case "awaitingBg":
        rotX = -0.14;                        // leaning back, watching its dispatched work spin
        armLZ = 0.9; armRZ = -0.9;
        this.orbMat.opacity = 0.9;
        this.orb.rotation.y = t * 2.2; this.orb.rotation.x = 0.5;
        this.orb.position.y = 1.55 + 0.08 * Math.sin(t * 2.6);
        break;
      case "compacting": case "clearing":
        y = 0.18 + 0.05 * Math.sin(t * 2.4); // levitating meditation, teal swirl orbiting
        yaw = t * 0.8;
        armLZ = 1.5; armRZ = -1.5;           // arms out, zen
        aura = 0.75; this.auraMat.color.setHex(ST.compacting);
        this.aura.rotation.z = t * 2.2;
        break;
      case "interrupting":
        this.squash = Math.max(this.squash, 1.12);   // freeze-frame squash; no motion at all
        break;
      case "opening":
        // the egg: eyes hidden, face hidden, wobbling toward the hatch
        this.eyeL.scale.setScalar(0.001); this.eyeR.scale.setScalar(0.001);
        this.face.visible = false; this.armL.visible = false; this.armR.visible = false;
        this.bodyMat.color.lerp(new THREE.Color(0xf2ead9), 0.2);
        rotZ = 0.09 * Math.sin(t * 9);
        break;
      default:                               // ready
        if (this.faded) { rotX = -0.32; y = -0.06; armLZ = 0.1; armRZ = -0.1; }   // dozing
        else {
          rotZ = 0.03 * Math.sin(t * 1.3 + this.phase);
          armLZ = 0.35 + 0.06 * Math.sin(t * 1.3 + this.phase);
          armRZ = -0.35 - 0.06 * Math.sin(t * 1.3 + this.phase);
        }
    }
    if (s !== "opening") { this.face.visible = true; this.armL.visible = true; this.armR.visible = true; }
    if (s !== "working") this.bodyMat.emissiveIntensity = 0.22;
    if (s !== "awaitingBg") this.orbMat.opacity = ease(this.orbMat.opacity, 0, dt, 8);
    this.desk.visible = desk;

    // squash & stretch: landings compress the bean, air time stretches it — scale about the
    // feet (the lathe sits on y=0, so plain scale already pivots there), volume-ish preserved
    const vy = (y - this.prevY) / Math.max(dt, 1e-4);
    this.prevY = y;
    if (y < 0.02 && vy < -0.6) this.squash = Math.max(this.squash, 1.22);
    this.squash = ease(this.squash, 1, dt, 9);
    const airStretch = Math.min(0.12, Math.max(0, vy * 0.03));
    const syn = (1 / this.squash) + airStretch;
    const breathe = 1 + 0.018 * Math.sin(t * 2.1 + this.phase);
    let bs = syn * breathe;
    if (this.pop > 0) {
      this.pop = Math.max(0, this.pop - dt);
      const p = this.pop / 0.45;             // 1→0: a quick overshoot pulse on the whole body
      bs *= 1 + 0.3 * Math.sin(p * Math.PI);
    }
    this.torso.scale.set(this.squash * (2 - breathe), bs, this.squash * (2 - breathe));

    this.armL.rotation.set(armLX, 0, armLZ);
    this.armR.rotation.set(armRX, 0, armRZ);
    this.group.position.x = sx;
    this.group.position.y = PAD_H + y;
    this.group.rotation.y = yaw;
    this.torso.rotation.x = rotX;
    this.torso.rotation.z = rotZ;
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
    ctx2.shadowColor = color; ctx2.shadowBlur = 14;   // the name IS a neon sign — its own glow
  }
  ctx2.fillStyle = color;
  ctx2.fillText(text, c.width / 2, bubble ? 40 : c.height / 2, c.width - 16);
  if (!bubble) ctx2.fillText(text, c.width / 2, c.height / 2, c.width - 16);   // double-strike brightens the core over the glow
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
  sp.scale.set(c.width / 72, c.height / 72, 1);   // readable from the overview orbit
  return sp;
}
function disposeSprite(s: THREE.Sprite) { s.material.map?.dispose(); s.material.dispose(); }

// A cell's nameplate: written flat ON the tile, map-label style (how Civ-like grids and map
// engines keep names from piling up — a label that belongs to its cell can never leave it).
// Crisp and glow-free: two-tone like every other surface (quiet "host:" prefix + identity
// color), the color dimmed to sit UNDER the bloom threshold so text never fuzzes. Long
// names ellipsize to the cell's width. The caller yaws it to the camera and fades it by
// zoom (Pad.update) — a map fades labels out at altitude, it never inflates them.
function makeNameDecal(name: string, sid: string, color: string): THREE.Mesh {
  const p = hostPrefix(name, sid);
  const host = p ? p.host : "";
  let rest = p ? p.rest : name;
  const c = document.createElement("canvas");
  const g = c.getContext("2d")!;
  const F = 46;
  const hostFont = `italic 400 ${Math.round(F * 0.88)}px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`;
  const nameFont = `600 ${F}px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`;
  const px = (s: string, f: string) => { g.font = f; return g.measureText(s).width; };
  const budget = (LABEL_W_MAX / LABEL_H) * (F * 1.35) - 24;   // px that fit at the fixed world height
  const hostPx = host ? px(host, hostFont) : 0;
  let cut = false;
  while (rest.length > 2 && hostPx + px(rest, nameFont) + (cut ? px("…", nameFont) : 0) > budget) {
    rest = rest.slice(0, -1);
    cut = true;
  }
  if (cut) rest += "…";
  c.width = Math.ceil(hostPx + px(rest, nameFont)) + 24;
  c.height = Math.round(F * 1.35);
  const g2 = c.getContext("2d")!;                 // resizing reset the context state above
  g2.textBaseline = "middle";
  const dim = new THREE.Color(color || "#cccccc").multiplyScalar(0.82);
  let x = 12;
  if (host) {
    g2.font = hostFont;
    g2.fillStyle = "#8a8a8a";
    g2.fillText(host, x, c.height / 2);
    x += hostPx;
  }
  g2.font = nameFont;
  g2.fillStyle = "#" + dim.getHexString();
  g2.fillText(rest, x, c.height / 2);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;                             // flat text at a glancing pitch smears without it
  const mesh = new THREE.Mesh(
    new THREE.PlaneGeometry(LABEL_H * (c.width / c.height), LABEL_H),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0, depthWrite: false }),
  );
  mesh.rotation.x = -Math.PI / 2;                 // lie flat; text-top points away from the viewer
  return mesh;
}
function disposeDecal(m: THREE.Mesh) {
  m.geometry.dispose();
  const mat = m.material as THREE.MeshBasicMaterial;
  mat.map?.dispose();
  mat.dispose();
}

// ── confetti / puffs: one pooled particle system for every burst ─────────────────────────
class Particles {
  points: THREE.Points;
  private geo = new THREE.BufferGeometry();
  private max = 600;
  private pos = new Float32Array(this.max * 3);
  private col = new Float32Array(this.max * 3);
  private vel: Float32Array = new Float32Array(this.max * 3);
  private life = new Float32Array(this.max);
  private grav = new Float32Array(this.max);   // per-particle: confetti falls, smoke rises
  private n = 0;

  constructor() {
    this.geo.setAttribute("position", new THREE.BufferAttribute(this.pos, 3));
    this.geo.setAttribute("color", new THREE.BufferAttribute(this.col, 3));
    this.points = new THREE.Points(this.geo, new THREE.PointsMaterial({
      size: 0.14, vertexColors: true, transparent: true, opacity: 0.95, depthWrite: false,
      blending: THREE.AdditiveBlending,      // sparks of light, not paper — they bloom
    }));
    this.points.frustumCulled = false;
    this.geo.setDrawRange(0, 0);
  }

  burst(at: THREE.Vector3, colors: number[], count: number, speed: number, gravity = -7.5, life = 1.1) {
    for (let i = 0; i < count && this.n < this.max; i++, this.n++) {
      const j = this.n * 3;
      // born spread over a small shell, not one point — a point of 50 additive sprites
      // reads as a white flashbulb on frame one, a shell reads as a firework
      const sh = 0.28;
      this.pos[j] = at.x + (Math.random() - 0.5) * sh * 2;
      this.pos[j + 1] = at.y + (Math.random() - 0.5) * sh;
      this.pos[j + 2] = at.z + (Math.random() - 0.5) * sh * 2;
      const th = Math.random() * Math.PI * 2, up = 0.5 + Math.random() * 0.9;
      this.vel[j] = Math.cos(th) * speed * (0.4 + Math.random() * 0.6);
      this.vel[j + 1] = up * speed;
      this.vel[j + 2] = Math.sin(th) * speed * (0.4 + Math.random() * 0.6);
      const c = new THREE.Color(colors[i % colors.length]);
      this.col[j] = c.r; this.col[j + 1] = c.g; this.col[j + 2] = c.b;
      this.life[this.n] = life + Math.random() * 0.5;
      this.grav[this.n] = gravity;
    }
  }

  update(dt: number) {
    let w = 0;
    for (let i = 0; i < this.n; i++) {
      this.life[i] -= dt;
      if (this.life[i] <= 0) continue;
      const j = i * 3, k = w * 3;
      this.vel[j + 1] += this.grav[i] * dt;
      this.pos[k] = this.pos[j] + this.vel[j] * dt;
      this.pos[k + 1] = this.pos[j + 1] + this.vel[j + 1] * dt;
      this.pos[k + 2] = this.pos[j + 2] + this.vel[j + 2] * dt;
      if (w !== i) {
        this.vel[k] = this.vel[j]; this.vel[k + 1] = this.vel[j + 1]; this.vel[k + 2] = this.vel[j + 2];
        this.col[k] = this.col[j]; this.col[k + 1] = this.col[j + 1]; this.col[k + 2] = this.col[j + 2];
        this.life[w] = this.life[i]; this.grav[w] = this.grav[i];
      }
      w++;
    }
    this.n = w;
    this.geo.setDrawRange(0, this.n);
    (this.geo.attributes.position as THREE.BufferAttribute).needsUpdate = true;
    (this.geo.attributes.color as THREE.BufferAttribute).needsUpdate = true;
  }
}

// ── the fly-in card: the session's gist + a talk composer (plans/hive.md) ────────────────
// DOM, not WebGL — readable text belongs to the page. Created ONCE and updated in place
// (never rebuilt per push), actions delegated to the stable card root (./actions), so
// clicks are click-safe by construction and every press flashes.
class HiveCard {
  el: HTMLElement;
  private dot: HTMLElement; private name: HTMLElement; private state: HTMLElement;
  private goal: HTMLElement; private brief: HTMLElement; private err: HTMLElement;
  private input: HTMLTextAreaElement; private sendBtn: HTMLButtonElement;
  sid: string | null = null;
  private curName = "";                        // latest display name (refresh keeps it current)
  private renaming = false;
  private endEdit: ((commit: boolean) => void) | null = null;   // cancel handle for a live editor
  onSend: (sid: string, text: string) => void = () => {};
  onOpen: (sid: string) => void = () => {};
  onClose: () => void = () => {};
  onRename: (sid: string, name: string) => void = () => {};

  constructor() {
    const el = document.createElement("div");
    el.id = "hive-card";
    el.innerHTML =
      '<div class="hc-head"><span class="hc-dot"></span>' +
      '<span class="hc-name" data-act="rename" title="Rename"></span>' +
      '<button class="hc-x" data-act="close" title="Back to the board (Esc)" aria-label="Close">×</button></div>' +
      '<div class="hc-state"></div>' +
      '<div class="hc-goal" hidden></div>' +
      '<div class="hc-brief" hidden></div>' +
      '<div class="hc-err" hidden></div>' +
      '<div class="hc-talk"><textarea class="hc-input" rows="2"></textarea>' +
      '<button class="hc-send" data-act="send">Send</button></div>' +
      '<div class="hc-foot"><button class="hc-open" data-act="open">Open session ↗</button></div>';
    document.body.appendChild(el);
    this.el = el;
    this.dot = el.querySelector(".hc-dot") as HTMLElement;
    this.name = el.querySelector(".hc-name") as HTMLElement;
    this.state = el.querySelector(".hc-state") as HTMLElement;
    this.goal = el.querySelector(".hc-goal") as HTMLElement;
    this.brief = el.querySelector(".hc-brief") as HTMLElement;
    this.err = el.querySelector(".hc-err") as HTMLElement;
    this.input = el.querySelector(".hc-input") as HTMLTextAreaElement;
    this.sendBtn = el.querySelector(".hc-send") as HTMLButtonElement;
    delegate(el, {
      close: () => this.onClose(),
      open: () => { if (this.sid) this.onOpen(this.sid); },
      send: () => this.send(),
      rename: () => this.startRename(),
    });
    this.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this.send(); }
      e.stopPropagation();                   // typing must never orbit the camera / close the card
    });
  }

  private send() {
    const sid = this.sid, text = this.input.value.trim();
    if (!sid || !text) return;
    this.onSend(sid, text);
    // acknowledge NOW, before any kernel round-trip: the composer clears, the button says
    // so, and the world fires the send-puff at the hex (the delivery itself is the kernel's
    // normal park/forward — a refusal comes back as an err and lands in hc-err below)
    this.input.value = "";
    this.err.hidden = true;
    const b = this.sendBtn;
    b.disabled = true; b.textContent = "Sent ✓";
    setTimeout(() => { b.disabled = false; b.textContent = "Send"; }, 1100);
  }

  // Click the name to rename — the same inline editor contract as the chat tab strip
  // (render.ts startTabRename): a remote session displays as "host:name" where "host:" is
  // metadata THIS viewer prepends (host-prefix.ts) — the kernel that owns the session knows
  // it by the bare name, so the host renders as fixed chrome beside the field and only the
  // name is editable. Enter/blur commits, Esc cancels; the label only changes once the
  // kernel's push lands with the new name (never optimistically — the push is the truth).
  private startRename() {
    if (!this.sid || this.renaming) return;
    const sid = this.sid;                      // commit to the session the editor OPENED on —
    const p = hostPrefix(this.curName, sid);   // a blur can land after the card switched sids
    const base = p ? p.rest : this.curName;
    const input = document.createElement("input");
    input.className = "hc-rename";
    input.value = base;
    input.spellcheck = false;
    const fixed = p ? document.createElement("span") : null;
    if (fixed) { fixed.className = "host-prefix"; fixed.textContent = p!.host; }
    this.renaming = true;
    this.name.style.display = "none";
    this.name.after(input);
    if (fixed) input.before(fixed);
    let finished = false;
    const done = (commit: boolean) => {
      if (finished) return;
      finished = true;
      this.endEdit = null;
      const v = input.value.trim();
      input.remove();
      fixed?.remove();
      this.name.style.display = "";
      this.renaming = false;
      // the bare name, never the display string: the owning kernel is addressed by sid
      if (commit && v && v !== base) this.onRename(sid, v);
    };
    this.endEdit = done;
    input.addEventListener("keydown", (e) => {
      e.stopPropagation();                     // typing must never orbit the camera / close the card
      if (e.key === "Enter") { e.preventDefault(); done(true); }
      else if (e.key === "Escape") { e.preventDefault(); done(false); }
    });
    input.addEventListener("blur", () => done(true));
    for (const ev of ["click", "mousedown", "dblclick", "contextmenu"])
      input.addEventListener(ev, (e) => e.stopPropagation());
    input.focus();
    input.select();
  }

  show(s: HiveSession, now: number) {
    const fresh = this.sid !== s.sid;
    if (fresh) this.endEdit?.(false);          // switching sessions abandons a half-typed rename
    this.sid = s.sid;
    this.refresh(s, now);
    if (fresh) { this.err.hidden = true; this.input.value = ""; }
    this.el.classList.add("open");
    this.input.placeholder = "Say something to " + s.name + "…";
    if (fresh) this.input.focus();
  }

  refresh(s: HiveSession, now: number) {
    if (this.sid !== s.sid) return;
    this.curName = s.name;
    this.dot.style.background = s.color?.bg || "#8a8a8a";
    this.name.textContent = s.name;
    this.name.style.color = s.color?.bg || "#dddddd";
    this.name.dataset.act = "rename";          // a live session is renameable (gone() revokes)
    this.state.textContent = stateLine(s, now);
    this.state.dataset.state = s.state;
    this.goal.hidden = !s.goal;
    if (s.goal) this.goal.textContent = s.goal;
    const needsYou = s.state === "awaiting" || s.state === "blocked";
    this.brief.hidden = !(s.brief && needsYou);
    if (s.brief && needsYou) this.brief.textContent = s.brief;
  }

  gone() {
    // the selected session left the board — say so rather than silently going stale, and
    // stop offering rename: there is nothing behind the sid for the kernel to rename
    this.state.textContent = "this session has ended";
    this.state.dataset.state = "";
    this.endEdit?.(false);
    delete this.name.dataset.act;
  }

  error(title: string, text: string) {
    this.err.hidden = false;
    this.err.textContent = title + (text ? " — " + text : "");
  }

  hide() { this.endEdit?.(false); this.sid = null; this.el.classList.remove("open"); }
}

// ── the world ────────────────────────────────────────────────────────────────────────────
class HiveWorld {
  private renderer: THREE.WebGLRenderer;
  private composer!: EffectComposer;
  private bloom!: UnrealBloomPass;
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
  card = new HiveCard();
  // the ghost hex: the standing invitation. It parks on the first FREE slot, and GLIDES
  // under the pointer whenever any empty cell is hovered — every empty hexagon is
  // clickable to spawn a session there (the user 2026-08-13). A clean click (down+up,
  // no drag) recruits: it reserves the cell and opens the new-session picker.
  private ghost = new THREE.Group();
  private ghostTarget = new THREE.Vector3();   // eased toward in frame() — the ghost glides
  private ghostSlot = 0;                       // the cell the ghost is offering right now
  private ghostHome = 0;                       // first-free slot — where it parks at rest
  private reservedSlot: number | null = null;  // cell claimed by a click, honored in sync()
  private pendingRecruit: { x: number; y: number; slot: number } | null = null;
  // drag-to-end (the user 2026-08-13, TFT-style): a held press on a session that MOVES
  // picks the bean up; it rides the pointer, the trash dock slides in, and dropping on
  // the armed dock ends the session — the deliberate carry + highlighted dock IS the
  // confirmation (and an ended session still revives with its history from the picker).
  private pressedPad: { sid: string; x: number; y: number; bean: boolean } | null = null;
  private dragSession: { sid: string; depth: number; over: boolean } | null = null;
  private trashEl: HTMLElement;
  private ghostRingMat = new THREE.LineBasicMaterial({
    color: ACCENT, transparent: true, opacity: 0.14, blending: THREE.AdditiveBlending, depthWrite: false,
  });
  private ghostFill: THREE.Mesh;
  private ghostPlus: THREE.Sprite;
  private ghostHover = false;
  // the one-layer board: every cell of the honeycomb as thin faint lines — EMPTY cells are
  // this lattice, USED cells are the pads docked flush into it. Rebuilt only when the
  // needed ring count changes (an arrival/departure at the frontier), never per push.
  private lattice: THREE.LineSegments | null = null;
  private latticeRings = -1;

  constructor(private root: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setClearColor(WORLD_BG);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    root.appendChild(this.renderer.domElement);
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.1, 400);
    this.scene.fog = new THREE.FogExp2(WORLD_BG, 0.013);
    // cool, dim, mostly-emissive lighting — the neon does the work; the beans carry a
    // touch of self-light so they stay cute against the dark
    this.scene.add(new THREE.HemisphereLight(0x3b4a63, 0x0a0c10, 1.0));
    const key = new THREE.DirectionalLight(0xcfe0ff, 1.0);
    key.position.set(7, 12, 5);
    this.scene.add(key);
    const rim = new THREE.DirectionalLight(ACCENT, 0.7);
    rim.position.set(-6, 6, -8);
    this.scene.add(rim);
    this.scene.add(this.particles.points);

    // the floor: a dark matte disc fading into the fog. The only pattern on it is the hex
    // LATTICE (ensureLattice) — the square grid is gone: two grids at once fought the
    // tessellation instead of underlining it (the user 2026-08-13)
    const floor = new THREE.Mesh(
      new THREE.CircleGeometry(150, 64),
      // matte enough that the key light can't smear a bloom highlight across the floor
      new THREE.MeshStandardMaterial({ color: 0x0a0c10, roughness: 0.72, metalness: 0.3 }),
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -0.02;
    this.scene.add(floor);

    // bloom is the neon: everything over the threshold (the overdriven status lines, eyes,
    // screens) halos out; the dark tiles, lattice and floor stay dark
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.7, 0.45, 0.72);
    this.composer.addPass(this.bloom);

    const cv = this.renderer.domElement;
    cv.addEventListener("pointermove", (e) => this.onPointerMove(e));
    cv.addEventListener("pointerdown", (e) => this.onPointerDown(e));
    window.addEventListener("pointerup", (e) => this.onPointerUp(e));
    cv.addEventListener("wheel", (e) => {
      e.preventDefault();
      this.dist = Math.min(70, Math.max(7, this.dist * Math.exp(e.deltaY * 0.0012)));
      this.idleT = 0;
    }, { passive: false });
    cv.addEventListener("dblclick", () => {
      const sid = this.hovered;
      if (sid && sid !== HiveWorld.GHOST) this.openChat(sid);
    });
    window.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (this.dragSession) { this.dropSessionDrag(false); return; }   // Esc aborts a carry
      this.deselect();
    });

    // the trash dock: hidden below the bottom edge, slides in only while a session is
    // being carried; pointer-events none — the canvas keeps the pointer, we rect-test
    this.trashEl = document.createElement("div");
    this.trashEl.id = "hive-trash";
    this.trashEl.innerHTML =
      '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">' +
      '<path fill="currentColor" d="M9 3v1H4v2h16V4h-5V3H9zM6 8v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V8H6zm3 2h2v10H9V10zm4 0h2v10h-2V10z"/></svg>' +
      '<span class="ht-label">Drop to end</span>';
    document.body.appendChild(this.trashEl);

    const fit = () => {
      const w = root.clientWidth || 1, h = root.clientHeight || 1;
      this.renderer.setSize(w, h, false);
      this.composer.setSize(w, h);
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

    // deliberately QUIETER than any real pad: smaller, hairline ring, near-invisible fill —
    // an invitation at the spiral's frontier, not a resident
    // the ghost lights its whole cell like a resident would, just barely: the full boundary
    // line (connected to the web like every other cell) + a whisper of fill
    const ghostRing = new THREE.LineLoop(hexLineGeo(PAD_R), this.ghostRingMat);
    ghostRing.position.y = 0.03;
    this.ghostFill = new THREE.Mesh(
      new THREE.CircleGeometry(PAD_R, 6, RIM_THETA),
      new THREE.MeshBasicMaterial({ color: ACCENT, transparent: true, opacity: 0.012, depthWrite: false }));
    this.ghostFill.rotation.x = -Math.PI / 2;
    this.ghostFill.position.y = 0.02;
    this.ghostPlus = makeTextSprite("+", "#9cd2ff");
    this.ghostPlus.position.y = 0.6;
    this.ghostPlus.scale.multiplyScalar(0.7);
    this.ghostPlus.material.opacity = 0.25;
    this.ghost.add(ghostRing, this.ghostFill, this.ghostPlus);
    this.scene.add(this.ghost);

    this.card.onClose = () => this.deselect();
    this.card.onOpen = (sid) => this.openChat(sid);
    this.card.onRename = (sid, name) => {
      // the SAME renameSession op the chat tab strip posts — the kernel renames the tmux
      // session (or the names file for a dead one) and the new name rides the next push
      vscodeApi?.postMessage({ type: "renameSession", id: sid, name });
    };
    this.card.onSend = (sid, text) => {
      vscodeApi?.postMessage({ type: "sendMessage", id: sid, text });
      const pad = this.pads.get(sid);
      if (pad) {
        // the visible delivery: a little accent spark shower over their hex
        const at = pad.group.position.clone().setY(PAD_H + 1.6);
        this.particles.burst(at, [ACCENT, 0xd6ecff, 0x6fb7ff], 14, 1.6);
      }
    };
    (window as any).__hive = this;           // debug handle (harness + console poking)
  }

  private canvasPoint(e: PointerEvent) {
    const r = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  }

  private onPointerMove(e: PointerEvent) {
    this.canvasPoint(e);
    // a held press on a session that moves becomes a PICK-UP; the gate is the gesture
    // (px travelled), the same 6px the recruit click uses
    if (this.pressedPad && !this.dragSession
        && Math.hypot(e.clientX - this.pressedPad.x, e.clientY - this.pressedPad.y) > 6) {
      this.beginSessionDrag(this.pressedPad.sid);
    }
    if (this.dragSession) { this.moveSessionDrag(e); return; }   // a carry never orbits
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
    const hit = this.pick();
    const sid = hit.sid;
    if (e.button === 2 || e.shiftKey) { this.dragging = { mode: "pan", x: e.clientX, y: e.clientY }; return; }
    if (sid === HiveWorld.GHOST) {
      // recruit is decided on the UP (a clean click, not a drag) so the whole empty board
      // stays grabbable for orbiting — the down arms the recruit AND starts the orbit
      this.pendingRecruit = { x: e.clientX, y: e.clientY, slot: this.ghostSlot };
      this.dragging = { mode: "orbit", x: e.clientX, y: e.clientY };
      return;
    }
    if (sid) {
      // acknowledge the press NOW (the pad dips); the ACTION fires on the clean UP —
      // a press that MOVES becomes a pick-up instead (drag to the trash dock to end)
      const pad = this.pads.get(sid);
      if (pad) { pad.lift = -0.07; setTimeout(() => { if (this.pads.get(sid) === pad) pad.lift = this.hovered === sid ? 0.12 : 0; }, 130); }
      this.pressedPad = { sid, x: e.clientX, y: e.clientY, bean: hit.bean };
    } else {
      this.dragging = { mode: "orbit", x: e.clientX, y: e.clientY };
    }
  }

  private onPointerUp(e: PointerEvent) {
    const pr = this.pendingRecruit;
    const pp = this.pressedPad;
    this.pendingRecruit = null;
    this.pressedPad = null;
    this.dragging = null;
    if (this.dragSession) { this.dropSessionDrag(this.dragSession.over); return; }
    if (pp) {
      if (Math.hypot(e.clientX - pp.x, e.clientY - pp.y) > 5) return;   // it was a nudge, not a click
      const pad = this.pads.get(pp.sid);
      // clicking the BEAN is the direct line to their chat: they pop (acknowledgement on
      // THEM) and the chat pane opens; clicking the TILE opens the fly-in card
      if (pp.bean && pad) {
        pad.pokeBean();
        this.openChat(pp.sid);
      } else if (pad) {
        this.select(pp.sid);
      }
      return;
    }
    if (!pr) return;
    // a real drag is a camera move, not a click — the gate is the gesture itself (px
    // travelled between down and up), never a timer
    if (Math.hypot(e.clientX - pr.x, e.clientY - pr.y) > 5) return;
    this.reservedSlot = pr.slot;
    const p = axialToXZ(spiralSlot(pr.slot), HEX_SIZE);
    // acknowledge NOW with a spark on the clicked cell, then ask the shell for the picker
    this.particles.burst(new THREE.Vector3(p.x, 0.6, p.z), [ACCENT, 0xd6ecff], 16, 1.8);
    try { if (window.parent !== window) window.parent.postMessage({ romp: "openPicker" }, "*"); } catch { /* standalone */ }
  }

  private beginSessionDrag(sid: string) {
    const pad = this.pads.get(sid);
    if (!pad || pad.dyingT >= 0) return;
    // carry at the pad's own camera depth, so the bean stays screen-true under the cursor
    this.dragSession = { sid, depth: this.camera.position.distanceTo(pad.group.position), over: false };
    pad.lift = 0;
    const label = this.trashEl.querySelector(".ht-label") as HTMLElement;
    label.textContent = "Drop to end " + pad.sess.name;
    this.trashEl.classList.add("show");
    this.renderer.domElement.style.cursor = "grabbing";
  }

  private moveSessionDrag(e: PointerEvent) {
    const d = this.dragSession!;
    const pad = this.pads.get(d.sid);
    if (!pad || pad.dyingT >= 0) { this.dropSessionDrag(false); return; }
    const p = new THREE.Vector3(this.pointer.x, this.pointer.y, 0.5).unproject(this.camera)
      .sub(this.camera.position).normalize().multiplyScalar(d.depth).add(this.camera.position);
    if (p.y < 0.4) p.y = 0.4;                // never carried below the board
    pad.carryTo(p);
    const r = this.trashEl.getBoundingClientRect();
    const over = e.clientX >= r.left - 14 && e.clientX <= r.right + 14 && e.clientY >= r.top - 14;
    if (over !== d.over) { d.over = over; this.trashEl.classList.toggle("armed", over); }
  }

  private dropSessionDrag(over: boolean) {
    const d = this.dragSession;
    this.dragSession = null;
    this.trashEl.classList.remove("show", "armed");
    this.renderer.domElement.style.cursor = "default";
    if (!d) return;
    const pad = this.pads.get(d.sid);
    if (!pad) return;
    if (over && pad.dyingT < 0) {
      // the drop is the decision: end the session (the kernel's own endSession op — the
      // same one the chat tab's × posts). The bean bursts where it vanished, and the
      // tile goes straight to the sink — the farewell hop is for natural exits.
      vscodeApi?.postMessage({ type: "endSession", id: d.sid });
      this.particles.burst(pad.beanWorldPos().setY(1.0), [0xe5484d, 0x8a8a8a, 0xffb3b6], 26, 2.6);
      pad.consumeBean();
      pad.dyingT = 0.26;
      if (this.selected === d.sid) this.deselect();
    } else {
      pad.carryTo(null);                     // anywhere else: they spring home, no harm done
    }
  }

  private static GHOST = "\0ghost";          // impossible sid — the ghost's pick token

  private pick(): { sid: string | null; bean: boolean } {
    this.raycaster.setFromCamera(this.pointer, this.camera);
    let best: { sid: string; d: number; bean: boolean } | null = null;
    for (const [sid, pad] of this.pads) {
      if (pad.dyingT >= 0) continue;
      const hits = this.raycaster.intersectObjects(pad.hitMeshes(), false);
      if (hits.length && (!best || hits[0].distance < best.d)) best = { sid, d: hits[0].distance, bean: false };
      // the bean stands proud of its tile, so when both are under the pointer the bean wins
      const bh = this.raycaster.intersectObjects(pad.beanMeshes(), false);
      if (bh.length && (!best || bh[0].distance < best.d)) best = { sid, d: bh[0].distance, bean: true };
    }
    const gh = this.raycaster.intersectObject(this.ghostFill, false);
    if (gh.length && (!best || gh[0].distance < best.d)) best = { sid: HiveWorld.GHOST, d: gh[0].distance, bean: false };
    if (best) return { sid: best.sid, bean: best.bean };
    // nothing solid under the pointer: any EMPTY cell of the board is the invitation too —
    // the ghost glides to it and a clean click recruits there (the user 2026-08-13)
    const oy = this.raycaster.ray.origin.y, dy = this.raycaster.ray.direction.y;
    const t = dy !== 0 ? -oy / dy : -1;
    if (t > 0) {
      const px = this.raycaster.ray.origin.x + this.raycaster.ray.direction.x * t;
      const pz = this.raycaster.ray.origin.z + this.raycaster.ray.direction.z * t;
      const cell = xzToAxial(px, pz, HEX_SIZE);
      if (hexDistance(cell, { q: 0, r: 0 }) <= this.latticeRings) {
        const slot = slotOfAxial(cell);
        if (slot >= 0 && !new Set(this.slots.values()).has(slot)) {
          if (slot !== this.ghostSlot) this.ghostTo(slot);
          return { sid: HiveWorld.GHOST, bean: false };
        }
      }
    }
    return { sid: null, bean: false };
  }

  // the direct line to a session: its chat opens in the chat pane (left of the board),
  // the hive stays put on the right — one message pair, used by the bean click, the
  // card's Open, and dblclick alike
  openChat(sid: string) {
    vscodeApi?.postMessage({ type: "openSession", id: sid });
    try { if (window.parent !== window) window.parent.postMessage({ romp: "reveal", pane: "chat" }, "*"); } catch { /* standalone */ }
  }

  // aim the ghost at a cell; frame() glides it there (everything springs, nothing teleports)
  private ghostTo(slot: number) {
    this.ghostSlot = slot;
    const p = axialToXZ(spiralSlot(slot), HEX_SIZE);
    this.ghostTarget.set(p.x, 0, p.z);
  }

  select(sid: string) {
    this.selected = sid;
    const pad = this.pads.get(sid);
    if (pad) {
      this.target.copy(pad.group.position).setY(0.6);
      this.dist = 10.5;
      this.pitch = 0.62;
      this.idleT = 0;
      this.card.show(pad.sess, Math.floor(Date.now() / 1000));
    }
  }
  deselect() {
    if (this.selected === null) return;
    this.selected = null;
    this.card.hide();
    this.frameAll();
  }

  // snap every eased camera value to its target — debug/screenshot use (virtual-time
  // headless runs too few frames for the springs to settle; production never calls this)
  settle() {
    this.yawCur = this.yaw; this.pitchCur = this.pitch; this.distCur = this.dist;
    this.targetCur.copy(this.target);
  }

  frameAll() {
    const occupied = [...this.pads.values()].filter((p) => p.dyingT < 0);
    const slots = occupied.map((p) => this.slots.get(p.sess.sid) ?? 0);
    const r = Math.max(6, frameRadius(slots, HEX_SIZE));
    let cx = 0, cz = 0;
    for (const p of occupied) { cx += p.group.position.x; cz += p.group.position.z; }
    const n = Math.max(1, occupied.length);
    this.target.set(cx / n, 0, cz / n);
    this.dist = Math.min(70, Math.max(11, (r / Math.tan((this.camera.fov * Math.PI) / 360)) * 0.48));
    this.pitch = 0.72;
  }

  // apply one payload's worth of change — called with the model's diff, never per frame
  sync(sessions: HiveSession[], first: boolean) {
    const prevSessions = [...this.pads.values()].map((p) => p.sess);
    const diff = diffSessions(first ? null : prevSessions, sessions);
    const stored = loadSlots(this.slots);
    this.slots = assignSlots(stored, sessions.map((s) => s.sid));
    // a click on an empty cell reserved it for the next NEW arrival (onPointerUp) — honor
    // it here, before pads are built. A REVIVED session outranks the click: it returns to
    // its remembered hex (the standing invariant), so the reservation only takes a sid
    // with no remembered home. Spent (or dropped) at the first arrival after the click.
    if (this.reservedSlot !== null && diff.added.length) {
      const want = this.reservedSlot;
      this.reservedSlot = null;
      const sid = diff.added.find((id) => !stored.has(id));
      if (sid !== undefined && (this.slots.get(sid) === want || ![...this.slots.values()].includes(want))) {
        this.slots.set(sid, want);
      }
    }
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
      if (this.selected === sid) { this.card.gone(); this.selected = null; this.frameAll(); }
      if (this.hovered === sid) this.hovered = null;
    }
    for (const sid of diff.added) {
      const s = bySid.get(sid)!;
      const pad = new Pad(s, this.slots.get(sid) ?? 0);
      this.pads.set(sid, pad);
      this.scene.add(pad.group);
    }
    const changed = new Set(diff.stateChanged.map((c) => c.sid));
    const nowS = Math.floor(Date.now() / 1000);
    for (const s of sessions) {
      const pad = this.pads.get(s.sid);
      if (pad && !diff.added.includes(s.sid)) pad.apply(s, changed.has(s.sid));
      if (this.selected === s.sid) this.card.refresh(s, nowS);
    }
    for (const sid of diff.goalDone) {
      const pad = this.pads.get(sid);
      if (pad) {
        const at = pad.group.position.clone().setY(PAD_H + 1.1);
        const tint = new THREE.Color(pad.sess.color?.bg || "#9cd2ff").getHex();
        // no pure white in the mix — additive + bloom turns white into a supernova
        this.particles.burst(at, [tint, ACCENT, 0xffd700, tint], 48, 4.2);
      }
    }
    // the ghost's PARK is the first FREE slot — the natural "next" cell of the spiral; it
    // only re-aims now if it isn't busy following the pointer (or its cell got taken)
    let free = 0;
    const taken = new Set(this.slots.values());
    while (taken.has(free)) free++;
    this.ghostHome = free;
    if (this.hovered !== HiveWorld.GHOST || taken.has(this.ghostSlot)) this.ghostTo(free);
    // …and keep the one-layer lattice one ring wider than anything on it
    this.ensureLattice(Math.max(3, ringOf(Math.max(free, ...this.slots.values())) + 1));

    if (first || diff.added.length || diff.removed.length) {
      if (this.selected === null) this.frameAll();
    }
    // deep-link: #focus=<sid> flies straight to that session's hex on arrival — the same
    // affordance a feed/outline "show me" tap will use (and the screenshot harness does)
    if (first) {
      const m = /[#&]focus=([^&]+)/.exec(location.hash);
      const sid = m && decodeURIComponent(m[1]);
      if (sid && this.pads.has(sid)) this.select(sid);
    }
    this.ensureLoop();
  }

  // (re)build the empty-cell lattice for rings 0..n: one LineSegments of every unique edge
  // (latticeSegments dedupes shared ones), faint accent, flat on the board — the layer the
  // pads dock into
  private ensureLattice(rings: number) {
    if (rings === this.latticeRings) return;
    this.latticeRings = rings;
    if (this.lattice) {
      this.scene.remove(this.lattice);
      this.lattice.geometry.dispose();
      (this.lattice.material as THREE.Material).dispose();
    }
    const seg = latticeSegments(rings, HEX_SIZE);
    const pos = new Float32Array((seg.length / 4) * 6);
    for (let i = 0, j = 0; i < seg.length; i += 4) {
      pos[j++] = seg[i]; pos[j++] = 0; pos[j++] = seg[i + 1];
      pos[j++] = seg[i + 2]; pos[j++] = 0; pos[j++] = seg[i + 3];
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    this.lattice = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
      color: ACCENT, transparent: true, opacity: 0.13, blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    this.lattice.position.y = 0.004;
    this.scene.add(this.lattice);
  }

  private ensureLoop() {
    const want = this.visible && !document.hidden;
    if (want && !this.running) {
      this.running = true;
      this.lastFrame = -1;                   // frame() takes its dt from the rAF clock only
      requestAnimationFrame(this.frame);
    } else if (!want) {
      this.running = false;                  // the in-flight rAF sees this and stops
    }
  }

  private frame = (now: number) => {
    if (!this.running) return;
    const dt = frameDt(now, this.lastFrame);
    this.lastFrame = now;
    this.clock += dt;
    this.idleT += dt;

    // hover pick once per frame (not per pointermove — cheaper and steadier); a carried
    // bean under the cursor must not churn hover or drag the ghost around
    const sid = this.dragSession ? this.hovered : this.pick().sid;
    if (sid !== this.hovered) {
      const old = this.hovered && this.hovered !== HiveWorld.GHOST ? this.pads.get(this.hovered) : null;
      if (old) old.lift = 0;
      this.hovered = sid;
      const nw = sid && sid !== HiveWorld.GHOST ? this.pads.get(sid) : null;
      if (nw) nw.lift = 0.12;
      this.ghostHover = sid === HiveWorld.GHOST;
      this.renderer.domElement.style.cursor = sid ? "pointer" : "default";
    }
    // the ghost glides to its aim (the hovered empty cell, or its park when the pointer
    // leaves the board) and breathes faintly, waking on hover
    if (this.hovered !== HiveWorld.GHOST && this.ghostSlot !== this.ghostHome) this.ghostTo(this.ghostHome);
    this.ghost.position.lerp(this.ghostTarget, 1 - Math.exp(-14 * dt));
    const gTarget = this.ghostHover ? 0.5 : 0.06 + 0.03 * (0.5 + 0.5 * Math.sin(this.clock * 1.2));
    this.ghostRingMat.opacity = ease(this.ghostRingMat.opacity, gTarget, dt, 8);
    this.ghostPlus.material.opacity = ease(this.ghostPlus.material.opacity, this.ghostHover ? 0.95 : 0.22, dt, 8);
    this.ghostPlus.position.y = 0.6 + (this.ghostHover ? 0.1 * Math.abs(Math.sin(this.clock * 4)) : 0);

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

    // ambient emitters, event-free by design: smoke while blocked, zzz while dozing — a
    // steady drizzle tied to the CURRENT state, not a transition (they stop the moment the
    // state moves on, no latch to forget)
    for (const pad of this.pads.values()) {
      if (pad.dyingT >= 0) continue;
      const st = pad.sess.state;
      if (st === "blocked" && Math.random() < dt * 1.6) {
        const at = pad.group.position.clone(); at.y += PAD_H + 0.75;
        at.x += 0.25; at.z += 0.45;           // off the dead laptop, not the bean's head
        this.particles.burst(at, [0x555b63, 0x3c4148, 0x6a7076], 2, 0.22, 0.55, 1.4);
      }
      if (st === "ready" && pad.sess.faded && Math.random() < dt * 0.8) {
        const at = pad.group.position.clone(); at.y += PAD_H + 1.25;
        at.x += 0.3;
        this.particles.burst(at, [0x9cd2ff, 0x6fa8d8], 1, 0.14, 0.3, 1.8);
      }
    }
    const dead: string[] = [];
    for (const [psid, pad] of this.pads)
      if (pad.update(dt, this.yawCur, this.distCur, psid === this.hovered || psid === this.selected)) dead.push(psid);
    for (const psid of dead) {
      const pad = this.pads.get(psid)!;
      this.scene.remove(pad.group);
      pad.dispose();
      this.pads.delete(psid);
    }
    this.particles.update(dt);

    this.composer.render();
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
  if (!m) return;
  // a refused drive op (foreign sid, dead kernel) comes back as an err — surface it on the
  // card instead of letting the send silently vanish (the fail-loudly rule)
  if (m.type === "err" && world && world.card.sid && (!m.sid || m.sid === world.card.sid)) {
    world.card.error(String(m.title || "That message was not delivered"), String(m.text || ""));
    return;
  }
  // a refused op that answers as a warn (renameSession's bad-name reply) — same fail-loudly
  // rule: this socket only carries replies to ops THIS page sent, so an open card is the
  // one that asked
  if (m.type === "warn" && world && world.card.sid) {
    world.card.error(String(m.text || "That didn't take"), "");
    return;
  }
  if (m.type !== "feed") return;
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
