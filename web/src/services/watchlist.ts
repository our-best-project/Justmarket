export interface Watchlist {
  list(): readonly string[];
  has(eventId: string): boolean;
  add(eventId: string): void;
  remove(eventId: string): void;
  toggle(eventId: string): boolean;
}

export const WATCHLIST_STORAGE_KEY = "es_watch_v3";

export class LocalStorageWatchlist implements Watchlist {
  public constructor(
    private readonly storage: Storage,
    private readonly storageKey = WATCHLIST_STORAGE_KEY,
  ) {}

  public list(): readonly string[] {
    try {
      const value: unknown = JSON.parse(this.storage.getItem(this.storageKey) ?? "[]");
      return Array.isArray(value)
        ? value.filter((item): item is string => typeof item === "string")
        : [];
    } catch {
      return [];
    }
  }

  public has(eventId: string): boolean {
    return this.list().includes(eventId);
  }

  public add(eventId: string): void {
    const eventIds = [...this.list()];
    if (!eventIds.includes(eventId)) {
      eventIds.push(eventId);
      this.write(eventIds);
    }
  }

  public remove(eventId: string): void {
    this.write(this.list().filter((id) => id !== eventId));
  }

  public toggle(eventId: string): boolean {
    if (this.has(eventId)) {
      this.remove(eventId);
      return false;
    }

    this.add(eventId);
    return true;
  }

  private write(eventIds: readonly string[]): void {
    this.storage.setItem(this.storageKey, JSON.stringify(eventIds));
  }
}
