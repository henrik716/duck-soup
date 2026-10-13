import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { PreviewRow } from './types'
import { esc } from './dom'
import { getTheme, onThemeChange } from './theme'

// maplibre-gl resolves its worker script relative to its own bundled URL, which doesn't
// exist once Vite inlines everything into one chunk, and the worker module itself imports
// a sibling "maplibre-gl-shared.mjs" by a fixed relative path that Vite's normal asset
// pipeline (hashed filenames) would break. Both files are vendored verbatim under
// public/maplibre/ (copied from node_modules/maplibre-gl/dist/ — re-copy them whenever
// maplibre-gl is upgraded) so they're served unhashed, next to each other, at a stable URL.
maplibregl.setWorkerUrl(`${import.meta.env.BASE_URL}maplibre/maplibre-gl-worker.mjs`)

let map: maplibregl.Map | null = null

// Whatever updateMap() was last given, kept around so a basemap switch (which tears down
// and rebuilds every runtime-added source/layer, see switchBaseStyle()) can redraw the same
// preview without asking main.ts to re-fetch.
let lastRows: PreviewRow[] = []
let lastActiveStep: any = null

// Each drawn feature's `id` is its row's index in lastRows (null here when the row has no
// geometry), which is how the map, its feature-state and the preview table refer to a row.
let featuresByRow: (GeoJSON.Feature | null)[] = []
let selectedRow: number | null = null
let hoveredRow: number | null = null
let popup: maplibregl.Popup | null = null
let featureSelectListener: ((row: number | null) => void) | null = null

const SOURCE_ID = 'preview-data'
const HOVER_SOURCE_ID = 'preview-hover'
const SELECTED_SOURCE_ID = 'preview-selected'
const PREVIEW_LAYER_IDS = ['preview-fill', 'preview-line', 'preview-point']

const geometryTypes = (...types: string[]): maplibregl.FilterSpecification =>
  ['in', ['geometry-type'], ['literal', types]]
const POLYGONS = geometryTypes('Polygon', 'MultiPolygon')
const LINES_AND_POLYGONS = geometryTypes('LineString', 'MultiLineString', 'Polygon', 'MultiPolygon')
const POINTS = geometryTypes('Point', 'MultiPoint')

// Drawn bottom to top: the preview, the nearest-neighbor search radius, then the selected
// feature (its own source, so it sits above every other feature, with a casing in the ink
// colour that keeps its outline readable on either basemap).
const LAYERS: { id: string; type: 'fill' | 'line' | 'circle'; source: string; filter?: maplibregl.FilterSpecification }[] = [
  { id: 'preview-fill', type: 'fill', source: SOURCE_ID, filter: POLYGONS },
  { id: 'preview-line', type: 'line', source: SOURCE_ID, filter: LINES_AND_POLYGONS },
  { id: 'preview-point', type: 'circle', source: SOURCE_ID, filter: POINTS },
  { id: 'hover-fill', type: 'fill', source: HOVER_SOURCE_ID },
  { id: 'hover-line', type: 'line', source: HOVER_SOURCE_ID },
  { id: 'selected-fill', type: 'fill', source: SELECTED_SOURCE_ID, filter: POLYGONS },
  { id: 'selected-casing', type: 'line', source: SELECTED_SOURCE_ID, filter: LINES_AND_POLYGONS },
  { id: 'selected-line', type: 'line', source: SELECTED_SOURCE_ID, filter: LINES_AND_POLYGONS },
  { id: 'selected-point', type: 'circle', source: SELECTED_SOURCE_ID, filter: POINTS },
]

const HOVERED: maplibregl.ExpressionSpecification = ['boolean', ['feature-state', 'hover'], false]

function layerPaint(c: ReturnType<typeof colors>): Record<string, maplibregl.AllPaintProperties> {
  return {
    'preview-fill': { 'fill-color': c.feature, 'fill-opacity': ['case', HOVERED, 0.45, 0.25] },
    'preview-line': {
      'line-color': c.feature,
      'line-width': ['case', HOVERED, 5, 3],
      'line-opacity': ['case', HOVERED, 1, 0.8],
    },
    'preview-point': {
      'circle-radius': ['case', HOVERED, 8, 6],
      'circle-color': c.feature,
      'circle-stroke-color': c.ink,
      'circle-stroke-width': 1,
      'circle-opacity': ['case', HOVERED, 1, 0.8],
    },
    'hover-fill': { 'fill-color': c.accent, 'fill-opacity': 0.15 },
    'hover-line': { 'line-color': c.accent, 'line-width': 1, 'line-dasharray': [4, 4] },
    'selected-fill': { 'fill-color': c.selected, 'fill-opacity': 0.35 },
    'selected-casing': { 'line-color': c.ink, 'line-width': 8, 'line-opacity': 0.9 },
    'selected-line': { 'line-color': c.selected, 'line-width': 4 },
    'selected-point': {
      'circle-radius': 9,
      'circle-color': c.selected,
      'circle-stroke-color': c.ink,
      'circle-stroke-width': 3,
    },
  }
}

