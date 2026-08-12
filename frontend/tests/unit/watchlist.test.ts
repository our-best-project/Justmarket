import { describe, expect, it } from "vitest";

import {
  LocalStorageWatchlist,
  WATCHLIST_STORAGE_KEY,
} from "../../src/services/watchlist";

class MemoryStorage implements Storage {
  readonly #data = new Map<string, string>();

  public get length(): number {
    return this.#data.size;
  }

  public clear(): void {
    this.#data.clear();
  }

  public getItem(key: string): string | null {
    return this.#data.get(key) ?? null;
  }

  public key(index: number): string | null {
    return [...this.#data.keys()][index] ?? null;
  }

  public removeItem(key: string): void {
    this.#data.delete(key);
  }

  public setItem(key: string, value: string): void {
    this.#data.set(key, value);
  }
}

describe("LocalStorageWatchlist", () => {
  it("keeps the legacy storage key and persists toggles", () => {
    const storage = new MemoryStorage();
    const watchlist = new LocalStorageWatchlist(storage);

    expect(watchlist.toggle("EVT-001")).toBe(true);
    expect(storage.getItem(WATCHLIST_STORAGE_KEY)).toBe('["EVT-001"]');
    expect(watchlist.has("EVT-001")).toBe(true);
    expect(watchlist.toggle("EVT-001")).toBe(false);
    expect(watchlist.list()).toEqual([]);
  });

  it("recovers from invalid or non-array stored values", () => {
    const storage = new MemoryStorage();
    storage.setItem(WATCHLIST_STORAGE_KEY, "{broken");
    expect(new LocalStorageWatchlist(storage).list()).toEqual([]);

    storage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify({ event: "EVT-001" }));
    expect(new LocalStorageWatchlist(storage).list()).toEqual([]);
  });

  it("filters invalid ids while preserving valid strings", () => {
    const storage = new MemoryStorage();
    storage.setItem(
      WATCHLIST_STORAGE_KEY,
      JSON.stringify(["EVT-001", null, 42, "EVT-002"]),
    );

    expect(new LocalStorageWatchlist(storage).list()).toEqual([
      "EVT-001",
      "EVT-002",
    ]);
  });
});
