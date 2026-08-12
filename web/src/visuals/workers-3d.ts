// 內室裡走動的 Police 工人（three.js），座標綁定背景圖的 cover、
// scale 與視差，讓角色跟著地板移動。
import * as THREE from "three";
import {
  GLTFLoader,
  type GLTF,
} from "three/addons/loaders/GLTFLoader.js";

import type { Dispose, GetParallax } from "./parallax";
import { SCENE_GEOMETRY } from "./scene-config";

interface WorkerConfig {
  url: string;
  mode: "anchor" | "traverse";
  footIy: number;
  heightImg: number;
  px: number;
  baseYaw: number;
  timeScale: number;
  z: number;
  speed?: number;
  wrapMin?: number;
  wrapMax?: number;
  runYaw?: number;
}

interface Worker {
  cfg: WorkerConfig;
  rig: THREE.Group;
  model: THREE.Group;
  mixer: THREE.AnimationMixer | null;
  shadow: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>;
  hips?: THREE.Object3D;
  modelH: number;
  groundY1: number;
  px: number;
}

type TintableMaterial = THREE.Material & {
  map?: THREE.Texture | null;
  color?: THREE.Color;
  roughness?: number;
  metalness?: number;
};

const COLOR = new THREE.Color(0x97b4b3);
const WORKERS: WorkerConfig[] = [
  {
    url: new URL(
      "../assets/runtime/models/police-pacing.glb",
      import.meta.url,
    ).href,
    mode: "anchor",
    footIy: 0.745,
    heightImg: 0.14,
    px: 0.52,
    baseYaw: -2,
    timeScale: 1,
    z: 100,
  },
];

function stripRootXZ(clip: THREE.AnimationClip) {
  clip.tracks.forEach((track) => {
    if (!track.name.endsWith("Hips.position")) return;
    const values = track.values;
    for (let index = 0; index < values.length; index += 3) {
      values[index] = 0;
      values[index + 2] = 0;
    }
  });
}

function disposeObject(root: THREE.Object3D) {
  root.traverse((object) => {
    if (!(object instanceof THREE.Mesh)) return;
    object.geometry?.dispose();
    const materials = Array.isArray(object.material)
      ? object.material
      : [object.material];
    materials.forEach((material: TintableMaterial) => {
      material.map?.dispose();
      material.dispose();
    });
  });
}