const STYLES = {
  dark: 'https://tiles.openfreemap.org/styles/dark',
  light: 'https://tiles.openfreemap.org/styles/positron',
} as const
type StyleKey = keyof typeof STYLES
// Starts out matching the app theme and follows it on every theme toggle; the on-map
// Dark/Light switcher can still override it in between.
let currentStyleKey: StyleKey = getTheme()

// MapLibre paint properties are style-spec values (color strings/expressions), not CSS —
// they can't reference `var(--token)`. So resolve the tokens to literal colors instead. Done
// lazily and memoised: at module-eval time the stylesheet may not be applied yet.
let _colors: { feature: string; selected: string; accent: string; ink: string } | null = null
function colors(): { feature: string; selected: string; accent: string; ink: string } {
  if (_colors) return _colors
  const cs = getComputedStyle(document.documentElement)
  const read = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback
  _colors = {
    // Its own dedicated token, not --spatial — that variable is shared with several other UI
    // elements (step icons, lineage nodes, badges, syntax highlighting) that shouldn't recolor
    // along with the map's drawn features.
    feature: read('--map-feature', '#ff5ec4'),
    selected: read('--map-selected', '#ffc53d'),
    accent: read('--accent', '#9d85ff'),
    ink: read('--ink', '#f0edff'),
  }
  return _colors
}

function emptyFC(): GeoJSON.FeatureCollection {
  return { type: 'FeatureCollection', features: [] }
}

// The map container clips anything outside its own bounds — so a popup taller than the
// container fits nowhere no matter how it's positioned, and its bottom rows become
// unreachable. Size the popup to what's actually available right now (the map panel is
// resizable) instead of a fixed guess, leaving room for the toolbar it opens below plus its
// own tip/margins.
function popupMaxHeight(): number {
  const available = (map?.getContainer().clientHeight ?? 300) - 90
  return Math.max(120, Math.min(available, 400))
}

// Same reasoning as popupMaxHeight, but for width: the default `maxWidth: 'none'` lets long
// attribute values (URLs, file paths) push the popup wider than the map panel, which on a
// narrow/small screen means part of it renders past the clipped container edge and is
// unreadable. Cap it to the panel's own width minus a small margin.
function popupMaxWidth(): string {
  const available = (map?.getContainer().clientWidth ?? 300) - 24
  return `${Math.max(180, available)}px`
}

// A small custom base-layer switcher — MapLibre has no built-in equivalent of Leaflet's
// L.control.layers.
class BaseLayerControl implements maplibregl.IControl {
  private container: HTMLElement | null = null

  onAdd(): HTMLElement {
    this.container = document.createElement('div')
    this.container.className = 'maplibregl-ctrl map-base-switcher'
    ;(Object.keys(STYLES) as StyleKey[]).forEach(key => {
      const btn = document.createElement('button')
      btn.type = 'button'
      btn.textContent = key === 'dark' ? 'Dark' : 'Light'
      btn.dataset['style'] = key
      btn.className = key === currentStyleKey ? 'active' : ''
      btn.addEventListener('click', () => switchBaseStyle(key))
      this.container!.appendChild(btn)
    })
    return this.container
  }

  onRemove(): void {
    this.container?.remove()
    this.container = null
  }
}

function switchBaseStyle(key: StyleKey): void {
  if (!map || key === currentStyleKey) return
  currentStyleKey = key
  document.querySelectorAll<HTMLButtonElement>('.map-base-switcher button')
    .forEach(b => b.classList.toggle('active', b.dataset['style'] === key))
  map.setStyle(STYLES[key])
  map.once('style.load', () => setupPreviewLayers())
}

