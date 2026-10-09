export type SourceFormat =
  | 'gpkg' | 'geojson' | 'gml' | 'fgdb' | 'wfs'
  | 'arcgis_rest' | 'oapif' | 'parquet' | 'flatgeobuf' | 'shp' | 'xlsx' | 'csv' | 'json' | 'postgres'

export type JoinPredicate = 'intersects' | 'contains' | 'within'

export type MapFunc = 'uuid' | 'now' | 'today' | 'lat' | 'lon' | 'mgrs' | 'wkb' | 'area' | 'length'

export interface Source {
  id: string
  format: SourceFormat
  uri: string
  layer?: string
  crs?: string
  geometry?: boolean
  make_valid?: boolean
  force_2d?: boolean
  where?: string
  page_size?: number
  header_row?: boolean
  records?: string
  x_field?: string
  y_field?: string
  geom_field?: string
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
//
// `rejects` (spatial_join, attribute_join, nearest_neighbor, clip, filter): an output layer
// for the rows this step rejects (no match / filtered out / clipped away), which then leave
// the chain — see RejectsMixin in config.py.

export interface SpatialJoin {
  type: 'spatial_join'
  source: string
  predicate: JoinPredicate
  match?: 'first' | 'all'
  on_multiple?: 'first' | 'largest_overlap'
  fields: Record<string, string>
  branch?: string
  rejects?: string
}

export interface AttributeJoin {
  type: 'attribute_join'
  source: string
  left: string
  right: string
  fields: Record<string, string>
  branch?: string
  rejects?: string
}

export interface NearestNeighbor {
  type: 'nearest_neighbor'
  source: string
  max_distance?: number
  distance_field?: string
  fields: Record<string, string>
  branch?: string
  rejects?: string
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
  rejects?: string
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

export interface LineOverlay {
  type: 'line_overlay'
  source: string
  fields: Record<string, string>
  tolerance?: number
  branch?: string
}

export interface Filter {
  type: 'filter'
  where: string
  branch?: string
  rejects?: string
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

export type Step = SpatialJoin | AttributeJoin | NearestNeighbor | Buffer | Centroid | Clip | Erase | Dissolve | IntersectOverlay | LineOverlay | Filter | Merge | Snapshot

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
  mapping?: MapItem[]
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

// What a slow source is busy with (sources.py's prepare_source / source_progress).
export interface SourceProgress {
  stage: 'queued' | 'download' | 'convert'
  source?: string
  bytes?: number
  total?: number | null
  elapsed: number
}

export interface InspectResponse {
  ok: boolean
  // A source still being downloaded / converted to Parquet in the background (see
  // _pending_response in web/app.py): show `progress` and ask again shortly.
  pending?: boolean
  progress?: SourceProgress
  // Every pending source, each with its `source` id (progress is the first of them).
  pending_sources?: SourceProgress[]
  columns?: InspectColumn[]
  // A sanity-check hint when the declared crs looks inconsistent with the sampled
  // coordinate magnitudes (e.g. EPSG:4326 but values are clearly meters) — see
  // crs_extent_warning in sources.py. null/absent means nothing looked off.
  crs_warning?: string | null
  // Set when the source is read via pyogrio instead of ST_Read (a file over 2 GiB on
  // Windows) — see large_file_reader_note in sources.py. Informational, not an error.
  reader_note?: string | null
  // A remote file source previews read from a download (sources.py's download_info): its
  // size, age in seconds, and whether it's also read via a Parquet copy. Null otherwise.
  download?: { bytes: number; age: number; parquet: boolean } | null
  error?: string
}

export interface InspectFileResponse {
  ok: boolean
  // A source still being downloaded / converted to Parquet in the background (see
  // _pending_response in web/app.py): show `progress` and ask again shortly.
  pending?: boolean
  progress?: SourceProgress
  // Every pending source, each with its `source` id (progress is the first of them).
  pending_sources?: SourceProgress[]
  layers?: (string | { value: string; label?: string })[]
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
  // A source still being downloaded / converted to Parquet in the background (see
  // _pending_response in web/app.py): show `progress` and ask again shortly.
  pending?: boolean
  progress?: SourceProgress
  // Every pending source, each with its `source` id (progress is the first of them).
  pending_sources?: SourceProgress[]
  rows?: PreviewRow[]
  error?: string
}

// One layer written by a run (run_config's `written` in engine.py).
export interface WrittenLayer {
  pipeline: string
  layer: string
  rows: number
  kind: 'layer' | 'rejects'
  path: string
}

export interface RunResponse {
  ok: boolean
  output?: string
  log?: string[]
  layers?: WrittenLayer[]
  error?: string
  trace?: string
}

// One view of the SQL plan (Engine.sql_plan in engine.py).
export interface PlanEntry {
  kind: 'source' | 'derived' | 'base' | 'step' | 'split' | 'rejects' | 'branch' | 'layer'
  view: string | null
  sql: string
  title?: string
  id?: string
  step?: number
  layer?: string
}

export interface PlanResponse {
  ok: boolean
  plan?: PlanEntry[]
  error?: string
}

// Row counts after each step (Engine.counts). Step keys are 1-based step numbers.
export interface CountsResponse {
  ok: boolean
  // A source still being downloaded / converted to Parquet in the background (see
  // _pending_response in web/app.py): show `progress` and ask again shortly.
  pending?: boolean
  progress?: SourceProgress
  // Every pending source, each with its `source` id (progress is the first of them).
  pending_sources?: SourceProgress[]
  limit?: number | null
  base?: number
  steps?: Record<string, number>
  rejects?: Record<string, number>
  layers?: number[]
  sources?: Record<string, number>
  error?: string
}

// A run's record (history.py); the list endpoint leaves out `log` and `yaml`.
export interface RunRecord {
  id: string
  config: string
  trigger: 'cli' | 'editor'
  started_at: string
  finished_at?: string
  duration_s: number
  ok: boolean
  error: string | null
  output: string | null
  layers: WrittenLayer[]
  config_hash: string | null
  log?: string[]
  yaml?: string
}

export interface ExportScriptResponse {
  ok: boolean
  script?: string
  filename?: string
  error?: string
}

export interface FileEntry {
  name: string
  path: string
  is_dir: boolean
  size: number | null
  modified: number | null  // epoch seconds
  folder?: string          // search results only: the containing folder
}

export interface FilesResponse {
  current: string
  parent: string | null
  crumbs: { name: string; path: string }[]
  entries: FileEntry[]
}

export interface FilePlace {
  name: string
  path: string
  kind: 'project' | 'data' | 'pipelines' | 'output' | 'home' | 'drive'
}

export interface FileSearchResponse {
  results: FileEntry[]
  truncated: boolean
}

export interface PipelineLoadResponse {
  config: Config
  warning?: string | null
}

export interface ParseYamlResponse {
  ok: boolean
  config?: Config
  error?: string
}
