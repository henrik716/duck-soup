// ---- shared app state ----
// The editor's vocabulary, from /api/meta (filled in before any card is drawn).
export const META = {
  formats: [] as string[], predicates: [] as string[], funcs: [] as string[], step_types: [] as string[],
  tabular_geom_formats: [] as string[], header_row_formats: [] as string[],
  aggregate_hows: [] as string[], numeric_aggregate_hows: [] as string[],
  aggregate_sql: {} as Record<string, string>,
}
export const SOURCE_SCHEMAS: Record<string, { name: string; type: string }[]> = {}

// Map file extensions to format values
export const EXT_FORMAT: Record<string, string> = {
  gpkg: 'gpkg',
  geojson: 'geojson', json: 'geojson',
  gml: 'gml', xml: 'gml',
  gdb: 'fgdb',
  parquet: 'parquet',
  fgb: 'flatgeobuf',
  shp: 'shp',
  xlsx: 'xlsx', xls: 'xlsx',
  csv: 'csv',
}