// The preview layers' colours are read from the theme's CSS tokens, so re-read them after a
// theme change. A basemap switch rebuilds the layers from colors() anyway; when the basemap
// stays put, repaint the existing layers in place.
function applyTheme(theme: StyleKey): void {
  _colors = null
  if (!map) return
  if (theme !== currentStyleKey) { switchBaseStyle(theme); return }
  for (const [layer, paint] of Object.entries(layerPaint(colors()))) {
    if (!map.getLayer(layer)) continue
    for (const prop of Object.keys(paint) as (keyof maplibregl.AllPaintProperties)[]) {
      map.setPaintProperty(layer, prop, paint[prop])
    }
  }
}

export function initMap(): void {
  try {
    map = new maplibregl.Map({
      container: 'map',
      style: STYLES[currentStyleKey],
      center: [10.75, 59.91],
      zoom: 11,
    })
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-left')
    map.addControl(new BaseLayerControl(), 'top-right')

    map.once('load', () => setupPreviewLayers())
    wireInteractions()
    onThemeChange(applyTheme)

    new ResizeObserver(() => map?.resize()).observe(document.getElementById('map-container')!)
  } catch (e) {
    console.error('Failed to initialize Map:', e)
  }
}

function setupPreviewLayers(): void {
  if (!map) return
  const paint = layerPaint(colors())
  for (const id of [SOURCE_ID, HOVER_SOURCE_ID, SELECTED_SOURCE_ID]) {
    if (!map.getSource(id)) map.addSource(id, { type: 'geojson', data: emptyFC() })
  }
  for (const layer of LAYERS) {
    if (!map.getLayer(layer.id)) {
      map.addLayer({ ...layer, paint: paint[layer.id] } as maplibregl.LayerSpecification)
    }
  }

  // Redraw whatever was last shown, but never re-fit the view — this runs after every style
  // load (including a basemap switch), and a basemap switch shouldn't recenter the map.
  render(false)
  setSelection(selectedRow)
}

/** The rows drawn under a screen point, topmost first. A few pixels of slack around the
 *  point, so a thin line or a small point doesn't take pixel-perfect aim to hit. */
function rowsAt(point: maplibregl.Point): number[] {
  if (!map || !map.getLayer(PREVIEW_LAYER_IDS[0])) return []
  const pad = 4
  const hits = map.queryRenderedFeatures(
    [[point.x - pad, point.y - pad], [point.x + pad, point.y + pad]],
    { layers: PREVIEW_LAYER_IDS },
  )
  const rows: number[] = []
  for (const f of hits) {
    const row = Number(f.id)
    if (Number.isInteger(row) && !rows.includes(row)) rows.push(row)
  }
  return rows
}

// Wired once, on the map rather than per layer: map-level listeners outlive a basemap switch
// (setStyle), so per-layer ones added on every style load would stack up.
function wireInteractions(): void {
  if (!map) return
  map.on('mousemove', e => {
    const row = rowsAt(e.point)[0] ?? null
    map!.getCanvas().style.cursor = row === null ? '' : 'pointer'
    setHovered(row)
  })
  map.on('mouseout', () => {
    map!.getCanvas().style.cursor = ''
    setHovered(null)
  })
  map.on('click', e => {
    const rows = rowsAt(e.point)
    if (rows.length) {
      showPopup(e.lngLat, rows, 0)
    } else if (selectedRow !== null) {
      closePopup()
      setSelection(null)
      featureSelectListener?.(null)
    }
  })
}

function setHovered(row: number | null): void {
  if (!map || row === hoveredRow || !map.getSource(SOURCE_ID)) return
  if (hoveredRow !== null) map.setFeatureState({ source: SOURCE_ID, id: hoveredRow }, { hover: false })
  hoveredRow = row
  if (row !== null) map.setFeatureState({ source: SOURCE_ID, id: row }, { hover: true })
  drawSearchRadius(row)
}

// Nearest-neighbor search radius around a hovered point, which the preview data itself
// doesn't show. Not drawn for buffer steps (the preview shows the real buffered geometry),
// nor around lines/polygons, whose search area isn't a circle.
function drawSearchRadius(row: number | null): void {
  const src = map?.getSource(HOVER_SOURCE_ID) as maplibregl.GeoJSONSource | undefined
  if (!src) return
  const radius = Number(lastActiveStep?.max_distance)
  const geom = row === null ? null : featuresByRow[row]?.geometry
  if (lastActiveStep?.type !== 'nearest_neighbor' || !(radius > 0) || geom?.type !== 'Point') {
    src.setData(emptyFC())
    return
  }
  src.setData({ type: 'FeatureCollection', features: [circlePolygon(geom.coordinates as [number, number], radius)] })
}

