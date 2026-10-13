import { newClientId, previewConfig } from '../api'
import { fieldPreviewConfig } from '../field-preview'
import { collectPipelineDef } from './pipeline-card'
import { SAMPLE } from './aggregate-row'
import type { Step } from '../types'

// ---- "matches several" warning ----
// A join with match: first keeps one match per feature and quietly drops the rest. This runs
// the steps before the card and the card's own join as match: summarize, counting each
// feature's matches, on the sample data, and says how many features had more than one.
// Only while the card is open, so loading a pipeline doesn't send one request per join.

export function wireMatchWarning(card: HTMLElement, kind: 'spatial_join' | 'attribute_join'): void {
  const msg = card.querySelector<HTMLElement>('[data-match-warning]')
  if (!msg) return
  const client = newClientId()
  let timer: ReturnType<typeof setTimeout> | undefined
  let seq = 0
  const clear = () => { msg.textContent = ''; msg.className = 'expr-validation-msg' }

  const run = async () => {
    const mySeq = ++seq
    if (card.classList.contains('collapsed') || !card.isConnected) return
    const pl = card.closest<HTMLElement>('.pipeline-card')
    const steps = card.parentElement ? [...card.parentElement.querySelectorAll(':scope > .card')] : []
    const own = pl ? collectPipelineDef(pl).steps[steps.indexOf(card)] as unknown as Record<string, unknown> | undefined : undefined
    if (!own || (own['match'] ?? 'first') !== 'first' || !own['source']
      || (kind === 'attribute_join' && !(own['left'] && own['right']))) { clear(); return }
    const counting: Record<string, unknown> = { ...own, match: 'summarize', fields: { _ds_n: { how: 'count' } } }
    delete counting['rejects']
    delete counting['on_multiple']
    const cfg = fieldPreviewConfig(card, [{ to: '_ds_n', from: '_ds_n' }], counting as unknown as Step)
    if (!cfg) { clear(); return }
    try {
      const d = await previewConfig(cfg, 0, SAMPLE, undefined, undefined, false, client, SAMPLE)
      if (mySeq !== seq || d.stale) return
      if (!d.ok || !d.rows?.length) { clear(); return }
      const counts = d.rows.map(r => Number((r as Record<string, unknown>)['_ds_n']))
      const several = counts.filter(n => n > 1).length
      if (!several) { clear(); return }
      const what = kind === 'spatial_join' ? 'features' : 'rows'
      const fix = kind === 'spatial_join'
        ? 'Set match to summarize to combine them, all for a row per match, or pick the largest overlap.'
        : 'Set match to summarize to combine them.'
      msg.className = 'expr-validation-msg caution'
      msg.textContent = `⚠ ${several} of ${counts.length} ${what} in the sample match several in ${String(own['source'])}, and only the first (in its row order) is kept. ${fix}`
    } catch { /* a failed check says nothing; the preview reports real errors */ }
  }
  const schedule = () => {
    if (timer) clearTimeout(timer)
    timer = setTimeout(run, 800)
  }
  for (const k of ['source', 'predicate', 'match', 'left', 'right']) {
    const inp = card.querySelector(`[data-k="${k}"]`)
    inp?.addEventListener('input', schedule)
    inp?.addEventListener('change', schedule)
  }
  // Opening the card checks it.
  let open = !card.classList.contains('collapsed')
  new MutationObserver(() => {
    const nowOpen = !card.classList.contains('collapsed')
    if (nowOpen && !open) schedule()
    open = nowOpen
  }).observe(card, { attributes: true, attributeFilter: ['class'] })
}
