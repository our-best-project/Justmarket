// 兩層視差：整體慢晃（idle sway）+ 滑鼠微視差，阻尼平滑。
// 前景移動幅度比後景大，讓近景移動較快並產生空間感。

export interface ParallaxState {
  fx: number;
  fy: number;
  nx: number;
  ny: number;
}

export type GetParallax = () => Readonly<ParallaxState>;
export type Dispose = () => void;

export interface ParallaxController {
  getParallax: GetParallax;
  dispose: Dispose;
}

const NEAR = { x: 72, y: 40 };
const FAR = { x: 34, y: 20 };
const SWAY = { x: 13, y: 8 };

export function mountParallax(
  root: HTMLElement = document.documentElement,
): ParallaxController {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const state: ParallaxState = { fx: 0, fy: 0, nx: 0, ny: 0 };

  let targetX = 0;
  let targetY = 0;
  let curX = 0;
  let curY = 0;
  let rafId = 0;
  let disposed = false;

  const onPointerMove = (event: PointerEvent) => {
    targetX = (event.clientX / window.innerWidth) * 2 - 1;
    targetY = (event.clientY / window.innerHeight) * 2 - 1;
  };
  const onPointerLeave = () => {
    targetX = 0;
    targetY = 0;
  };
  const onDeviceOrientation = (event: DeviceOrientationEvent) => {
    if (event.gamma == null || event.beta == null) return;
    targetX = Math.max(-1, Math.min(1, event.gamma / 30));
    targetY = Math.max(-1, Math.min(1, (event.beta - 45) / 30));
  };

  window.addEventListener("pointermove", onPointerMove, { passive: true });
  window.addEventListener("pointerleave", onPointerLeave);
  if (window.DeviceOrientationEvent && "ontouchstart" in window) {
    window.addEventListener("deviceorientation", onDeviceOrientation, {
      passive: true,
    });
  }

  const start = performance.now();
  const frame = (now: number) => {
    if (disposed) return;

    const t = (now - start) / 1000;
    const swayX =
      Math.sin(t * 0.32) * SWAY.x + Math.sin(t * 0.11) * SWAY.x * 0.4;
    const swayY = Math.cos(t * 0.27) * SWAY.y;

    curX += (targetX - curX) * 0.045;
    curY += (targetY - curY) * 0.045;

    const nx = -curX * NEAR.x + swayX;
    const ny = -curY * NEAR.y + swayY;
    const fx = -curX * FAR.x + swayX * 0.5;
    const fy = -curY * FAR.y + swayY * 0.5;

    root.style.setProperty("--near-x", `${nx.toFixed(2)}px`);
    root.style.setProperty("--near-y", `${ny.toFixed(2)}px`);
    root.style.setProperty("--far-x", `${fx.toFixed(2)}px`);
    root.style.setProperty("--far-y", `${fy.toFixed(2)}px`);
    Object.assign(state, { fx, fy, nx, ny });

    rafId = requestAnimationFrame(frame);
  };

  if (reduced) {
    state.fx = 0;
    state.fy = 0;
  }
  rafId = requestAnimationFrame(frame);

  const dispose = () => {
    if (disposed) return;
    disposed = true;
    cancelAnimationFrame(rafId);
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerleave", onPointerLeave);
    window.removeEventListener("deviceorientation", onDeviceOrientation);
    root.style.removeProperty("--near-x");
    root.style.removeProperty("--near-y");
    root.style.removeProperty("--far-x");
    root.style.removeProperty("--far-y");
    Object.assign(state, { fx: 0, fy: 0, nx: 0, ny: 0 });
  };

  return {
    getParallax: () => state,
    dispose,
  };
}