/** Draw `row` as the selected feature (or nothing). Doesn't touch the popup or the table. */
function setSelection(row: number | null): void {
  selectedRow = row
  const src = map?.getSource(SELECTED_SOURCE_ID) as maplibregl.GeoJSONSource | undefined
  const feature = row === null ? null : featuresByRow[row]
  src?.setData(feature ? { type: 'FeatureCollection', features: [feature] } : emptyFC())
}

function closePopup(): void {
  const p = popup
  popup = null // first, so its close handler knows it was closed on purpose
  p?.remove()
}

/** The popup for the `k`th of the features under a click, with a "k of n" pager when they
 *  overlap; the selection follows the page. */
function showPopup(lngLat: maplibregl.LngLat, rows: number[], k: number): void {
  if (!map) return
  const row = rows[k]
  setSelection(row)
  featureSelectListener?.(row)

  const content = document.createElement('div')
  if (rows.length > 1) {
    const nav = document.createElement('div')
    nav.className = 'preview-popup-nav'
    nav.innerHTML =
      `<button type="button" data-step="-1" aria-label="Previous feature here">‹</button>` +
      `<span>${k + 1} of ${rows.length} here</span>` +
      `<button type="button" data-step="1" aria-label="Next feature here">›</button>`
    nav.addEventListener('click', e => {
      const btn = (e.target as HTMLElement).closest<HTMLButtonElement>('button')
      if (!btn) return
      showPopup(lngLat, rows, (k + Number(btn.dataset['step']) + rows.length) % rows.length)
    })
    content.appendChild(nav)
  }
  const body = document.createElement('div')
  body.className = 'preview-popup-body'
  body.style.maxHeight = `${popupMaxHeight()}px`
  body.style.overflowY = 'auto'
  body.innerHTML = Object.entries(lastRows[row] ?? {})
    .filter(([k]) => k !== '__geojson' && k !== 'geom')
    .map(([k, v]) => `<strong>${esc(k)}:</strong> ${v !== null && v !== undefined ? esc(v) : 'NULL'}`)
    .join('<br>')
  content.appendChild(body)

  if (popup?.isOpen()) {
    popup.setLngLat(lngLat).setDOMContent(content)
    return
  }
  // closeOnClick off: a click elsewhere on the map is handled above (select what's there, or
  // clear), and the built-in one would close the popup that click just opened.
  const p = new maplibregl.Popup({ maxWidth: popupMaxWidth(), closeOnClick: false })
    .setLngLat(lngLat)
    .setDOMContent(content)
    .addTo(map)
  p.on('close', () => {
    if (popup !== p) return
    popup = null
    setSelection(null)
    featureSelectListener?.(null)
  })
  popup = p
}

// Standard destination-point-given-bearing-and-distance formula (geodesic circle, radius in
// meters) — the same math Leaflet's L.circle draws, since MapLibre's circle-radius paint
// property is in screen pixels, not meters, and can't represent a fixed ground distance.
function circlePolygon(centerLngLat: [number, number], radiusMeters: number, steps = 64): GeoJSON.Feature<GeoJSON.Polygon> {
  const [lng, lat] = centerLngLat
  const R = 6371008.8 // mean earth radius, meters
  const latRad = (lat * Math.PI) / 180
  const lngRad = (lng * Math.PI) / 180
  const coords: [number, number][] = []
  for (let i = 0; i <= steps; i++) {
    const brng = (i / steps) * 2 * Math.PI
    const lat2 = Math.asin(
      Math.sin(latRad) * Math.cos(radiusMeters / R) +
      Math.cos(latRad) * Math.sin(radiusMeters / R) * Math.cos(brng)
    )
    const lng2 = lngRad + Math.atan2(
      Math.sin(brng) * Math.sin(radiusMeters / R) * Math.cos(latRad),
      Math.cos(radiusMeters / R) - Math.sin(latRad) * Math.sin(lat2)
    )
    coords.push([(lng2 * 180) / Math.PI, (lat2 * 180) / Math.PI])
  }
  return { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [coords] } }
}

