// Identical requests share one round trip. A config whose pipelines repeat the same sources
// (the tutorial's 34 pipelines, with the same 12 sources each) has a card per source per
// pipeline, and each card inspects its source on load, twice: 1200+ requests that the
// server's inspect worker answers one at a time, with the previews queued behind them for
// minutes. Shared, they're 12 of each.

export interface SharedRequests<T> {
  /** `run()`'s answer for `key`: the request in flight for it, or an answer from the last
   *  `ttlMs`, else a new request. Every caller gets its own copy of the answer. */
  get(key: string, run: () => Promise<T>): Promise<T>
  /** Forget every answer, e.g. after a source is downloaded again. */
  clear(): void
}

/**
 * Requests shared by key. An answer is kept for `ttlMs` after it arrives only when `keep`
 * says so (not an error, not "still downloading"), so those are asked again next time.
 * A rejected request isn't kept either. `now` is for tests.
 */
export function sharedRequests<T>(
  keep: (answer: T) => boolean,
  ttlMs: number,
  now: () => number = () => Date.now(),
): SharedRequests<T> {
  const entries = new Map<string, { answer: Promise<T>; done?: number }>()
  return {
    get(key, run) {
      const hit = entries.get(key)
      if (hit && (hit.done === undefined || now() - hit.done < ttlMs)) {
        return hit.answer.then(a => structuredClone(a))
      }
      const entry: { answer: Promise<T>; done?: number } = { answer: run() }
      entries.set(key, entry)
      entry.answer.then(
        a => {
          if (entries.get(key) !== entry) return
          if (keep(a)) entry.done = now()
          else entries.delete(key)
        },
        () => { if (entries.get(key) === entry) entries.delete(key) },
      )
      return entry.answer.then(a => structuredClone(a))
    },
    clear() { entries.clear() },
  }
}
