import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { PreviewRow } from './types'
import { esc } from './dom'

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

const SOURCE_ID = 'preview-data'
const HOVER_SOURCE_ID = 'preview-hover'
const PREVIEW_LAYER_IDS = ['preview-fill', 'preview-line', 'preview-point']

const STYLES = {
  dark: 'https://tiles.openfreemap.org/styles/dark',
  light: 'https://tiles.openfreemap.org/styles/positron',
} as const
type StyleKey = keyof typeof STYLES
let currentStyleKey: StyleKey = 'dark'

// MapLibre paint properties are style-spec values (color strings/expressions), not CSS —
// they can't reference `var(--token)`. So resolve the tokens to literal colors instead. Done
// lazily and memoised: at module-eval time the stylesheet may not be applied yet.
let _colors: { feature: string; accent: string; ink: string } | null = null
function colors(): { feature: string; accent: string; ink: string } {
  if (_colors) return _colors
  const cs = getComputedStyle(document.documentElement)
  const read = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback
  _colors = {
    // Its own dedicated token, not --spatial — that variable is shared with several other UI
    // elements (step icons, lineage nodes, badges, syntax highlighting) that shouldn't recolor
    // along with the map's drawn features.
    feature: read('--map-feature', '#ff5ec4'),
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
      btn.className = key === currentStyleKey ? 'active' : ''
      btn.addEventListener('click', () => {
        switchBaseStyle(key)
        this.container?.querySelectorAll('button').forEach(b => b.classList.remove('active'))
        btn.classList.add('active')
      })
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
  map.setStyle(STYLES[key])
  map.once('style.load', () => setupPreviewLayers())
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

    new ResizeObserver(() => map?.resize()).observe(document.getElementById('map-container')!)
  } catch (e) {
    console.error('Failed to initialize Map:', e)
  }
}

function setupPreviewLayers(): void {
  if (!map) return
  const c = colors()

  if (!map.getSource(SOURCE_ID)) {
    map.addSource(SOURCE_ID, { type: 'geojson', data: emptyFC() })
  }
  if (!map.getLayer('preview-fill')) {
    map.addLayer({
      id: 'preview-fill', type: 'fill', source: SOURCE_ID,
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': c.feature, 'fill-opacity': 0.25 },
    })
  }
  if (!map.getLayer('preview-line')) {
    map.addLayer({
      id: 'preview-line', type: 'line', source: SOURCE_ID,
      filter: ['in', ['geometry-type'], ['literal', ['LineString', 'Polygon']]],
      paint: { 'line-color': c.feature, 'line-width': 3, 'line-opacity': 0.8 },
    })
  }
  if (!map.getLayer('preview-point')) {
    map.addLayer({
      id: 'preview-point', type: 'circle', source: SOURCE_ID,
      filter: ['==', ['geometry-type'], 'Point'],
      paint: {
        'circle-radius': 6,
        'circle-color': c.feature,
        'circle-stroke-color': c.ink,
        'circle-stroke-width': 1,
        'circle-opacity': 0.8,
      },
    })
  }

  if (!map.getSource(HOVER_SOURCE_ID)) {
    map.addSource(HOVER_SOURCE_ID, { type: 'geojson', data: emptyFC() })
  }
  if (!map.getLayer('hover-fill')) {
    map.addLayer({
      id: 'hover-fill', type: 'fill', source: HOVER_SOURCE_ID,
      paint: { 'fill-color': c.accent, 'fill-opacity': 0.15 },
    })
  }
  if (!map.getLayer('hover-line')) {
    map.addLayer({
      id: 'hover-line', type: 'line', source: HOVER_SOURCE_ID,
      paint: { 'line-color': c.accent, 'line-width': 1, 'line-dasharray': [4, 4] },
    })
  }

  wireInteractions()
  // Redraw whatever was last shown, but never re-fit the view — this runs after every style
  // load (including a basemap switch), and a basemap switch shouldn't recenter the map.
  render(false)
}

function wireInteractions(): void {
  if (!map) return
  PREVIEW_LAYER_IDS.forEach(id => {
    map!.on('click', id, (e: maplibregl.MapLayerMouseEvent) => {
      const f = e.features?.[0]
      if (!f) return
      const props = f.properties ?? {}
      const html = Object.entries(props)
        .map(([k, v]) => `<strong>${esc(k)}:</strong> ${v !== null && v !== undefined ? esc(v) : 'NULL'}`)
        .join('<br>')
      if (!html) return
      new maplibregl.Popup({ maxWidth: 'none' })
        .setLngLat(e.lngLat)
        .setHTML(`<div class="preview-popup-body" style="max-height:${popupMaxHeight()}px;overflow-y:auto">${html}</div>`)
        .addTo(map!)
    })
    map!.on('mouseenter', id, () => { map!.getCanvas().style.cursor = 'pointer' })
    map!.on('mouseleave', id, () => { map!.getCanvas().style.cursor = '' })

    map!.on('mouseenter', id, (e: maplibregl.MapLayerMouseEvent) => {
      const radiusVal = lastActiveStep?.max_distance || lastActiveStep?.distance
      const radiusNum = Number(radiusVal)
      if (!radiusVal || isNaN(radiusNum) || radiusNum <= 0) return
      const poly = circlePolygon([e.lngLat.lng, e.lngLat.lat], radiusNum)
      const src = map!.getSource(HOVER_SOURCE_ID) as maplibregl.GeoJSONSource
      src?.setData({ type: 'FeatureCollection', features: [poly] })
    })
    map!.on('mouseleave', id, () => {
      const src = map!.getSource(HOVER_SOURCE_ID) as maplibregl.GeoJSONSource
      src?.setData(emptyFC())
    })
  })
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

function render(fitBounds: boolean): void {
  if (!map || !map.getSource(SOURCE_ID)) return
  const features: GeoJSON.Feature[] = []
  lastRows.forEach(row => {
    if (!row.__geojson) return
    try {
      const geom = JSON.parse(row.__geojson as string)
      const properties = Object.fromEntries(
        Object.entries(row).filter(([k]) => k !== '__geojson' && k !== 'geom')
      )
      const feature: GeoJSON.Feature = geom.type === 'Feature'
        ? { ...geom, properties }
        : { type: 'Feature', geometry: geom, properties }
      features.push(feature)
    } catch (e) {
      console.error('Failed to parse geojson', e)
    }
  })

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

export function updateMap(rows: PreviewRow[], activeStep?: any, opts?: { fitBounds?: boolean }): void {
  lastRows = rows ?? []
  lastActiveStep = activeStep ?? null
  render(opts?.fitBounds ?? true)
}
