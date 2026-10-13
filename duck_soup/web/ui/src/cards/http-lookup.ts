import { mkEl, esc } from '../dom'
import { comboField, wireCombos } from '../combo'
import { collectColumnsBeforeStep, formatPendingSources, PENDING_RETRY_MS } from '../schema'
import { renderValidationMsg } from '../validation'
import { fieldPreviewConfig } from '../field-preview'
import { httpSample } from '../api'
import { atPath, guessRecords, leafPaths, templateColumns } from '../http-paths'
import { renderIcons } from '../icons'
import { mutate } from '../history'
import type { HttpLookup } from '../types'

// ---- http_lookup step card (config.HttpLookup) ----
// Called from step-card.ts: the body markup, its wiring, and reading it back for
// collectPipelineDef. The URL, records, status field and the request settings are plain
// `data-k` inputs, which stepCard hydrates and wires itself; the lists (fields, params,
// headers) and the test request live here.

const HEAD_STYLE = 'font-family:var(--mono);font-size:10px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase;display:block;margin-bottom:6px'
const PLUS_ICON = '<i data-lucide="plus" style="width:12px;height:12px"></i>'

export const HTTP_LOOKUP_HINT = 'Sends a GET request per row (one per distinct URL) and adds values from the JSON response as columns, e.g. geocoding addresses with an address-search API. Responses are cached, so the preview only waits for new URLs.'

// The card's lists, each row two inputs: fields (path → output name), query parameters and
// headers (name = value).
type ListKind = 'hfields' | 'hparams' | 'hheaders'
const LISTS: Record<ListKind, { left: string; right: string; arrow: string; title: string }> = {
  hfields: { left: 'path in the hit, e.g. location.lat', right: 'output_name', arrow: 'arrow-right', title: 'Remove this field' },
  hparams: { left: 'name', right: "SQL, e.g. kommunenr or 'json'", arrow: '', title: 'Remove this parameter' },
  hheaders: { left: 'header, e.g. Authorization', right: 'value, ${ENV_VAR} read when sent', arrow: '', title: 'Remove this header' },
}

export function httpLookupBodyHtml(): string {
  const list = (kind: ListKind, head: string, button: string, extra = '') => `
    <div style="margin-top:10px">
      <span class="head" style="${HEAD_STYLE}">${head}</span>${extra}
      <div data-${kind}></div>
      <button class="mini ghost" data-add-${kind} style="margin-top:6px">${PLUS_ICON} ${button}</button>
    </div>`
  return `
    <div class="row" style="margin-top:8px">
      <label class="field grow" title="{column} is replaced by that column's value, URL-encoded. A row with an empty value there makes no request.">URL&nbsp;<span style="font-weight:normal">({column} inserts a column's value)</span>
        <input data-k="url" placeholder="https://api.example.com/geocode?q={address}&limit=1" spellcheck="false">
        <div class="expr-validation-msg" data-url-validation></div></label>
    </div>
    <div class="row" style="margin-top:4px">
      <label class="field grow" title="Instead of a URL template: a column holding each row's whole URL, e.g. made in an Update Attributes step. Sent as it is.">or a column with the whole URL${comboField('data-k="url_column" data-from-list="1"', '', [], '— none —')}</label>
    </div>
    <div class="row" style="margin-top:8px;align-items:flex-end">
      <label class="field grow" title="Dot path to the list of hits in the response; the first hit is used. Empty: the whole response is the hit.">hits at (records)<input data-k="records" placeholder="results" spellcheck="false"></label>
      <label class="field grow" title="Output column for the HTTP status code (0 when there was no response)">status code column (optional)<input data-k="status_field" placeholder="http_status"></label>
      <button class="mini" data-http-test title="Send the request for the first row reaching this step and show the response, to pick fields from"><i data-lucide="play" style="width:12px;height:12px"></i> test request</button>
    </div>
    <div class="http-sample" data-http-sample hidden></div>
    ${list('hfields', 'fields&nbsp; path in the hit → output name', 'field', '<p class="hint" style="margin:0 0 6px">Values come out as text. Use test request to see the response and click a value to add it.</p>')}
    <details style="margin-top:10px">
      <summary class="head" style="${HEAD_STYLE};cursor:pointer;display:list-item">request settings</summary>
      ${list('hparams', 'query parameters&nbsp; <span style="font-weight:normal;text-transform:none;letter-spacing:0">(added to the URL, URL-encoded)</span>', 'parameter')}
      ${list('hheaders', 'headers', 'header')}
      <div class="row" style="margin-top:10px">
        <label class="field grow" title="Most requests per second, to be polite to the API">requests / second<input data-k="max_rate" type="number" min="0.1" step="any" placeholder="10"></label>
        <label class="field grow" title="Requests in flight at once">at once<input data-k="concurrency" type="number" min="1" max="32" step="1" placeholder="4"></label>
        <label class="field grow" title="Seconds to wait for each response">timeout (s)<input data-k="timeout" type="number" min="1" step="any" placeholder="30"></label>
      </div>
      <div class="row" style="margin-top:6px">
        <label class="field grow" title="A run asks again for a URL whose cached response is older than this. Empty: never. Previews use any cached response, however old.">run: ask again after (hours)<input data-k="max_age_hours" type="number" min="0" step="any" placeholder="24"></label>
        <label class="field grow" title="Previews look up only the first N rows reaching this step, so the editor stays quick; the rest continue without a response.">preview: look up first N rows<input data-k="preview_limit" type="number" min="1" step="1" placeholder="100"></label>
      </div>
    </details>`
}

