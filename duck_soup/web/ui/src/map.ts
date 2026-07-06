import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { PreviewRow } from './types'

let map: L.Map | null = null
let geojsonGroup: L.FeatureGroup | null = null

export function initMap(): void {
  try {
    const darkLayer = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
      subdomains: 'abcd',
      maxZoom: 20,
    })

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

export function updateMap(rows: PreviewRow[], activeStep?: any): void {
  if (!map || !geojsonGroup) return
  geojsonGroup.clearLayers()
  if (!rows || rows.length === 0) return

  let hoverCircle: L.Circle | null = null

  rows.forEach(row => {
    if (row.__geojson) {
      try {
        const geom = JSON.parse(row.__geojson as string)
        const layer = L.geoJSON(geom, {
          style: { color: 'var(--spatial)', weight: 3, opacity: 0.8 },
          pointToLayer: (_point, latlng) =>
            L.circleMarker(latlng, {
              radius: 6,
              fillColor: 'var(--spatial)',
              color: 'var(--ink)',
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
                color: 'var(--accent)',
                weight: 1,
                fillColor: 'var(--accent)',
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
          .map(([k, v]) => `<strong>${k}:</strong> ${v !== null ? v : 'NULL'}`)
          .join('<br>')

        if (tooltipContent) layer.bindPopup(tooltipContent)
        layer.addTo(geojsonGroup!)
      } catch (e) {
        console.error('Failed to parse geojson', e)
      }
    }
  })

  if (geojsonGroup.getLayers().length > 0) {
    map.fitBounds(geojsonGroup.getBounds())
  }
}
