import type { TaiwanBreadth } from "../types";
import { escapeHtml } from "./event-card";

/**
 * 多空領土戰：把當日漲跌家數畫成紅綠兩軍爭奪的領土，交界處有小兵交戰。
 *
 * 前身是 experiments_everyone/viz_Bright/多空領土戰3D.html（滿版 3D 場景、
 * 可拖曳旋轉、戰況用亂數漂移）。搬進儀表板時刻意砍掉三樣東西：
 *   1. 相機控制——儀表板區塊不該要求使用者先轉視角才看得懂，改固定斜角。
 *   2. 亂數漂移——原型是為了展示動態，這裡戰線必須忠實反映真實漲跌家數。
 *   3. 一半的小兵——原本每邊 22 隻，塞進面板高度後只是糊成一片。
 *
 * ⚠️ 這一塊違反了 dashboard.css 開頭寫的「不使用常駐動畫」原則。代價是自覺的，
 * 因此補了三道節流：離開視窗就停、prefers-reduced-motion 只畫一格靜態畫面、
 * 使用者可以手動關掉。
 */

/** 與 --dash-red / --dash-green 對應；canvas 讀不到 CSS 變數，掛載時再同步。 */
const FALLBACK_BULL = "#f05b4f";
const FALLBACK_BEAR = "#3eaa78";
const FRONT_LINE = "#f0c465";

const FIELD_X = 360;      // 戰線方向的半寬（世界座標）
// 縱深從原型的 150 收到 90：面板是 1214×360 的扁長條，縱深越大投影後越吃垂直空間，
// 而垂直一旦卡住，整個戰場就被縮到只用畫布中央一小塊（實測左右各空 370px）。
const FIELD_Z = 90;
const MAX_SIDE = 14;      // 每邊小兵上限
const CANVAS_H = 360;
// 樹高上限同樣影響垂直預算——樹是螢幕空間繪製，高度 = h × 2.1 × s，
// 原型的 42 會吃掉近一半的垂直空間。
const TREE_H_MIN = 16;
const TREE_H_MAX = 26;

export interface Camera { yaw: number; pitch: number; dist: number; fov: number }
interface RawPoint { cx: number; cy: number; d: number }
export interface CameraFit { fov: number; horizAnchor: number; vertAnchor: number }

/** 相機空間座標（尚未套 fov）。fitCamera 需要它來反推縮放。 */
export function rawProject(cam: Camera, x: number, y: number, z: number): RawPoint {
  const cx = x * Math.cos(cam.yaw) - z * Math.sin(cam.yaw);
  const wz = x * Math.sin(cam.yaw) + z * Math.cos(cam.yaw);
  const cy = y * Math.cos(cam.pitch) + wz * Math.sin(cam.pitch);
  const cz = wz * Math.cos(cam.pitch) - y * Math.sin(cam.pitch);
  return { cx, cy, d: Math.max(cam.dist + cz, 60) };
}

/**
 * 依畫布尺寸反推 fov 與兩個錨點，讓整個戰場（含最高的樹）剛好塞得下。
 *
 * 原本 fov 寫死 520、錨點固定置中，是照原型那個 380px 高的滿版場景調的。搬進
 * 儀表板後畫布變成又寬又扁（實測 1214×360），戰場的角就被切掉。與其手動試參數，
 * 不如算——螢幕座標對 fov 是線性的（s = fov/d），所以對每個極端點解出容得下的
 * 最大 fov、取最小值即可。畫布尺寸一變就重算，任何比例都不會再出界。
 *
 * 兩個錨點也一起解：只有一軸會綁住縮放，另一軸會剩餘裕。餘裕平均分到兩側才是
 * 置中；只把內容頂到 PAD 的話餘裕會全積在另一邊（實測左 22px、右 193px）。
 */
