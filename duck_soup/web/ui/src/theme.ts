// Light/dark theme. The choice lives on <html data-theme>, which styles.css keys its colour
// tokens off. index.html sets it with a tiny inline script before the stylesheet paints, so the
// page doesn't flash dark first; this module owns everything after that. Default follows the
// OS, like the docs site; an explicit toggle is remembered in localStorage.

export type Theme = 'dark' | 'light'

const STORAGE_KEY = 'ds-theme'
const EVENT = 'ds-theme-change'

function stored(): Theme | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return v === 'light' || v === 'dark' ? v : null
  } catch {
    return null
  }
}

function systemTheme(): Theme {
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

export function getTheme(): Theme {
  return document.documentElement.dataset['theme'] === 'light' ? 'light' : 'dark'
}

function apply(theme: Theme): void {
  if (theme === getTheme() && document.documentElement.dataset['theme']) return
  document.documentElement.dataset['theme'] = theme
  window.dispatchEvent(new CustomEvent<Theme>(EVENT, { detail: theme }))
}

export function setTheme(theme: Theme): void {
  try { localStorage.setItem(STORAGE_KEY, theme) } catch { /* private mode etc. */ }
  apply(theme)
}

export function toggleTheme(): void {
  setTheme(getTheme() === 'light' ? 'dark' : 'light')
}

export function onThemeChange(fn: (theme: Theme) => void): void {
  window.addEventListener(EVENT, e => fn((e as CustomEvent<Theme>).detail))
}

export function initTheme(): void {
  apply(stored() ?? systemTheme())
  // Keep following the OS until the user picks a theme explicitly.
  window.matchMedia?.('(prefers-color-scheme: light)').addEventListener('change', () => {
    if (!stored()) apply(systemTheme())
  })
}
