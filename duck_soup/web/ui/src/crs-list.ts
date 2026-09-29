import { esc } from './dom'
import type { ComboOptionDef } from './combo'

export interface CrsOption {
  value: string
  name: string
}

// A curated shortlist for the crs field's searchable combo — not an EPSG database (this repo
// has no PROJ/pyproj lookup exposed to the frontend; see sources.py's module docstring for why
// GDAL's own database isn't queried live for this either). Covers WGS84/Web Mercator, every
// ETRS89 UTM zone across Europe (the projected CRS most of this repo's own test data and
// example pipeline use — see pipelines/test.yaml's `working_crs: EPSG:25833`), a few national
// grids, and NAD83/GDA94/NZGD2000 for elsewhere. Anything not listed can still be typed
// directly — this only adds search-by-name, it never restricts the field.
export const COMMON_CRS: CrsOption[] = [
  { value: 'EPSG:4326', name: 'WGS 84 (lat/lon)' },
  { value: 'EPSG:3857', name: 'WGS 84 / Pseudo-Mercator (Web Mercator)' },
  { value: 'EPSG:4258', name: 'ETRS89 (lat/lon, Europe)' },
  { value: 'EPSG:3035', name: 'ETRS89-extended / LAEA Europe' },
  { value: 'EPSG:25828', name: 'ETRS89 / UTM zone 28N' },
  { value: 'EPSG:25829', name: 'ETRS89 / UTM zone 29N' },
  { value: 'EPSG:25830', name: 'ETRS89 / UTM zone 30N' },
  { value: 'EPSG:25831', name: 'ETRS89 / UTM zone 31N' },
  { value: 'EPSG:25832', name: 'ETRS89 / UTM zone 32N (Norway/Germany/Denmark)' },
  { value: 'EPSG:25833', name: 'ETRS89 / UTM zone 33N (Norway)' },
  { value: 'EPSG:25834', name: 'ETRS89 / UTM zone 34N' },
  { value: 'EPSG:25835', name: 'ETRS89 / UTM zone 35N (Norway)' },
  { value: 'EPSG:25836', name: 'ETRS89 / UTM zone 36N' },
  { value: 'EPSG:25837', name: 'ETRS89 / UTM zone 37N' },
  { value: 'EPSG:25838', name: 'ETRS89 / UTM zone 38N' },
  { value: 'EPSG:3006', name: 'SWEREF99 TM (Sweden)' },
  { value: 'EPSG:3067', name: 'ETRS89 / TM35FIN (Finland)' },
  { value: 'EPSG:27700', name: 'OSGB36 / British National Grid (UK)' },
  { value: 'EPSG:2154', name: 'RGF93 / Lambert-93 (France)' },
  { value: 'EPSG:4269', name: 'NAD83 (lat/lon, US/Canada)' },
  { value: 'EPSG:5070', name: 'NAD83 / Conus Albers (US)' },
  { value: 'EPSG:2193', name: 'NZGD2000 / New Zealand Transverse Mercator' },
  { value: 'EPSG:3577', name: 'GDA94 / Australian Albers' },
]

export const CRS_COMBO_OPTIONS: ComboOptionDef[] = COMMON_CRS.map(c => ({
  value: c.value,
  label: `<span style="font-family:var(--mono);">${esc(c.value)}</span> `
    + `<span style="font-size:10px;color:var(--muted);margin-left:6px;">${esc(c.name)}</span>`,
  search: c.name,
}))
