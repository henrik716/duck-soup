// The step gallery's search: which cards a query matches. Kept free of the DOM so it can be
// unit-tested; step-gallery.ts shows and hides the cards.

export interface SearchableStep {
  type: string
  name: string
  desc: string
  /** What the step is called elsewhere: FME transformers, QGIS tools, everyday words. */
  aliases?: string[]
}

/** Lower case, accents off, and anything but letters and digits as a single space. */
export function normalize(text: string): string {
  return text.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, ' ').trim()
}

/** The text a card is found by: its name, step type, description, group and aliases. */
export function haystack(item: SearchableStep, group = ''): string {
  return normalize([item.name, item.type, item.desc, group, ...(item.aliases ?? [])].join(' '))
}

/**
 * Whether `item` matches `query`: every word of the query is found in the card's text,
 * also with the spaces taken out, so "snapto" finds Snap to Grid, "linecombiner"
 * LineCombiner and "remove dup" Remove Duplicates. An empty query matches everything.
 */
export function matchesQuery(item: SearchableStep, query: string, group = ''): boolean {
  const words = normalize(query).split(' ').filter(Boolean)
  if (!words.length) return true
  const text = haystack(item, group)
  const joined = text.replace(/ /g, '')
  return words.every(w => text.includes(w) || joined.includes(w))
}
