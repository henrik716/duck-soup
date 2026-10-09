import type {
  Config,
  CountsResponse,
  ExportScriptResponse,
  FilePlace,
  FileSearchResponse,
  FilesResponse,
  InspectFileResponse,
  InspectResponse,
  MetaResponse,
  ParseYamlResponse,
  PipelineLoadResponse,
  PlanResponse,
  PreviewResponse,
  RunRecord,
  RunResponse,
  Source,
  ValidateResponse,
  VersionResponse,
} from './types'

const JSON_HEADERS = { 'content-type': 'application/json' }

/** The JSON body of an `{ ok, error }` endpoint, or an `{ ok: false, error }` saying what
 *  went wrong when the request itself failed — e.g. a 404 from a server started before the
 *  endpoint existed, which would otherwise surface as a bare "unknown error". */
async function okJson<T extends { ok: boolean; error?: string }>(r: Response, what: string): Promise<T> {
  if (r.ok) return r.json()
  let detail = r.statusText
  try {
    const body = await r.json()
    detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
  } catch { /* not JSON */ }
  const hint = r.status === 404 ? ` — the server has no ${what}; restart it so it runs the current version` : ''
  return { ok: false, error: `${r.status} ${detail}${hint}` } as T
}

export async function fetchMeta(): Promise<MetaResponse> {
  return (await fetch('/api/meta')).json()
}

/** Null when the server predates /api/version (or the request failed): no badge then. */
export async function fetchVersion(): Promise<VersionResponse | null> {
  try {
    const r = await fetch('/api/version')
    return r.ok ? await r.json() : null
  } catch {
    return null
  }
}

export async function fetchPipelineNames(): Promise<string[]> {
  return (await fetch('/api/pipelines')).json()
}

export async function fetchPipeline(name: string): Promise<PipelineLoadResponse> {
  const r = await fetch('/api/pipelines/' + encodeURIComponent(name))
  if (!r.ok) {
    let detail = r.statusText
    try { detail = (await r.json()).detail ?? detail } catch { /* body wasn't JSON */ }
    throw new Error(`failed to load '${name}': ${detail}`)
  }
  return r.json()
}

export async function savePipeline(name: string, config: Config): Promise<Response> {
  return fetch('/api/pipelines/' + encodeURIComponent(name), {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ config }),
  })
}

export async function parseYaml(yamlText: string): Promise<ParseYamlResponse> {
  const r = await fetch('/api/parse_yaml', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ yaml: yamlText }),
  })
  return r.json()
}

export async function validateConfig(config: Config): Promise<ValidateResponse> {
  const r = await fetch('/api/validate', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ config }),
  })
  return r.json()
}

// Identifies this editor tab to the server, which drops a tab's previews once a newer one
// arrives instead of computing every one of them in turn (see _preview_ticket in app.py).
// crypto.randomUUID needs a secure context, which an editor opened over plain http on a
// LAN address isn't.
export function newClientId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
}
const CLIENT_ID = newClientId()

export async function previewConfig(
  config: Config,
  pipeline_idx = 0,
  limit = 1000,
  preview_until_step?: number,
  bbox?: [number, number, number, number],
  rejects = false,
  /** Who's asking: previews from the same client replace each other (default: this tab's
   *  main preview). A live check passes its own, so it doesn't stop the main preview. */
  client = CLIENT_ID,
): Promise<PreviewResponse> {
  const r = await fetch('/api/preview', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ config, pipeline_idx, limit, preview_until_step, bbox, rejects, client }),
  })
  return r.json()
}

export async function fetchPlan(config: Config, pipeline_idx = 0): Promise<PlanResponse> {
  const r = await fetch('/api/plan', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ config, pipeline_idx }),
  })
  return okJson(r, '/api/plan')
}

/** `limit` null counts all the data; a number counts the first N base features. */
export async function fetchCounts(
  config: Config,
  pipeline_idx: number,
  limit: number | null,
  bbox?: [number, number, number, number],
): Promise<CountsResponse> {
  const r = await fetch('/api/counts', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ config, pipeline_idx, limit, bbox }),
  })
  return okJson(r, '/api/counts')
}

export async function fetchRuns(name: string): Promise<RunRecord[]> {
  const r = await fetch('/api/runs/' + encodeURIComponent(name))
  if (!r.ok) return []
  return (await r.json()).runs
}

export async function fetchRun(name: string, id: string): Promise<RunRecord | null> {
  const r = await fetch(`/api/runs/${encodeURIComponent(name)}/${encodeURIComponent(id)}`)
  return r.ok ? r.json() : null
}

/** `name` keys the run history: the saved config's name. */
// No timeout: a run takes as long as it takes, in its own engine process on the server
// (previews keep working meanwhile), and cancelRun stops it.
export async function runConfig(config: Config, name?: string): Promise<RunResponse> {
  const r = await fetch('/api/run', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ config, name }),
  })
  return r.json()
}

/** Stops the run in progress; its /api/run request then answers `cancelled: true`. */
export async function cancelRun(): Promise<void> {
  await fetch('/api/run/cancel', { method: 'POST' })
}

export async function exportScript(config: Config, name: string): Promise<ExportScriptResponse> {
  const r = await fetch('/api/export_script', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ config, name }),
  })
  return r.json()
}

export async function inspectSource(source: Source): Promise<InspectResponse> {
  const r = await fetch('/api/inspect', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ source }),
  })
  return r.json()
}

/** Download a remote file source again; answers pending while it runs (like inspect). */
export async function refreshSource(source: Source): Promise<InspectResponse> {
  const r = await fetch('/api/refresh_source', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ source }),
  })
  return r.json()
}

export async function inspectFile(uri: string, format: string): Promise<InspectFileResponse> {
  const r = await fetch('/api/inspect_file', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ uri, format }),
  })
  return r.json()
}

export async function fetchFiles(subpath: string): Promise<FilesResponse> {
  const r = await fetch(`/api/files?subpath=${encodeURIComponent(subpath)}`)
  if (!r.ok) {
    const detail = await r.json().then(b => b?.detail, () => null)
    throw new Error(typeof detail === 'string' ? detail : 'Failed to load directory')
  }
  return r.json()
}

export async function fetchFilePlaces(): Promise<FilePlace[]> {
  const r = await fetch('/api/files/places')
  if (!r.ok) return []
  return (await r.json()).places
}

export async function searchFiles(q: string, subpath: string, signal?: AbortSignal): Promise<FileSearchResponse> {
  const params = new URLSearchParams({ q, subpath })
  const r = await fetch(`/api/files/search?${params}`, { signal })
  if (!r.ok) throw new Error('Search failed')
  return r.json()
}

export async function uploadFile(file: File, relpath?: string): Promise<{ ok: boolean; path?: string; error?: string }> {
  const formData = new FormData()
  formData.append('file', file)
  if (relpath) formData.append('relpath', relpath)
  const r = await fetch('/api/upload', {
    method: 'POST',
    body: formData,
  })
  return r.json()
}