function computeBounds(features: GeoJSON.Feature[]): maplibregl.LngLatBoundsLike | null {
  let west = Infinity, south = Infinity, east = -Infinity, north = -Infinity
  const visit = (coords: any): void => {
    if (typeof coords[0] === 'number') {
      const [x, y] = coords
      if (x < west) west = x
      if (x > east) east = x
      if (y < south) south = y
      if (y > north) north = y
    } else {
      coords.forEach(visit)
    }
  }
  features.forEach(f => {
    const geom = f.geometry as any
    if (geom && 'coordinates' in geom) visit(geom.coordinates)
  })
  return west === Infinity ? null : [[west, south], [east, north]]
}

function setEmptyOverlay(visible: boolean): void {
  const el = document.getElementById('map-empty')
  if (el) (el as HTMLElement).hidden = !visible
}

function parseFeatures(rows: PreviewRow[]): (GeoJSON.Feature | null)[] {
  return rows.map((row, i) => {
    if (!row.__geojson) return null
    try {
      const geom = JSON.parse(row.__geojson as string)
      const properties = Object.fromEntries(
        Object.entries(row).filter(([k]) => k !== '__geojson' && k !== 'geom')
      )
      return geom.type === 'Feature'
        ? { ...geom, id: i, properties }
        : { type: 'Feature', id: i, geometry: geom, properties }
    } catch (e) {
      console.error('Failed to parse geojson', e)
      return null
    }
  })
}

function render(fitBounds: boolean): void {
  if (!map || !map.getSource(SOURCE_ID)) return
  const features = featuresByRow.filter((f): f is GeoJSON.Feature => f !== null)

  // Feature ids are row indexes, so a hover state left over from the previous rows would
  // land on whichever new feature has the same index.
  map.removeFeatureState({ source: SOURCE_ID })
  hoveredRow = null
  drawSearchRadius(null)
  ;(map.getSource(SOURCE_ID) as maplibregl.GeoJSONSource).setData({ type: 'FeatureCollection', features })
  setEmptyOverlay(features.length === 0)

  if (fitBounds && features.length > 0) {
    const bounds = computeBounds(features)
    if (bounds) map.fitBounds(bounds, { padding: 40, maxZoom: 18 })
  }
}

/** Current map viewport as a WGS84 [west, south, east, north] bbox, or null before the map exists. */
export function getMapBounds(): [number, number, number, number] | null {
  if (!map) return null
  const b = map.getBounds()
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
}

/** Fires `cb` after the user finishes a real pan or zoom gesture. */
export function onViewChange(cb: () => void): void {
  map?.on('dragend', cb)
  map?.on('zoomend', cb)
}

/** Fires `cb` with the row index when a feature is selected on the map, or null when the
 *  selection is cleared there (an empty click, closing the popup, a refresh that changed
 *  the selected row). Not fired for selectFeature(). */
export function onFeatureSelect(cb: (row: number | null) => void): void {
  featureSelectListener = cb
}

/** Select row `row` of the current preview from outside the map (the table). With `reveal`,
 *  pans/zooms to its feature when it isn't already in view. */
export function selectFeature(row: number | null, opts?: { reveal?: boolean }): void {
  closePopup()
  setSelection(row)
  const feature = row === null ? null : featuresByRow[row]
  if (!map || !feature || !opts?.reveal) return
  const bounds = computeBounds([feature])
  if (!bounds) return
  const box = maplibregl.LngLatBounds.convert(bounds)
  const view = map.getBounds()
  if (view.contains(box.getSouthWest()) && view.contains(box.getNorthEast())) return
  map.fitBounds(box, { padding: 60, maxZoom: Math.max(map.getZoom(), 14) })
}

/** Hover-highlight row `row`'s feature, as hovering it on the map does (for the table). */
export function hoverFeature(row: number | null): void {
  setHovered(row)
}

export function updateMap(rows: PreviewRow[], activeStep?: any, opts?: { fitBounds?: boolean }): void {
  // A refresh (every edit re-runs the preview) keeps the selection when the selected row came
  // back unchanged at the same index; otherwise it no longer means anything.
  const before = selectedRow === null ? undefined : JSON.stringify(lastRows[selectedRow])
  lastRows = rows ?? []
  lastActiveStep = activeStep ?? null
  featuresByRow = parseFeatures(lastRows)
  render(opts?.fitBounds ?? true)
  if (selectedRow === null) return
  if (before !== undefined && JSON.stringify(lastRows[selectedRow]) === before) {
    setSelection(selectedRow)
  } else {
    closePopup()
    setSelection(null)
    featureSelectListener?.(null)
  }
}
