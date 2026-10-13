export type SourceFormat =
  | 'gpkg' | 'geojson' | 'gml' | 'fgdb' | 'wfs'
  | 'arcgis_rest' | 'oapif' | 'parquet' | 'flatgeobuf' | 'shp' | 'xlsx' | 'csv' | 'json' | 'postgres'

export type JoinPredicate = 'intersects' | 'contains' | 'within'

export type MapFunc = 'uuid' | 'seq' | 'now' | 'today' | 'lat' | 'lon' | 'x' | 'y' | 'mgrs' | 'geohash'
  | 'wkb' | 'wkt' | 'geom_type' | 'area' | 'length'

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

/** A join's fields: {output: source column}, or with match: summarize, {output: an aggregate
 *  over the matches} (its name is the key). */
export type JoinFields = Record<string, string | Omit<Aggregate, 'to'>>

export interface SpatialJoin {
  type: 'spatial_join'
  source: string
  predicate: JoinPredicate
  match?: 'first' | 'all' | 'summarize'
  on_multiple?: 'first' | 'largest_overlap'
  /** {output: source column}; with match: summarize, {output: how to combine the matches}. */
  fields: JoinFields
  branch?: string
  rejects?: string
}

export interface AttributeJoin {
  type: 'attribute_join'
  source: string
  left: string
  right: string
  match?: 'first' | 'summarize'
  fields: JoinFields
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

/** A named SQL expression: a dissolve or combine_lines group key. */
export interface ExprColumn {
  to: string
  expr: string
}

/** How an aggregate combines a column's values without SQL (config.AGGREGATE_HOWS). */
export type AggregateHow = 'any' | 'most_common' | 'from_largest' | 'all' | 'sum' | 'average' | 'min' | 'max' | 'count'

/** One value per group (or chain): `how` to combine the `from` column, or a SQL `expr`.
 *  `to` defaults to `from`. */
export interface Aggregate {
  to?: string
  from?: string
  how?: AggregateHow
  expr?: string
}

export interface Dissolve {
  type: 'dissolve'
  /** A column name, or a named expression to group by. */
  by: (string | ExprColumn)[]
  aggregates?: Aggregate[]
  branch?: string
}

export interface CombineLines {
  type: 'combine_lines'
  /** A column name, or a named expression to group by. */
  by: (string | ExprColumn)[]
  aggregates?: Aggregate[]
  /** Only join lines running the same way (default true). */
  preserve_direction?: boolean
  branch?: string
}

/** Fewer vertices, within `tolerance` of the original (config.Simplify). */
export interface Simplify {
  type: 'simplify'
  tolerance: number
  /** Simplify the polygons together, so the edges neighbours share stay shared. */
  shared_boundaries?: boolean
  /** With shared_boundaries: leave the coverage's outer edge as it is. */
  keep_outer_boundary?: boolean
  branch?: string
}

export interface SnapToGrid {
  type: 'snap_to_grid'
  grid_size: number
  branch?: string
}

/** Polygons made to fit edge to edge: snapped, overlaps removed, gaps filled (config.CleanCoverage). */
export interface CleanCoverage {
  type: 'clean_coverage'
  snap_distance?: number
  max_gap_width?: number
  branch?: string
}

/** Multipart to singleparts: a row per part. */
/** Polygons without their holes, or only the small ones (config.FillHoles). */
export interface FillHoles {
  type: 'fill_holes'
  max_area?: number
  branch?: string
}

/** Small polygons merged into a neighbour (config.EliminateSlivers). */
export interface EliminateSlivers {
  type: 'eliminate_slivers'
  max_area?: number
  /** SQL condition picking the polygons; with max_area, both must hold. */
  where?: string
  merge_into?: 'longest_border' | 'largest'
  branch?: string
}

/** Lines cut where they cross each other, or a source's lines or polygon edges (config.SplitLines). */
export interface SplitLines {
  type: 'split_lines'
  source?: string
  /** A line ending this close to another cuts it there too. */
  tolerance?: number
  branch?: string
}

export interface Explode {
  type: 'explode'
  /** Column to number the parts in (1, 2, …). */
  part_field?: string
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

/** The first row of each set with the same `by` values and/or geometry (config.RemoveDuplicates). */
export interface RemoveDuplicates {
  type: 'remove_duplicates'
  by?: string[]
  geometry?: boolean
  branch?: string
  rejects?: string
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

export interface Attributes {
  type: 'attributes'
  columns: MapItem[]
  drop?: string[]
  branch?: string
}

// One GET per distinct URL (config.HttpLookup): `url` is a template ({column} → its value,
// URL-encoded), or `url_column` names a column holding the whole URL.
export interface HttpLookup {
  type: 'http_lookup'
  url?: string
  url_column?: string
  params?: Record<string, string>
  headers?: Record<string, string>
  records?: string
  fields?: Record<string, string>
  status_field?: string
  max_rate?: number
  concurrency?: number
  timeout?: number
  max_age_hours?: number | null
  preview_limit?: number
  branch?: string
  rejects?: string
}

// Point geometry from two coordinate columns or expressions (config.MakePoints).
export interface MakePoints {
  type: 'make_points'
  x: string
  y: string
  crs?: string
  branch?: string
  rejects?: string
}

export type Step = SpatialJoin | AttributeJoin | NearestNeighbor | Buffer | Centroid | Clip | Erase | Dissolve | CombineLines | SplitLines | Simplify | SnapToGrid | CleanCoverage | FillHoles | EliminateSlivers | Explode | IntersectOverlay | LineOverlay | Filter | RemoveDuplicates | Merge | Snapshot | Attributes | HttpLookup | MakePoints

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
  // Formats that build geometry from columns (x_field/y_field/geom_field), and the ones
  // taking header_row: config.TABULAR_GEOM_FORMATS / HEADER_ROW_FORMATS.
  tabular_geom_formats: SourceFormat[]
  header_row_formats: SourceFormat[]
  aggregate_hows: AggregateHow[]
  numeric_aggregate_hows: AggregateHow[]
  /** The SQL of each `how`, with {col} for the quoted column. */
  aggregate_sql: Record<AggregateHow, string>
}

export interface VersionResponse {
  version: string
  /** Newest release on PyPI; null when unknown (offline, or DUCK_SOUP_NO_UPDATE_CHECK). */
  latest: string | null
  update_available: boolean
  /** Upgrade commands, the most likely install method first. */
  upgrade: string[]
  release_notes: string
}

export interface InspectColumn {
  name: string
  type: string
}

// What a slow source is busy with (sources/prepare.py's prepare_source, sources/remote.py's
// source_progress).
export interface SourceProgress {
  // 'http': an http_lookup step's requests (http_lookup.start_job), `source` naming the step.
  stage: 'queued' | 'download' | 'convert' | 'http'
  source?: string
  bytes?: number
  total?: number | null
  done?: number
  elapsed: number
}

// /api/http_sample: an http_lookup step's request for the first row reaching it.
export interface HttpSampleResponse {
  ok: boolean
  pending?: boolean
  progress?: SourceProgress
  pending_sources?: SourceProgress[]
  url?: string
  status?: number
  body?: string
  truncated?: boolean
  error?: string
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
  // crs_extent_warning in sources/crs.py. null/absent means nothing looked off.
  crs_warning?: string | null
  // Set when the source is read via pyogrio instead of ST_Read (a file over 2 GiB on
  // Windows) — see large_file_reader_note in sources/large_files.py. Informational, not an error.
  reader_note?: string | null
  // A remote file source previews read from a download (sources/prepare.py's download_info): its
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
  // A newer preview from this tab replaced this one before it finished: ignore it.
  stale?: boolean
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
  /** The engine process died (a native crash in DuckDB), see worker.py. */
  crashed?: boolean
  /** Stopped by /api/run/cancel. */
  cancelled?: boolean
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
