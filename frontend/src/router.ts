export type RouteName = "home" | "board" | "all" | "watchlist" | "method" | "detail" | "search";

export type AppRoute =
  | { readonly name: "home" }
  | { readonly name: "board" }
  | { readonly name: "all" }
  | { readonly name: "watchlist" }
  | { readonly name: "method" }
  | { readonly name: "detail"; readonly id: string }
  | { readonly name: "search"; readonly q: string };

export function parseRoute(hash: string): AppRoute {
  const route = (hash || "#home").slice(1);
  if (route.startsWith("event-")) {
    return { name: "detail", id: route.slice(6) };
  }
  // 關鍵字放在 hash 裡（#search=台積電）：重新整理與分享網址都能保留搜尋條件
  if (route.startsWith("search=")) {
    return { name: "search", q: decodeURIComponent(route.slice(7)) };
  }

  if (
    route === "board"
    || route === "all"
    || route === "watchlist"
    || route === "method"
    || route === "home"
  ) {
    return { name: route };
  }

  return { name: "home" };
}

export function getNavigationRoute(route: AppRoute): Exclude<RouteName, "detail" | "search"> {
  // 詳情與搜尋沒有自己的導覽項，導覽列高亮借用「全部事件」
  return route.name === "detail" || route.name === "search" ? "all" : route.name;
}
