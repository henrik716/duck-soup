export type SourceFormat =
  | 'gpkg' | 'geojson' | 'gml' | 'fgdb' | 'wfs'
  | 'arcgis_rest' | 'parquet' | 'shp' | 'xlsx' | 'csv'

export type JoinPredicate = 'intersects' | 'contains' | 'within'

export type MapFunc = 'uuid' | 'now' | 'today' | 'lon' | 'lat' | 'mgrs' | 'wkb' | 'area' | 'length'

export interface Source {
  id: string
  format: SourceFormat
  uri: string
  layer?: string
  crs?: string
  geometry?: boolean
  make_valid?: boolean
  where?: string
  page_size?: number
}

export interface DerivedSource {
  id: string
  from: string
  where?: string
  buffer?: number
  make_valid?: boolean
}

// `branch` (shared by every step type): apply this step to a named branch
// (from an earlier snapshot step) instead of the main chain. For `snapshot`
// itself, `branch` means "snapshot from this branch" rather than "operate on it".

export interface SpatialJoin {
  type: 'spatial_join'
  source: string
  predicate: JoinPredicate
  match?: 'first' | 'all'
  fields: Record<string, string>
  branch?: string
}

export interface AttributeJoin {
  type: 'attribute_join'
  source: string
  left: string
  right: string
  fields: Record<string, string>
  branch?: string
}

export interface NearestNeighbor {
  type: 'nearest_neighbor'
  source: string
  max_distance?: number
  distance_field?: string
  fields: Record<string, string>
  branch?: string
}

export interface Buffer {
  type: 'buffer'
  distance: number
  branch?: string
}

export interface Centroid {
  type: 'centroid'
  branch?: string
}

export interface Clip {
  type: 'clip'
  source: string
  predicate: JoinPredicate
  branch?: string
}

export interface Erase {
  type: 'erase'
  source: string
  predicate: JoinPredicate
  branch?: string
}

export interface Dissolve {
  type: 'dissolve'
  by: string[]
  branch?: string
}

export interface IntersectOverlay {
  type: 'intersect_overlay'
  source: string
  fields: Record<string, string>
  branch?: string
}

export interface Filter {
  type: 'filter'
  where: string
  branch?: string
}

export interface Merge {
  type: 'merge'
  source: string
  branch?: string
}

export interface Snapshot {
  type: 'snapshot'
  id: string
  branch?: string
}

export type Step = SpatialJoin | AttributeJoin | NearestNeighbor | Buffer | Centroid | Clip | Erase | Dissolve | IntersectOverlay | Filter | Merge | Snapshot

export interface CodeCase {
  value: string
  match?: string
  like?: string
  regex?: string
  is_blank?: boolean
}

export interface CodeList {
  source: string
  case_insensitive?: boolean
  cases?: CodeCase[]
  default?: string
  file?: string
  file_match_col?: string
  file_value_col?: string
}

export interface MapItem {
  to: string
  from?: string
  const?: unknown
  expr?: string
  func?: MapFunc
  codelist?: CodeList
  cast?: string
}

export interface OutputLayer {
  layer: string
  crs: string
  filter?: string
}

export interface PipelineDef {
  name: string
  description?: string
  working_crs?: string
  sources: Source[]
  derived_sources?: DerivedSource[]
  base: string
  steps: Step[]
  mapping: MapItem[]
  layers: OutputLayer[]
}

export interface DatasetMetadata {
  name?: string
  abstract?: string
  origin?: string
  update_frequency?: string
  geometric_quality?: string
  attribute_quality?: string
  access_method_source?: string
  gdpr?: string
}

export interface Config {
  name: string
  description?: string
  output: string
  overwrite?: boolean
  metadata?: DatasetMetadata
  pipelines: PipelineDef[]
}

// API response shapes
export interface MetaResponse {
  formats: SourceFormat[]
  predicates: JoinPredicate[]
  funcs: MapFunc[]
  step_types: string[]
}

export interface InspectColumn {
  name: string
  type: string
}

export interface InspectResponse {
  ok: boolean
  columns?: InspectColumn[]
  error?: string
}

export interface InspectFileResponse {
  ok: boolean
  layers?: string[]
  default_crs?: string
  // Present whenever ok is false — /api/inspect_file never raises, it reports.
  error?: string
}

export interface ValidateResponse {
  ok: boolean
  yaml?: string
  error?: string
}

export interface PreviewRow {
  __geojson?: string
  [key: string]: unknown
}

export interface PreviewResponse {
  ok: boolean
  rows?: PreviewRow[]
  error?: string
}

export interface RunResponse {
  ok: boolean
  output?: string
  log?: string[]
  error?: string
  trace?: string
}

export interface FilesResponse {
  current: string
  parent: string | null
  entries: { name: string; path: string; is_dir: boolean }[]
}

export interface PipelineLoadResponse {
  config: Config
  warning?: string | null
}
