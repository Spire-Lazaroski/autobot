// The sign-in page: a slow particle tunnel in silver and gold, the Spire "A" mark assembling in the middle, then the
// wordmark. Built with the app (no CDN). With "reduce motion" on (or no WebGL) the mark is drawn still.
import * as THREE from "three";
import "./login.css";

const NAVY = 0x1a1a2e, GOLD = 0xe8b23d, AMBER = 0xd98c2b, SILVER = 0xc9cbd6;
const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const small = Math.min(window.innerWidth, window.innerHeight) < 700;

function scene() {
  const canvas = document.getElementById("scene") as HTMLCanvasElement;
  let renderer: THREE.WebGLRenderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  } catch {
    document.getElementById("wordmark")?.classList.add("on");
    return;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  const sc = new THREE.Scene();
  sc.fog = new THREE.FogExp2(NAVY, 0.045);
  const cam = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.1, 200);
  cam.position.set(0, 0.6, 7);

  // particle tunnel: mostly silver, some gold
  const N = small ? 1400 : 3200;
  const pos = new Float32Array(N * 3), col = new Float32Array(N * 3);
  const silver = new THREE.Color(SILVER), gold = new THREE.Color(GOLD), amber = new THREE.Color(AMBER);
  const place = (i: number, z?: number) => {
    const a = Math.random() * Math.PI * 2, r = 1.8 + Math.random() * 4.5;
    pos[i * 3] = Math.cos(a) * r; pos[i * 3 + 1] = Math.sin(a) * r; pos[i * 3 + 2] = z ?? -30 + Math.random() * 36;
  };
  for (let i = 0; i < N; i++) {
    place(i);
    const c = Math.random() < 0.78 ? silver : Math.random() < 0.6 ? gold : amber;
    col.set([c.r, c.g, c.b], i * 3);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setAttribute("color", new THREE.BufferAttribute(col, 3));
  const dots = new THREE.Points(g, new THREE.PointsMaterial({ size: 0.045, vertexColors: true, transparent: true, opacity: 0.6,
    blending: THREE.AdditiveBlending, depthWrite: false }));
  sc.add(dots);

  // faint rings
  const rings = new THREE.Group();
  const ringMat = new THREE.MeshBasicMaterial({ color: SILVER, wireframe: true, transparent: true, opacity: 0.06 });
  for (let i = 0; i < 16; i++) {
    const m = new THREE.Mesh(new THREE.TorusGeometry(2.6, 0.012, 6, 90), ringMat);
    m.position.z = -18 + i * 1.5;
    rings.add(m);
  }
  sc.add(rings);

  // the mark: a navy-metal triangle with a gold "A" cut-in, assembled from two halves
  const tri = new THREE.Shape();
  tri.moveTo(0, 1.25); tri.lineTo(-1.2, -0.85); tri.lineTo(1.2, -0.85); tri.lineTo(0, 1.25);
  const triMat = new THREE.MeshStandardMaterial({ color: 0x2a2a44, metalness: 0.85, roughness: 0.28, emissive: 0x111122 });
  const triMesh = new THREE.Mesh(new THREE.ExtrudeGeometry(tri, { depth: 0.22, bevelEnabled: true, bevelThickness: 0.04, bevelSize: 0.04, bevelSegments: 3 }), triMat);
  const A = new THREE.Shape();
  A.moveTo(0, 0.62); A.lineTo(-0.46, -0.62); A.lineTo(-0.26, -0.62); A.lineTo(-0.15, -0.3); A.lineTo(0.15, -0.3);
  A.lineTo(0.26, -0.62); A.lineTo(0.46, -0.62); A.lineTo(0, 0.62);
  const hole = new THREE.Path();
  hole.moveTo(0, 0.28); hole.lineTo(-0.09, -0.12); hole.lineTo(0.09, -0.12); hole.lineTo(0, 0.28);
  A.holes.push(hole);
  const aMat = new THREE.MeshStandardMaterial({ color: GOLD, metalness: 0.9, roughness: 0.22, emissive: AMBER, emissiveIntensity: 0.18 });
  const aMesh = new THREE.Mesh(new THREE.ExtrudeGeometry(A, { depth: 0.1, bevelEnabled: true, bevelThickness: 0.02, bevelSize: 0.015, bevelSegments: 2 }), aMat);
  aMesh.position.set(0, -0.28, 0.24);
  const mark = new THREE.Group();
  mark.add(triMesh, aMesh);
  mark.position.set(0, 1.95, 0);       // upper third of the screen: the wordmark and the card sit below it
  mark.scale.setScalar(small ? 0.8 : 1);
  sc.add(mark);

  sc.add(new THREE.AmbientLight(0x404060, 1.2));
  const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(3, 4, 5); sc.add(key);
  const rim = new THREE.PointLight(GOLD, 18, 20); rim.position.set(-2.5, 1.5, 2.5); sc.add(rim);
  const back = new THREE.PointLight(0x8899cc, 10, 20); back.position.set(2, 2, -3); sc.add(back);

  const resize = () => {
    cam.aspect = window.innerWidth / window.innerHeight;
    cam.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  };
  window.addEventListener("resize", resize);

  if (reduce) {                       // still picture: the finished mark, no motion
    mark.rotation.set(0, 0, 0);
    renderer.render(sc, cam);
    document.getElementById("wordmark")?.classList.add("on");
    return;
  }

  // entrance: the triangle and the A fly in from opposite sides and lock together
  triMesh.position.set(-3.5, 1.5, -6); aMesh.position.set(3.5, -2, -5);
  const triEnd = new THREE.Vector3(0, 0, 0), aEnd = new THREE.Vector3(0, -0.28, 0.24);
  const triStart = triMesh.position.clone(), aStart = aMesh.position.clone();
  const t0 = performance.now();
  let shown = false;
  const ease = (x: number) => 1 - Math.pow(1 - x, 3);
  const loop = (now: number) => {
    const t = (now - t0) / 1000;
    const k = ease(Math.min(1, t / 2.6));
    triMesh.position.lerpVectors(triStart, triEnd, k);
    aMesh.position.lerpVectors(aStart, aEnd, ease(Math.min(1, Math.max(0, (t - 0.4) / 2.4))));
    triMesh.rotation.set((1 - k) * 1.2, (1 - k) * -2.2, 0);
    mark.rotation.y = k >= 1 ? Math.sin(t * 0.35) * 0.28 : 0;
    aMat.emissiveIntensity = 0.18 + (k >= 1 ? 0.1 + Math.sin(t * 1.6) * 0.08 : 0);
    if (!shown && t > 2.4) { shown = true; document.getElementById("wordmark")?.classList.add("on"); }
    for (let i = 0; i < N; i++) {     // the tunnel drifts towards the viewer
      const z = pos[i * 3 + 2] + 0.06;
      if (z > 7) place(i, -30); else pos[i * 3 + 2] = z;
    }
    g.attributes.position.needsUpdate = true;
    rings.children.forEach((r) => { r.position.z += 0.025; if (r.position.z > 7) r.position.z = -18; r.rotation.z += 0.0015; });
    rim.intensity = 16 + Math.sin(t * 1.2) * 4;
    renderer.render(sc, cam);
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}

function form() {
  const f = document.getElementById("login") as HTMLFormElement;
  const err = document.getElementById("error") as HTMLParagraphElement;
  const go = document.getElementById("go") as HTMLButtonElement;
  (document.getElementById("user") as HTMLInputElement).focus();
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.hidden = true;
    go.disabled = true;
    try {
      const res = await fetch("/api/login", {
        method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
        body: JSON.stringify({
          user: (document.getElementById("user") as HTMLInputElement).value,
          password: (document.getElementById("password") as HTMLInputElement).value,
          remember: (document.getElementById("remember") as HTMLInputElement).checked,
        }),
      });
      if (res.ok) { window.location.replace("/"); return; }
      const body = await res.json().catch(() => ({}));
      err.textContent = body.detail ?? "Sign-in failed.";
    } catch {
      err.textContent = "No connection to the lab.";
    }
    (document.getElementById("password") as HTMLInputElement).value = "";
    err.hidden = false;
    go.disabled = false;
  });
}

scene();
form();