export function fitCamera(cam: Camera, width: number, height: number): CameraFit {
  const PAD = 8;
  // 樹與小兵是螢幕空間繪製的（高度 = 常數 × s），不是世界座標的 y，
  // 所以額外的上下留白要以「每單位 fov 的像素」計入，否則算出來的 fov 仍會切到樹梢。
  const TOP_EXTRA = TREE_H_MAX * 2.1;
  const BOTTOM_EXTRA = 8;                  // 影子
  if (width <= PAD * 2 || height <= PAD * 2) {
    return { fov: cam.fov, horizAnchor: 0.5, vertAnchor: 0.5 };
  }
  let left = 0;
  let right = 0;
  let up = 0;
  let down = 0;
  for (const x of [-FIELD_X, FIELD_X]) {
    for (const z of [-FIELD_Z, FIELD_Z]) {
      for (const y of [0, 26]) {           // 26 = 前線光牆頂端
        const { cx, cy, d } = rawProject(cam, x, y, z);
        left = Math.max(left, -cx / d);
        right = Math.max(right, cx / d);
        up = Math.max(up, (cy + TOP_EXTRA) / d);
        down = Math.max(down, (BOTTOM_EXTRA - cy) / d);
      }
    }
  }
  const byHeight = up + down > 0 ? (height - PAD * 2) / (up + down) : 1200;
  const byWidth = left + right > 0 ? (width - PAD * 2) / (left + right) : 1200;
  const fov = Math.max(60, Math.min(byHeight, byWidth, 1200));
  const anchor = (near: number, far: number, span: number): number =>
    Math.min(0.94, Math.max(0.06, (fov * near + (span - fov * (near + far)) / 2) / span));
  return { fov, horizAnchor: anchor(left, right, width), vertAnchor: anchor(up, down, height) };
}
interface Projected { x: number; y: number; s: number; depth: number }
interface Soldier {
  side: 1 | -1; x: number; z: number; speed: number; phase: number;
  state: "march" | "fight" | "knock" | "dead"; t: number; cd: number; hp: number;
}
interface Particle {
  x: number; y: number; z: number;
  vx: number; vy: number; vz: number; color: string; life: number;
}
interface Prop {
  type: "tree" | "rock"; x: number; z: number; tone: number;
  h?: number; w?: number; r?: number; verts?: { a: number; r: number }[];
}

/** 多方領土佔比（0–1）。平盤不列入——它既不是紅方也不是綠方的領土。 */
export function bullShare(breadth: TaiwanBreadth): number {
  const contested = breadth.advancers + breadth.decliners;
  if (contested <= 0) return 0.5;
  return breadth.advancers / contested;
}

export function renderTerritoryWar(breadth: TaiwanBreadth): string {
  const share = bullShare(breadth);
  const bullPct = Math.round(share * 100);
  return `
    <section class="dash-panel territory-war" aria-labelledby="war-title">
      <div class="dash-panel__head">
        <div><p class="dash-kicker">MARKET TERRITORY / 05</p><h2 id="war-title">多空領土戰</h2></div>
        <div class="war-head__right">
          <div class="dash-legend"><span class="up">▲ 上漲 ${breadth.advancers}</span><span class="down">▼ 下跌 ${breadth.decliners}</span><span>● 平盤 ${breadth.unchanged}</span></div>
          <button type="button" class="war-toggle" data-war-toggle aria-pressed="true">動畫：開</button>
        </div>
      </div>
      <p class="war__note">領土面積＝當日上漲／下跌家數的比例（平盤不列入）。交界處的推擠只是視覺呈現，不代表任何預測。今日多方 <b class="up">${bullPct}%</b>、空方 <b class="down">${100 - bullPct}%</b>。</p>
      <div class="war__stage">
        <canvas data-war-canvas height="${CANVAS_H}"
          role="img"
          aria-label="${escapeHtml(`多空領土圖：上漲 ${breadth.advancers} 家佔 ${bullPct}%，下跌 ${breadth.decliners} 家佔 ${100 - bullPct}%`)}"></canvas>
      </div>
    </section>`;
}

