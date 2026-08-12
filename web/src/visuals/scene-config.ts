import type { Dispose } from "./parallax";

export const sceneAssetUrls = {
  background: {
    webp: new URL("../assets/runtime/images/bg.webp", import.meta.url).href,
    png: new URL("../assets/runtime/images/bg.png", import.meta.url).href,
  },
  foreground: {
    webp: new URL("../assets/runtime/images/fg.webp", import.meta.url).href,
    png: new URL("../assets/runtime/images/fg.png", import.meta.url).href,
  },
} as const;

export const SCENE_GEOMETRY = {
  imageWidth: 1920,
  imageHeight: 1076,
  farScale: 1.08,
  nearScale: 1.14,
  positionY: 0.46,
} as const;

function cssUrl(url: string): string {
  return `url("${url.replaceAll("\\", "\\\\").replaceAll('"', '\\"')}")`;
}

function supportsWebp(document: Document): boolean {
  try {
    return document
      .createElement("canvas")
      .toDataURL("image/webp")
      .startsWith("data:image/webp");
  } catch {
    return false;
  }
}

export function applySceneCssVariables(root: HTMLElement): Dispose {
  const format = supportsWebp(root.ownerDocument) ? "webp" : "png";
  root.style.setProperty(
    "--bg",
    cssUrl(sceneAssetUrls.background[format]),
  );
  root.style.setProperty(
    "--fg",
    cssUrl(sceneAssetUrls.foreground[format]),
  );
  root.style.setProperty("--far-scale", String(SCENE_GEOMETRY.farScale));
  root.style.setProperty("--near-scale", String(SCENE_GEOMETRY.nearScale));
  root.style.setProperty(
    "--scene-position-y",
    `${SCENE_GEOMETRY.positionY * 100}%`,
  );

  return () => {
    root.style.removeProperty("--bg");
    root.style.removeProperty("--fg");
    root.style.removeProperty("--far-scale");
    root.style.removeProperty("--near-scale");
    root.style.removeProperty("--scene-position-y");
  };
}