export function mountWorkers3d(
  canvas: HTMLCanvasElement,
  getParallax: GetParallax,
): Dispose {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const freeze = location.search.includes("freeze");
  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(0, 1, 1, 0, -1000, 1000);
  const renderer = new THREE.WebGLRenderer({
    canvas,
    alpha: true,
    antialias: true,
    powerPreference: "high-performance",
  });

  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1;
  camera.position.z = 200;

  scene.add(new THREE.HemisphereLight(0xdce8f2, 0x40474f, 2.2));
  const key = new THREE.DirectionalLight(0xfff4e2, 2.4);
  key.position.set(-40, 120, 90);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xaecbe8, 0.9);
  fill.position.set(60, 40, 50);
  scene.add(fill);

  const shadowCanvas = document.createElement("canvas");
  shadowCanvas.width = shadowCanvas.height = 128;
  const shadowContext = shadowCanvas.getContext("2d");
  if (!shadowContext) {
    renderer.dispose();
    return () => undefined;
  }
  const gradient = shadowContext.createRadialGradient(64, 64, 4, 64, 64, 60);
  gradient.addColorStop(0, "rgba(8,12,20,0.5)");
  gradient.addColorStop(1, "rgba(8,12,20,0)");
  shadowContext.fillStyle = gradient;
  shadowContext.fillRect(0, 0, 128, 128);
  const shadowTexture = new THREE.CanvasTexture(shadowCanvas);
  shadowTexture.colorSpace = THREE.SRGBColorSpace;

  const workers: Worker[] = [];
  const loader = new GLTFLoader();
  const vector = new THREE.Vector3();
  const box = new THREE.Box3();
  const clock = new THREE.Clock();
  let width = 0;
  let height = 0;
  let imageWidth = 0;
  let imageHeight = 0;
  let offsetX = 0;
  let offsetY = 0;
  let unit = 1;
  let rafId = 0;
  let disposed = false;

  const computeCover = () => {
    width = window.innerWidth;
    height = window.innerHeight;
    const cover = Math.max(
      width / SCENE_GEOMETRY.imageWidth,
      height / SCENE_GEOMETRY.imageHeight,
    );
    imageWidth = SCENE_GEOMETRY.imageWidth * cover;
    imageHeight = SCENE_GEOMETRY.imageHeight * cover;
    offsetX = (width - imageWidth) * 0.5;
    offsetY = (height - imageHeight) * SCENE_GEOMETRY.positionY;
    unit = cover * SCENE_GEOMETRY.farScale;
  };

  const imageToScreen = (ix: number, iy: number, fx: number, fy: number) => {
    const baseX = offsetX + ix * imageWidth;
    const baseY = offsetY + iy * imageHeight;
    return {
      sx:
        width / 2 +
        SCENE_GEOMETRY.farScale * (baseX + fx - width / 2),
      sy:
        height / 2 +
        SCENE_GEOMETRY.farScale * (baseY + fy - height / 2),
    };
  };

  const layout = () => {
    if (disposed) return;
    computeCover();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(width, height, false);
    camera.left = 0;
    camera.right = width;
    camera.top = height;
    camera.bottom = 0;
    camera.updateProjectionMatrix();

    workers.forEach((worker) => {
      const targetHeight =
        SCENE_GEOMETRY.imageHeight * worker.cfg.heightImg * unit;
      const scale = targetHeight / worker.modelH;
      worker.model.scale.setScalar(scale);
      worker.model.position.y = -worker.groundY1 * scale;
      const shadowWidth = targetHeight * 0.6;
      worker.shadow.scale.set(shadowWidth, shadowWidth * 0.32, 1);
      worker.shadow.position.set(0, targetHeight * 0.015, -2);
    });
  };

  const buildWorker = (gltf: GLTF, cfg: WorkerConfig): Worker => {
    const rig = new THREE.Group();
    scene.add(rig);
    rig.position.z = cfg.z;

    const model = gltf.scene;
    model.traverse((object) => {
      if (!(object instanceof THREE.Mesh)) return;
      const materials = Array.isArray(object.material)
        ? object.material
        : [object.material];
      materials.forEach((material: TintableMaterial) => {
        material.map?.dispose();
        material.map = null;
        material.color?.copy(COLOR);
        material.roughness = Math.max(material.roughness ?? 0.6, 0.6);
        material.metalness = 0;
        material.needsUpdate = true;
      });
    });
    rig.add(model);

    model.scale.setScalar(1);
    model.position.set(0, 0, 0);
    model.rotation.y = cfg.mode === "anchor" ? cfg.baseYaw : Math.PI / 2;
    model.updateMatrixWorld(true);
    const bounds = new THREE.Box3().setFromObject(model);
    const modelH = Math.max(bounds.max.y - bounds.min.y, 0.001);

    let mixer: THREE.AnimationMixer | null = null;
    let clip: THREE.AnimationClip | null = null;
    if (gltf.animations[0]) {
      clip = gltf.animations[0];
      stripRootXZ(clip);
      mixer = new THREE.AnimationMixer(model);
      const action = mixer.clipAction(clip);
      action.setLoop(THREE.LoopRepeat, Infinity);
      action.timeScale = cfg.timeScale;
      action.play();
    }

    let groundY1 = bounds.min.y;
    if (mixer && clip) {
      groundY1 = Infinity;
      for (let index = 0; index <= 24; index += 1) {
        mixer.setTime((index / 24) * clip.duration);
        model.updateMatrixWorld(true);
        box.setFromObject(model);
        groundY1 = Math.min(groundY1, box.min.y);
      }
      mixer.setTime(0);
    }

    const shadow = new THREE.Mesh(
      new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({
        map: shadowTexture,
        transparent: true,
        depthWrite: false,
        toneMapped: false,
      }),
    );
    shadow.renderOrder = -1;
    rig.add(shadow);

    return {
      cfg,
      rig,
      model,
      mixer,
      shadow,
      hips: model.getObjectByName("mixamorigHips"),
      modelH,
      groundY1,
      px: cfg.px,
    };
  };

  WORKERS.forEach((config) => {
    loader.load(
      config.url,
      (gltf) => {
        if (disposed) {
          disposeObject(gltf.scene);
          return;
        }
        workers.push(buildWorker(gltf, config));
        layout();
      },
      undefined,
      () => {
        if (!disposed) console.warn("載入失敗:", config.url);
      },
    );
  });

  const animate = () => {
    if (disposed) return;
    const dt = Math.min(clock.getDelta(), 0.05);
    const parallax = getParallax();

    workers.forEach((worker) => {
      if (worker.cfg.mode === "traverse") {
        if (!reduced && !freeze) {
          worker.px += (worker.cfg.speed ?? 0) * dt;
          if (worker.px > (worker.cfg.wrapMax ?? Infinity)) {
            worker.px = worker.cfg.wrapMin ?? worker.px;
          }
        }
        worker.model.rotation.y = worker.cfg.runYaw ?? Math.PI / 2;
      }
      if (!reduced) worker.mixer?.update(dt);
      const { sx, sy } = imageToScreen(
        worker.px,
        worker.cfg.footIy,
        parallax.fx,
        parallax.fy,
      );
      worker.rig.position.x = sx;
      worker.rig.position.y = height - sy;
      if (worker.hips) {
        worker.hips.getWorldPosition(vector);
        worker.shadow.position.x = vector.x - worker.rig.position.x;
      }
    });
    renderer.render(scene, camera);
    rafId = requestAnimationFrame(animate);
  };

  window.addEventListener("resize", layout);
  computeCover();
  rafId = requestAnimationFrame(animate);

  return () => {
    if (disposed) return;
    disposed = true;
    cancelAnimationFrame(rafId);
    window.removeEventListener("resize", layout);
    workers.forEach((worker) => {
      worker.mixer?.stopAllAction();
      worker.mixer?.uncacheRoot(worker.model);
    });
    disposeObject(scene);
    shadowTexture.dispose();
    renderer.renderLists.dispose();
    renderer.dispose();
    scene.clear();
  };
}
