import type {
  Config,
  ExportScriptResponse,
  FilesResponse,
  InspectFileResponse,
  InspectResponse,
  MetaResponse,
  ParseYamlResponse,
  PipelineLoadResponse,
  PreviewResponse,
  RunResponse,
  Source,
  ValidateResponse,
} from './types'

const JSON_HEADERS = { 'content-type': 'application/json' }

export async function fetchMeta(): Promise<MetaResponse> {
  return (await fetch('/api/meta')).json()
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

export async function previewConfig(
  config: Config,
  pipeline_idx = 0,
  limit = 1000,
  preview_until_step?: number,
  bbox?: [number, number, number, number],
): Promise<PreviewResponse> {
  const r = await fetch('/api/preview', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ config, pipeline_idx, limit, preview_until_step, bbox }),
  })
  return r.json()
}

export async function runConfig(config: Config): Promise<RunResponse> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 5 * 60 * 1000)
  try {
    const r = await fetch('/api/run', {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ config }),
      signal: controller.signal,
    })
    return r.json()
  } catch (e) {
    // "Timed out" overstated what happened: aborting only drops *our* side of the request.
    // The server has no cancellation path, so the run carries on — and it holds the global
    // DUCKDB_LOCK while it does, which is why previews stay frozen until it finishes.
    if ((e as Error).name === 'AbortError')
      return {
        ok: false,
        error: 'Stopped waiting after 5 minutes — the run is still going on the server, '
             + 'and previews will stay frozen until it finishes.',
        log: [],
      } as RunResponse
    throw e
  } finally {
    clearTimeout(timer)
  }
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
  if (!r.ok) throw new Error('Failed to load directory')
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
