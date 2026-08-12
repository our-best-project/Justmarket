import { renderBreadcrumb } from "../components";

export function renderMethod(): string {
  const steps = [
    ["01", "收斂", "把多家媒體與官方公告講的「同一件事」聚合成一個事件，去除重複的新聞瀑布。"],
    ["02", "還原脈絡", "保留每個來源的標題、時間與是否含獨家細節，讓你看得到事件怎麼被拼出來。"],
    ["03", "市場驗證", "用事件發生前後的價格、成交量與外資買賣超，確認市場是否真的反應——描述已發生，不預測。"],
  ] as const;

  return `<div class="page-wrap">
      ${renderBreadcrumb([{ t: "本期", href: "#home" }, { t: "方法" }])}
      <section class="section section--top">
        <p class="section__eyebrow">HOW IT WORKS</p>
        <div class="section__head"><h1 class="section__title">方法：從新聞到可驗證的事件</h1></div>
        <p class="section__note">Justmarket 的每個數字都對應「已經發生」的事，一致度分數只是市場反應的摘要，不是買賣建議。</p>
        <div class="method">${steps.map(([number, title, description]) => `<div class="method__card acrylic"><span class="method__n">${number}</span><h3>${title}</h3><p>${description}</p></div>`).join("")}</div>
        <div class="method__note acrylic"><b>一致度分數怎麼看</b><p>分數綜合「官方確認程度、來源數、事件前後量價與籌碼反應」，越高代表市場越明確地反應了這件事；「觀察中」表示反應尚未成形。全部以已發生資料計算，不含任何預測。</p></div>
      </section></div>`;
}

export function mountMethod(): () => void {
  return () => undefined;
}