function addListRow(c: HTMLElement, kind: ListKind, left = '', right = '', syncFn: () => void): HTMLElement | null {
  const box = c.querySelector<HTMLElement>(`[data-${kind}]`)
  if (!box) return null
  const spec = LISTS[kind]
  const r = mkEl('div', { className: 'kv' })
  r.style.marginTop = '6px'
  r.innerHTML = `<input data-hl placeholder="${esc(spec.left)}" value="${esc(left)}" spellcheck="false">
    <span class="arrow">${spec.arrow ? `<i data-lucide="${spec.arrow}" style="width:12px;height:12px;color:var(--muted)"></i>` : '='}</span>
    <input ${kind === 'hfields' ? 'data-fo ' : ''}data-hr placeholder="${esc(spec.right)}" value="${esc(right)}" spellcheck="false">
    <button class="mini danger ghost" data-hdel aria-label="${spec.title}"><i data-lucide="x" style="width:12px;height:12px"></i></button>`
  const leftInp = r.querySelector<HTMLInputElement>('[data-hl]')!
  const rightInp = r.querySelector<HTMLInputElement>('[data-hr]')!
  // A field's output name follows its path's last part until the user types their own.
  if (kind === 'hfields') {
    if (!right && left) { rightInp.value = lastSegment(left); rightInp.dataset['autofilled'] = '1' }
    leftInp.addEventListener('input', () => {
      if (!rightInp.value.trim() || rightInp.dataset['autofilled'] === '1') {
        rightInp.value = lastSegment(leftInp.value)
        rightInp.dataset['autofilled'] = '1'
      }
    })
    rightInp.addEventListener('input', () => { delete rightInp.dataset['autofilled'] })
  }
  r.querySelector('[data-hdel]')!.addEventListener('click', () => { r.remove(); syncFn() })
  r.querySelectorAll('input').forEach(i => i.addEventListener('input', syncFn))
  box.appendChild(r)
  renderIcons(r)
  return r
}

function lastSegment(path: string): string {
  const segs = path.split('.').map(s => s.trim()).filter(s => s && !/^\d+$/.test(s))
  return (segs[segs.length - 1] || '').replace(/[^A-Za-z0-9_æøåÆØÅ]/g, '_')
}

function readList(c: Element, kind: ListKind): Record<string, string> {
  const out: Record<string, string> = {}
  c.querySelectorAll(`[data-${kind}] .kv`).forEach(r => {
    const left = r.querySelector<HTMLInputElement>('[data-hl]')!.value.trim()
    const right = r.querySelector<HTMLInputElement>('[data-hr]')!.value.trim()
    if (!left || !right) return
    // fields are {output: path}; params and headers {name: value}.
    if (kind === 'hfields') out[right] = left
    else out[left] = right
  })
  return out
}

