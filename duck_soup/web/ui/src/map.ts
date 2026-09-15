import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { PreviewRow } from './types'
import { esc } from './dom'

let map: L.Map | null = null
let geojsonGroup: L.FeatureGroup | null = null

// Leaflet writes these straight into SVG `stroke`/`fill` presentation attributes, where
// `var(--token)` does not resolve — features silently fall back to the SVG default (black)
// on a dark basemap. So resolve the tokens to literal colors instead. Done lazily and
// memoised: at module-eval time the stylesheet may not be applied yet.
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

export function initMap(): void {
  try {
    // Esri's free (keyless) "Canvas" basemap — the same ArcGIS Online service already
    // used for the satellite layer below, so no new provider/API key to manage. CARTO's
    // basemaps.cartocdn.com previously used here now serves an "API key required"
    // watermark instead of tiles for anonymous use.
    const darkLayer = L.layerGroup([
      L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
        attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ',
        maxZoom: 16,
      }),
      L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}', {
        maxZoom: 16,
      }),
    ])

    const lightLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    })

    const satelliteLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      attribution: 'Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community',
      maxZoom: 18,
    })

    map = L.map('map', {
      center: [59.91, 10.75],
      zoom: 11,
      zoomControl: false,
      layers: [darkLayer]
    })
    L.control.zoom({ position: 'bottomleft' }).addTo(map)

    const baseMaps = {
      "Dark Mode": darkLayer,
      "Light Mode": lightLayer,
      "Satellite": satelliteLayer
    }

    L.control.layers(baseMaps, undefined, { position: 'topright' }).addTo(map)
    geojsonGroup = L.featureGroup().addTo(map)
  } catch (e) {
    console.error('Failed to initialize Map:', e)
  }
}

/** Current map viewport as a WGS84 [west, south, east, north] bbox, or null before the map exists. */
export function getMapBounds(): [number, number, number, number] | null {
  if (!map) return null
  const b = map.getBounds()
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
}

/** Fires `cb` after the user finishes a real pan or zoom gesture. Deliberately not 'moveend':
 *  that also fires for Leaflet's programmatic autoPan (e.g. nudging the view to reveal a
 *  popup that doesn't fully fit), which previously meant clicking a feature could itself
 *  trigger a bbox-mode re-preview a moment later — clearing and redrawing every layer,
 *  including the one whose popup had just opened, so it closed itself. */
export function onViewChange(cb: () => void): void {
  map?.on('dragend', cb)
  map?.on('zoomend', cb)
}

function setEmptyOverlay(visible: boolean): void {
  const el = document.getElementById('map-empty')
  if (el) (el as HTMLElement).hidden = !visible
}

export function updateMap(rows: PreviewRow[], activeStep?: any, opts?: { fitBounds?: boolean }): void {
  if (!map || !geojsonGroup) return
  geojsonGroup.clearLayers()
  if (!rows || rows.length === 0) {
    // Rows with no geometry are a legitimate result (a dissolve drops columns, a CSV source
    // has none at all) — say so rather than leaving the last preview's features on screen
    // or showing a blank map with no explanation.
    setEmptyOverlay(true)
    return
  }

  let hoverCircle: L.Circle | null = null
  const c = colors()

  rows.forEach(row => {
    if (row.__geojson) {
      try {
        const geom = JSON.parse(row.__geojson as string)
        const layer = L.geoJSON(geom, {
          style: { color: c.feature, weight: 3, opacity: 0.8 },
          pointToLayer: (_point, latlng) =>
            L.circleMarker(latlng, {
              radius: 6,
              fillColor: c.feature,
              color: c.ink,
              weight: 1,
              opacity: 1,
              fillOpacity: 0.8,
            }),
        })

        if (activeStep && (activeStep.max_distance || activeStep.distance)) {
          const radiusVal = activeStep.max_distance || activeStep.distance
          const radiusNum = Number(radiusVal)
          if (!isNaN(radiusNum) && radiusNum > 0) {
            layer.on('mouseover', (e: L.LeafletMouseEvent) => {
              if (hoverCircle) hoverCircle.remove()
              hoverCircle = L.circle(e.latlng, {
                radius: radiusNum,
                color: c.accent,
                weight: 1,
                fillColor: c.accent,
                fillOpacity: 0.15,
                dashArray: '4 4'
              }).addTo(map!)
            })
            layer.on('mouseout', () => {
              if (hoverCircle) {
                hoverCircle.remove()
                hoverCircle = null
              }
            })
          }
        }

        const tooltipContent = Object.entries(row)
          .filter(([k]) => k !== '__geojson' && k !== 'geom')
          .map(([k, v]) => `<strong>${esc(k)}:</strong> ${v !== null ? esc(v) : 'NULL'}`)
          .join('<br>')

        if (tooltipContent) layer.bindPopup(tooltipContent)
        layer.addTo(geojsonGroup!)
      } catch (e) {
        console.error('Failed to parse geojson', e)
      }
    }
  })

  const drawn = geojsonGroup.getLayers().length
  setEmptyOverlay(drawn === 0)

  if ((opts?.fitBounds ?? true) && drawn > 0) {
    map.fitBounds(geojsonGroup.getBounds())
  }
}
