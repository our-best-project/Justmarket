import type { DashboardSnapshot } from "../types";

export interface DashboardSource {
  loadToday(): Promise<DashboardSnapshot>;
}
