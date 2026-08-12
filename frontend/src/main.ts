import "./styles/index.css";

import { createEventSignalApp } from "./app";
import { FetchDashboardSource, FetchEventSource } from "./data";
import { parseRoute } from "./router";
import { mountStartupError, renderStartupError } from "./pages/startup-error";
import { LocalStorageWatchlist } from "./services/watchlist";
import {
  applySceneCssVariables,
  mountParallax,
  mountWorkers3d,
} from "./visuals";
import type { Dispose } from "./types";

async function bootstrap(): Promise<Dispose> {
  const page = document.querySelector<HTMLElement>("#page");
  const personCanvas = document.querySelector<HTMLCanvasElement>("#person-canvas");

  if (!page || !personCanvas) {
    throw new Error("EventSignal requires #page and #person-canvas.");
  }

  let disposeAmbientScene: Dispose | undefined;
  const syncAmbientScene = (): void => {
    const isBoard = parseRoute(location.hash).name === "board";
    document.body.classList.toggle("is-board", isBoard);
    if (isBoard) {
      disposeAmbientScene?.();
      disposeAmbientScene = undefined;
      return;
    }
    if (disposeAmbientScene) return;
    const disposeSceneCss = applySceneCssVariables(document.documentElement);
    const parallax = mountParallax(document.documentElement);
    const disposeWorkers = mountWorkers3d(personCanvas, parallax.getParallax);
    disposeAmbientScene = () => {
      disposeWorkers();
      parallax.dispose();
      disposeSceneCss();
    };
  };
  syncAmbientScene();
  window.addEventListener("hashchange", syncAmbientScene);

  const disposeApp = await createEventSignalApp({
    page,
    eventSource: new FetchEventSource(),
    dashboardSource: new FetchDashboardSource(),
    watchlist: new LocalStorageWatchlist(localStorage),
  });

  let disposed = false;
  return () => {
    if (disposed) return;
    disposed = true;
    disposeApp();
    window.removeEventListener("hashchange", syncAmbientScene);
    disposeAmbientScene?.();
    disposeAmbientScene = undefined;
  };
}

let dispose: Dispose = () => undefined;

void bootstrap()
  .then((cleanup) => {
    dispose = cleanup;
  })
  .catch((error: unknown) => {
    console.error("EventSignal failed to start.", error);
    // ⚠️ 只印 console 等於給使用者一片白畫面。拿掉 mock 退路之後，
    // 「取不到資料」變成正常會遇到的狀態，必須畫得出來。
    const page = document.querySelector<HTMLElement>("#page");
    if (page) {
      document.body.classList.remove("is-board");
      page.innerHTML = renderStartupError(error);
      dispose = mountStartupError(page);
    }
  });

window.addEventListener("pagehide", () => dispose(), { once: true });
import.meta.hot?.dispose(() => dispose());
