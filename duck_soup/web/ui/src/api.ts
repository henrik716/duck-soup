import type {
  Config,
  FilesResponse,
  InspectFileResponse,
  InspectResponse,
  MetaResponse,
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
  return (await fetch('/api/pipelines/' + encodeURIComponent(name))).json()
}

export async function savePipeline(name: string, config: Config): Promise<Response> {
  return fetch('/api/pipelines/' + encodeURIComponent(name), {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ config }),
  })
}

export async function validateConfig(config: Config): Promise<ValidateResponse> {
  const r = await fetch('/api/validate', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ config }),
  })
  return r.json()
}

export async function previewConfig(config: Config, pipeline_idx = 0, limit = 50, preview_until_step?: number): Promise<PreviewResponse> {
  const r = await fetch('/api/preview', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ config, pipeline_idx, limit, preview_until_step }),
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
    if ((e as Error).name === 'AbortError')
      return { ok: false, error: 'Run timed out after 5 minutes', log: [] } as RunResponse
    throw e
  } finally {
    clearTimeout(timer)
  }
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

export async function uploadFile(file: File): Promise<{ ok: boolean; path?: string; error?: string }> {
  const formData = new FormData()
  formData.append('file', file)
  const r = await fetch('/api/upload', {
    method: 'POST',
    body: formData,
  })
  return r.json()
}