/** The step as the card has it, for collectPipelineDef (the `data-k` scalars included). */
export function readHttpLookup(c: Element): Partial<HttpLookup> {
  const v = (k: string) => c.querySelector<HTMLInputElement>(`[data-k="${k}"]`)?.value.trim() ?? ''
  const st: Partial<HttpLookup> = {}
  if (v('url_column') && !v('url')) st.url_column = v('url_column')
  else st.url = v('url')
  const params = readList(c, 'hparams')
  if (Object.keys(params).length && st.url !== undefined) st.params = params
  const headers = readList(c, 'hheaders')
  if (Object.keys(headers).length) st.headers = headers
  if (v('records')) st.records = v('records')
  st.fields = readList(c, 'hfields')
  if (v('status_field')) st.status_field = v('status_field')
  const num = (k: 'max_rate' | 'concurrency' | 'timeout' | 'preview_limit') => {
    const n = parseFloat(v(k))
    if (Number.isFinite(n) && n > 0) st[k] = n
  }
  num('max_rate'); num('concurrency'); num('timeout'); num('preview_limit')
  // Empty means the default (24 h): "never ask again" is only expressible in YAML (null).
  const age = parseFloat(v('max_age_hours'))
  if (Number.isFinite(age) && age >= 0) st.max_age_hours = age
  return st
}

export function wireHttpLookup(c: HTMLElement, st: Partial<HttpLookup>, syncFn: () => void): void {
  for (const kind of Object.keys(LISTS) as ListKind[]) {
    c.querySelector(`[data-add-${kind}]`)?.addEventListener('click', e => {
      e.stopPropagation()
      addListRow(c, kind, '', '', syncFn)?.querySelector('input')?.focus()
      syncFn()
    })
  }
  Object.entries(st.fields || {}).forEach(([out, path]) => addListRow(c, 'hfields', path, out, syncFn))
  Object.entries(st.params || {}).forEach(([name, expr]) => addListRow(c, 'hparams', name, expr, syncFn))
  Object.entries(st.headers || {}).forEach(([name, value]) => addListRow(c, 'hheaders', name, value, syncFn))
  if (st.max_age_hours === null) {
    c.querySelector<HTMLInputElement>('[data-k="max_age_hours"]')!.placeholder = 'never'
  }
  wireCombos(c)
  wireUrlCheck(c)
  wireTestRequest(c, syncFn)
}

/** Names the URL's {placeholders} that aren't columns reaching this step. */
function wireUrlCheck(c: HTMLElement): void {
  const urlInp = c.querySelector<HTMLInputElement>('[data-k="url"]')!
  const colInp = c.querySelector<HTMLInputElement>('[data-k="url_column"]')!
  const msg = c.querySelector<HTMLElement>('[data-url-validation]')!
  const check = () => {
    const url = urlInp.value.trim()
    if (url && colInp.value.trim()) {
      renderValidationMsg(msg, 'bad', 'Use either a URL template or a URL column, not both.')
      return
    }
    const scope = c.closest('.pipeline-card')
    const known = scope ? collectColumnsBeforeStep(scope, c).map(col => col.name.toLowerCase()) : []
    const unknown = templateColumns(url).filter(name => !name || (known.length && !known.includes(name.toLowerCase())))
    if (unknown.length) {
      renderValidationMsg(msg, 'bad', unknown.some(n => !n)
        ? '{} needs a column name, e.g. {address}'
        : `No column ${unknown.map(n => `'${n}'`).join(', ')} reaches this step.`)
    } else {
      renderValidationMsg(msg, 'idle')
    }
  }
  urlInp.addEventListener('input', check)
  colInp.addEventListener('input', check)
  colInp.addEventListener('change', check)
  urlInp.addEventListener('blur', check)
}

/** "test request": the first row's request and response, with its values as chips that add
 *  a field, and the list of hits filled in when it's empty and the response has one. */