export function mountTerritoryWar(root: HTMLElement, breadth: TaiwanBreadth): () => void {
  const canvas = root.querySelector<HTMLCanvasElement>("[data-war-canvas]");
  const toggle = root.querySelector<HTMLButtonElement>("[data-war-toggle]");
  const ctx = canvas?.getContext("2d");
  if (!canvas || !ctx) return () => undefined;

  const style = getComputedStyle(root);
  const BULL = style.getPropertyValue("--dash-red").trim() || FALLBACK_BULL;
  const BEAR = style.getPropertyValue("--dash-green").trim() || FALLBACK_BEAR;

  // yaw 從原型的 0.9 降到 0.35：面板是又寬又扁的一整列，戰線正對鏡頭才用得到
  // 這個寬度；0.9 的斜角會讓戰場沿對角線拉長，兩端必然出界。
  // fov 不寫死，由 fitCamera() 依畫布尺寸反推（見下方）。
  const cam: Camera = { yaw: 0.35, pitch: 0.45, dist: 620, fov: 520 };
  let vertAnchor = 0.56;      // 由 fitCamera() 一併解出
  let horizAnchor = 0.5;
  const pct = bullShare(breadth);          // 固定值：戰線就是今天的漲跌家數比
  const frontX = (): number => -FIELD_X + 2 * FIELD_X * pct;

  let width = 0;
  let height = CANVAS_H;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);

  const project = (x: number, y: number, z: number): Projected => {
    const { cx, cy, d } = rawProject(cam, x, y, z);
    const s = cam.fov / d;
    return { x: width * horizAnchor + cx * s, y: height * vertAnchor - cy * s, s, depth: d };
  };

  const applyFit = (): void => {
    const fit = fitCamera(cam, width, height);
    cam.fov = fit.fov;
    horizAnchor = fit.horizAnchor;
    vertAnchor = fit.vertAnchor;
  };

  const soldiers: Soldier[] = [];
  const parts: Particle[] = [];

  const spawn = (side: 1 | -1): void => {
    const fx = frontX();
    soldiers.push({
      side,
      x: side > 0
        ? -FIELD_X + Math.random() * (fx + FIELD_X) * 0.6
        : FIELD_X - Math.random() * (FIELD_X - fx) * 0.6,
      z: (Math.random() * 2 - 1) * FIELD_Z * 0.85,
      speed: 0.5 + Math.random() * 0.7,
      phase: Math.random() * 7,
      state: "march", t: 0, cd: 0, hp: 2 + ((Math.random() * 2) | 0),
    });
  };

  const burst = (x: number, z: number, color: string, n = 6, up = 2): void => {
    for (let i = 0; i < n; i += 1) {
      parts.push({
        x, z, y: 14,
        vx: (Math.random() - 0.5) * 3, vz: (Math.random() - 0.5) * 3,
        vy: Math.random() * up, color, life: 20 + Math.random() * 12,
      });
    }
  };

  // 場景道具靠前後邊緣帶擺放，中央留給地面油漆字與戰線
  const props: Prop[] = [];
  const edgeZ = (): number =>
    (Math.random() < 0.5 ? -1 : 1) * (0.62 + Math.random() * 0.33) * FIELD_Z;
  for (let i = 0; i < 10; i += 1) {
    props.push({
      type: "tree", x: (Math.random() * 2 - 1) * FIELD_X * 0.92, z: edgeZ(),
      h: TREE_H_MIN + Math.random() * (TREE_H_MAX - TREE_H_MIN),
      w: 8 + Math.random() * 5, tone: Math.random() * 0.5,
    });
  }
  for (let i = 0; i < 7; i += 1) {
    const r = 5 + Math.random() * 7;
    const verts = Array.from({ length: 6 }, (_, a) => ({
      a: (a / 6) * Math.PI * 2, r: r * (0.7 + Math.random() * 0.5),
    }));
    props.push({
      type: "rock", x: (Math.random() * 2 - 1) * FIELD_X * 0.9, z: edgeZ(),
      r, verts, tone: Math.random() * 0.4,
    });
  }

  const drawTree = (o: Prop, p: Projected): void => {
    const k = p.s * 2.1;
    const h = o.h ?? 30;
    const w = o.w ?? 12;
    // 樹冠顏色跟著領土走：站在紅方領土就是紅樹，一眼看得出戰線在哪
    const canopy = o.x < frontX()
      ? `rgb(${168 + o.tone * 44},${54 + o.tone * 14},48)`
      : `rgb(38,${64 + o.tone * 40},58)`;
    ctx.save();
    ctx.translate(p.x, p.y);
    ctx.globalAlpha = 0.25;
    ctx.fillStyle = "#000";
    ctx.beginPath();
    ctx.ellipse(0, 1.5 * k, w * 0.8 * k, 2.4 * k, 0, 0, 7);
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#4a3a28";
    ctx.fillRect(-1.6 * k, -h * 0.36 * k, 3.2 * k, h * 0.36 * k);
    ctx.fillStyle = canopy;
    ctx.beginPath();
    ctx.moveTo(0, -h * k);
    ctx.lineTo(-w * 0.7 * k, -h * 0.45 * k);
    ctx.lineTo(w * 0.7 * k, -h * 0.45 * k);
    ctx.closePath();
    ctx.fill();
    ctx.beginPath();
    ctx.moveTo(0, -h * 0.72 * k);
    ctx.lineTo(-w * k, -h * 0.2 * k);
    ctx.lineTo(w * k, -h * 0.2 * k);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  };

  const drawRock = (o: Prop, p: Projected): void => {
    const k = p.s * 2.1;
    const g = 88 + o.tone * 36;
    const r = o.r ?? 6;
    ctx.save();
    ctx.translate(p.x, p.y);
    ctx.globalAlpha = 0.25;
    ctx.fillStyle = "#000";
    ctx.beginPath();
    ctx.ellipse(0, 1.2 * k, r * 1.1 * k, 2 * k, 0, 0, 7);
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.fillStyle = `rgb(${g},${g + 7},${g + 15})`;
    ctx.beginPath();
    (o.verts ?? []).forEach((v, i) => {
      const vx = Math.cos(v.a) * v.r * k;
      const vy = -Math.abs(Math.sin(v.a)) * v.r * 0.8 * k;
      if (i) ctx.lineTo(vx, vy); else ctx.moveTo(vx, vy);
    });
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  };

  const drawSoldier = (s: Soldier, now: number, p: Projected): void => {
    const c = s.side > 0 ? BULL : BEAR;
    const k = p.s * 2.1;
    const bob = s.state === "march" ? Math.sin(now / 90 + s.phase) * 1.5 * k : 0;
    const facing = Math.sign(project(s.x + 10, 0, s.z).x - p.x) * s.side;
    ctx.save();
    ctx.translate(p.x, p.y + bob);
    ctx.globalAlpha = 0.28;
    ctx.fillStyle = "#000";
    ctx.beginPath();
    ctx.ellipse(0, 1.2 * k, 3.2 * k, 1.3 * k, 0, 0, 7);
    ctx.fill();
    ctx.globalAlpha = 1;
    if (s.state === "knock") {
      ctx.rotate(facing * 0.5);
    }
    ctx.fillStyle = c;
    ctx.fillRect(-1.7 * k, -8 * k, 3.4 * k, 5.4 * k);          // 身體
    ctx.beginPath();
    ctx.arc(0, -9.6 * k, 1.9 * k, 0, 7);                        // 頭
    ctx.fill();
    // 武器：戰鬥中前刺，行軍時扛著
    ctx.strokeStyle = s.state === "fight" ? FRONT_LINE : c;
    ctx.lineWidth = Math.max(1, 0.8 * k);
    ctx.beginPath();
    if (s.state === "fight") {
      ctx.moveTo(facing * 1.4 * k, -6 * k);
      ctx.lineTo(facing * 5.4 * k, -6.6 * k);
    } else {
      ctx.moveTo(facing * 1.4 * k, -7.6 * k);
      ctx.lineTo(facing * 3.2 * k, -11 * k);
    }
    ctx.stroke();
    ctx.restore();
  };

  const quad = (corners: number[][], fill: string): void => {
    const q = corners.map((c) => project(c[0]!, 0, c[1]!));
    ctx.fillStyle = fill;
    ctx.beginPath();
    ctx.moveTo(q[0]!.x, q[0]!.y);
    for (let i = 1; i < 4; i += 1) ctx.lineTo(q[i]!.x, q[i]!.y);
    ctx.closePath();
    ctx.fill();
  };

  const withAlpha = (hex: string, alpha: number): string => {
    const v = hex.replace("#", "");
    const n = parseInt(v.length === 3 ? v.split("").map((c) => c + c).join("") : v, 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
  };

  const ground = (now: number): void => {
    const fx = frontX();
    quad([[-FIELD_X, -FIELD_Z], [fx, -FIELD_Z], [fx, FIELD_Z], [-FIELD_X, FIELD_Z]], withAlpha(BULL, 0.2));
    quad([[fx, -FIELD_Z], [FIELD_X, -FIELD_Z], [FIELD_X, FIELD_Z], [fx, FIELD_Z]], withAlpha(BEAR, 0.2));
    ctx.strokeStyle = "rgba(140,160,175,.12)";
    ctx.lineWidth = 1;
    for (let gx = -FIELD_X; gx <= FIELD_X; gx += 72) {
      const a = project(gx, 0, -FIELD_Z);
      const b = project(gx, 0, FIELD_Z);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
    for (let gz = -FIELD_Z; gz <= FIELD_Z; gz += 50) {
      const a = project(-FIELD_X, 0, gz);
      const b = project(FIELD_X, 0, gz);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
    // 前線發光牆
    const flick = Math.sin(now / 70) * 0.3 + 0.7;
    const t1 = project(fx, 0, -FIELD_Z);
    const t2 = project(fx, 0, FIELD_Z);
    const u1 = project(fx, 26, -FIELD_Z);
    const u2 = project(fx, 26, FIELD_Z);
    const grad = ctx.createLinearGradient(0, t1.y, 0, u1.y);
    grad.addColorStop(0, `rgba(240,196,101,${0.3 * flick})`);
    grad.addColorStop(1, "rgba(240,196,101,0)");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.moveTo(t1.x, t1.y);
    ctx.lineTo(t2.x, t2.y);
    ctx.lineTo(u2.x, u2.y);
    ctx.lineTo(u1.x, u1.y);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = `rgba(240,196,101,${0.55 * flick})`;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([6, 7]);
    ctx.beginPath();
    ctx.moveTo(t1.x, t1.y);
    ctx.lineTo(t2.x, t2.y);
    ctx.stroke();
    ctx.setLineDash([]);
  };

  /**
   * 把文字「印」在地面平面上：取地面兩個方向向量當文字的局部座標軸。
   *
   * ⚠️ 局部 +y 必須指向螢幕下方，否則字會上下顛倒。原型只有一個行列式翻正
   * （`if(a*d-b*c<0)`），那處理的是鏡像，不是顛倒——實際畫出來 54% / 46%
   * 兩組數字都是倒的。這裡改成明確指定兩軸方向：
   *   局部 +x → 螢幕右（字由左讀到右）
   *   局部 +y → 螢幕下（字的高度方向朝下）
   * 目前的 yaw/pitch 下，世界 +z 投影後指向螢幕左上，所以要取 −z。
   */
  const groundText = (text: string, wx: number, color: string, size: number): void => {
    const p0 = project(wx, 0, 0);
    const alongX = project(wx + 10, 0, 0);
    const alongZ = project(wx, 0, 10);
    let a = (alongX.x - p0.x) / 10;
    let b = (alongX.y - p0.y) / 10;
    let c = (alongZ.x - p0.x) / 10;
    let d = (alongZ.y - p0.y) / 10;
    if (d < 0) { c = -c; d = -d; }      // 字高朝下
    if (a < 0) { a = -a; b = -b; }      // 字由左讀到右
    ctx.save();
    ctx.transform(a, b, c, d, p0.x, p0.y);
    ctx.fillStyle = color;
    ctx.font = `900 ${size}px "Noto Sans TC", sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, 0, 0);
    ctx.restore();
  };

  const step = (dt: number, now: number): void => {
    const fx = frontX();
    const bulls = soldiers.filter((s) => s.side > 0).length;
    if (bulls < MAX_SIDE * pct) spawn(1);
    if (soldiers.length - bulls < MAX_SIDE * (1 - pct)) spawn(-1);

    for (const s of soldiers) {
      s.t += dt / 16;
      s.cd -= dt / 16;
      const toFront = fx - s.x;
      if (s.state === "march") {
        if (Math.abs(toFront) > 8) {
          s.x += Math.sign(toFront) * s.speed * dt / 16;
        } else {
          s.state = "fight";
          s.t = 0;
        }
        s.z += Math.sin(now / 500 + s.phase) * 0.15;
      } else if (s.state === "fight") {
        if (((s.t % 40) | 0) === 7 && s.cd <= 0) {
          s.cd = 30;
          burst(fx + (Math.random() - 0.5) * 12, s.z + (Math.random() - 0.5) * 20, FRONT_LINE, 5);
          const foes = soldiers.filter(
            (o) => o.side !== s.side && o.state === "fight" && Math.abs(o.z - s.z) < 45,
          );
          if (foes.length && Math.random() < 0.35) {
            const f = foes[(Math.random() * foes.length) | 0]!;
            f.state = "knock";
            f.t = 0;
            f.hp -= 1;
            burst(f.x, f.z, f.side > 0 ? BULL : BEAR, 4, 1.4);
          }
        }
        if (Math.abs(toFront) > 16) s.state = "march";
      } else if (s.state === "knock") {
        s.x -= s.side * 1.3 * dt / 16;
        if (s.t > 14) s.state = s.hp <= 0 ? "dead" : "march";
      }
    }
  };

  const draw = (now: number): void => {
    ctx.clearRect(0, 0, width, height);
    ground(now);

    const fx = frontX();
    const bullPct = Math.round(pct * 100);
    const wBull = fx + FIELD_X;
    const wBear = FIELD_X - fx;
    if (wBull > 70) {
      groundText(`${bullPct}%`, -FIELD_X + wBull / 2, withAlpha(BULL, 0.55), Math.min(64, wBull * 0.42));
    }
    if (wBear > 70) {
      groundText(`${100 - bullPct}%`, fx + wBear / 2, withAlpha(BEAR, 0.55), Math.min(64, wBear * 0.42));
    }

    // 深度排序後繪製（遠的先畫），否則近處的小兵會被遠處的樹蓋住
    const draws: { depth: number; fn: () => void }[] = [];
    for (const o of props) {
      const p = project(o.x, 0, o.z);
      draws.push({ depth: p.depth, fn: o.type === "tree" ? () => drawTree(o, p) : () => drawRock(o, p) });
    }
    for (let i = soldiers.length - 1; i >= 0; i -= 1) {
      const s = soldiers[i]!;
      if (s.state === "dead") {
        burst(s.x, s.z, s.side > 0 ? BULL : BEAR, 8, 2.4);
        soldiers.splice(i, 1);
        continue;
      }
      const p = project(s.x, 0, s.z);
      draws.push({ depth: p.depth, fn: () => drawSoldier(s, now, p) });
    }
    for (let i = parts.length - 1; i >= 0; i -= 1) {
      const q = parts[i]!;
      q.x += q.vx; q.z += q.vz; q.y += q.vy; q.vy -= 0.12; q.life -= 1;
      if (q.life <= 0 || q.y < 0) { parts.splice(i, 1); continue; }
      const p = project(q.x, q.y, q.z);
      draws.push({
        depth: p.depth,
        fn: () => {
          ctx.globalAlpha = Math.min(1, q.life / 16);
          ctx.fillStyle = q.color;
          ctx.fillRect(p.x, p.y, 2.4 * p.s * 1.4, 2.4 * p.s * 1.4);
          ctx.globalAlpha = 1;
        },
      });
    }
    draws.sort((a, b) => b.depth - a.depth);
    for (const d of draws) d.fn();
  };

  const resize = (): void => {
    width = canvas.clientWidth;
    height = CANVAS_H;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    applyFit();
    draw(performance.now());
  };

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let wanted = !reduceMotion.matches;      // 使用者的意願
  let visible = true;                      // 是否在視窗內
  let frame = 0;
  let last = 0;

  const tick = (now: number): void => {
    frame = requestAnimationFrame(tick);
    const dt = Math.min(now - last, 40);
    last = now;
    step(dt, now);
    draw(now);
  };

  // 常駐 rAF 在儀表板上是純浪費——面板捲出視窗就停，回來再續。
  const sync = (): void => {
    const shouldRun = wanted && visible;
    if (shouldRun && !frame) {
      last = performance.now();
      frame = requestAnimationFrame(tick);
    } else if (!shouldRun && frame) {
      cancelAnimationFrame(frame);
      frame = 0;
    }
  };

  const observer = new IntersectionObserver((entries) => {
    visible = entries.some((entry) => entry.isIntersecting);
    sync();
  }, { threshold: 0 });
  observer.observe(canvas);

  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(canvas);

  const onToggle = (): void => {
    wanted = !wanted;
    toggle?.setAttribute("aria-pressed", String(wanted));
    if (toggle) toggle.textContent = `動畫：${wanted ? "開" : "關"}`;
    sync();
  };
  toggle?.addEventListener("click", onToggle);

  if (!wanted && toggle) {
    toggle.setAttribute("aria-pressed", "false");
    toggle.textContent = "動畫：關";
  }

  resize();
  for (let i = 0; i < 10; i += 1) spawn(i % 2 ? 1 : -1);
  draw(performance.now());     // 先畫一格，即使動畫關著也看得到領土
  sync();

  return () => {
    if (frame) cancelAnimationFrame(frame);
    observer.disconnect();
    resizeObserver.disconnect();
    toggle?.removeEventListener("click", onToggle);
  };
}
