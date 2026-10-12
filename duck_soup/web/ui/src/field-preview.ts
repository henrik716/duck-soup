import { collectPipelineDef } from './cards/pipeline-card'
import type { Config, MapItem, Step } from './types'

/**
 * A throwaway preview Config for checking one field's SQL against real sample data via
 * /api/preview (the live checks, the expression builder): the pipeline as currently drafted
 * in the form, mapped to `mapping` (empty: every column).
 *
 * For a field inside a step card it runs only the steps before that card, the rows the
 * field's SQL actually sees, plus `ownStep` when given (e.g. the card's own step cut down to
 * the part before the field). Null without a pipeline card or base source yet.
 */
export function fieldPreviewConfig(el: Element, mapping: MapItem[], ownStep?: Step): Config | null {
  const plCard = el.closest<HTMLElement>('.pipeline-card')
  if (!plCard) return null
  const pdef = collectPipelineDef(plCard)
  if (!pdef.base) return null
  let steps = pdef.steps
  const stepCard = el.closest('.pl-steps > .card')
  if (stepCard?.parentElement) {
    // collectPipelineDef reads one step per `.pl-steps > .card`, in DOM order.
    steps = steps.slice(0, Array.from(stepCard.parentElement.children).indexOf(stepCard))
    if (ownStep) steps = [...steps, ownStep]
  }
  return {
    name: 'field_preview',
    output: 'preview.gpkg',
    pipelines: [{
      ...pdef,
      steps,
      mapping,
      layers: [{ layer: 'preview', crs: pdef.working_crs || 'EPSG:25833' }],
    }],
  }
}