function wireTestRequest(c: HTMLElement, syncFn: () => void): void {
  const btn = c.querySelector<HTMLButtonElement>('[data-http-test]')!
  const panel = c.querySelector<HTMLElement>('[data-http-sample]')!
  btn.addEventListener('click', async e => {
    e.stopPropagation()
    // The steps before this card, then this step as the card has it now.
    const cfg = fieldPreviewConfig(c, [], { type: 'http_lookup', ...readHttpLookup(c) } as HttpLookup)
    if (!cfg) { show('<p class="hint">Set the base source first.</p>'); return }
    const step = cfg.pipelines[0].steps.length
    btn.disabled = true
    show('<p class="hint">requesting…</p>')
    try {
      let d = await httpSample(cfg, 0, step)
      while (d.pending && d.progress) {
        show(`<p class="hint">${esc(formatPendingSources(d.pending_sources?.length ? d.pending_sources : [d.progress]))}</p>`)
        await new Promise(resolve => setTimeout(resolve, PENDING_RETRY_MS))
        d = await httpSample(cfg, 0, step)
      }
      if (!d.ok) { show(`<p class="expr-validation-msg bad">${esc(d.error?.split('\n')[0] || 'test request failed')}</p>`); return }
      renderSample(d.url ?? '', d.status ?? 0, d.body ?? '', d.truncated ?? false)
    } catch (err) {
      show(`<p class="expr-validation-msg bad">${esc(String(err))}</p>`)
    } finally {
      btn.disabled = false
    }
  })

  function show(html: string): void {
    panel.hidden = false
    panel.innerHTML = html
  }

  function renderSample(url: string, status: number, body: string, truncated: boolean): void {
    let doc: unknown
    try { doc = JSON.parse(body) } catch { doc = undefined }
    const recordsInp = c.querySelector<HTMLInputElement>('[data-k="records"]')!
    if (doc !== undefined && !recordsInp.value.trim()) {
      const guess = guessRecords(doc)
      if (guess) { recordsInp.value = guess; syncFn() }
    }
    const records = recordsInp.value.trim()
    const hit = doc === undefined ? undefined : records ? atPath(doc, `${records}.0`) : doc
    const leaves = hit === undefined ? [] : leafPaths(hit)
    const ok = status >= 200 && status < 300
    show(`
      <div class="hint" style="margin-top:8px;word-break:break-all"><b style="color:${ok ? 'var(--ok, inherit)' : 'var(--warn-hover)'}">${status || 'no response'}</b> ${esc(url)}</div>
      ${leaves.length ? `<div class="hint" style="margin-top:6px">${records ? `first hit in <code>${esc(records)}</code>` : 'response'} · click a value to add it as a field</div>
        <div class="st-pool-list" style="margin-top:4px">${leaves.map(l => `
          <span class="st-pool-chip"><button type="button" data-path="${esc(l.path)}" title="${esc(l.sample)}">${esc(l.path)} <span style="color:var(--muted)">${esc(l.sample.length > 24 ? l.sample.slice(0, 24) + '…' : l.sample)}</span></button></span>`).join('')}
        </div>` : doc !== undefined && records ? `<p class="hint" style="margin-top:6px">No hit at <code>${esc(records)}</code> for this row.</p>` : ''}
      <details style="margin-top:6px"><summary class="hint" style="cursor:pointer">raw response${truncated ? ' (cut short)' : ''}</summary>
        <pre class="http-sample-body">${esc(doc !== undefined ? JSON.stringify(doc, null, 2) : body)}</pre></details>`)
    panel.querySelectorAll<HTMLButtonElement>('[data-path]').forEach(b => b.addEventListener('click', () => {
      const path = b.dataset['path'] ?? ''
      const exists = [...c.querySelectorAll<HTMLInputElement>('[data-hfields] [data-hl]')].some(i => i.value.trim() === path)
      if (exists) return
      mutate('add http field')
      addListRow(c, 'hfields', path, '', syncFn)
      syncFn()
    }))
  }
}
