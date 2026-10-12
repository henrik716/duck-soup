import { mkEl, esc, helpLink } from './dom'
import { openOverlay, closeOverlay } from './overlay'
import type { Step } from './types'
import { renderIcons } from './icons'

interface GalleryItem { type: Step['type']; icon: string; name: string; desc: string }
interface GalleryGroup { name: string; what: string; items: GalleryItem[] }

// The gallery's cards, grouped like the step reference (Joins, Geometry, Rows, Attributes),
// each group with a duck name and its plain meaning.
export const STEP_GALLERY: GalleryGroup[] = [
  {
    name: 'Paddling together', what: 'joins: add columns from another source',
    items: [
      { type: 'spatial_join', icon: 'map-pin', name: 'Spatial Join', desc: 'Copy attributes from the feature in another source that intersects, contains or lies within each feature.' },
      { type: 'attribute_join', icon: 'link', name: 'Attribute Join', desc: 'Link attributes from another source using matching column values.' },
      { type: 'nearest_neighbor', icon: 'radar', name: 'Nearest Neighbour', desc: 'Find the closest feature in another source and optionally record the distance.' },
      { type: 'intersect_overlay', icon: 'git-merge', name: 'Intersect Overlay', desc: 'One row per overlapping pair, with the overlap as its geometry.' },
      { type: 'line_overlay', icon: 'split', name: 'Line Overlay', desc: 'Cut lines where another line layer lies on them and copy its attributes, keeping the rest of the network.' },
    ],
  },
  {
    name: 'Shaping up', what: 'geometry: change the shapes',
    items: [
      { type: 'buffer', icon: 'maximize-2', name: 'Buffer Geometry', desc: 'Expand or shrink features by a specified buffer distance.' },
      { type: 'centroid', icon: 'crosshair', name: 'Centroid', desc: 'Replace polygon or line features with their center point.' },
      { type: 'clip', icon: 'scissors', name: 'Clip', desc: 'Intersect base features with a clip mask to keep only overlapping areas.' },
      { type: 'erase', icon: 'eraser', name: 'Erase / Difference', desc: 'Remove geometries that overlap with a mask source.' },
      { type: 'dissolve', icon: 'layers', name: 'Dissolve', desc: 'Merge features that share the same group-by values, with per-group aggregates (sum, count, …).' },
    ],
  },
  {
    name: 'Pecking order', what: 'rows: choose which rows flow on',
    items: [
      { type: 'filter', icon: 'filter', name: 'Filter', desc: 'Drop rows where a SQL condition is false — e.g. reject unmatched joins.' },
      { type: 'merge', icon: 'combine', name: 'Merge / Union', desc: 'Append rows from another source, matched by column name.' },
      { type: 'snapshot', icon: 'camera', name: 'Snapshot', desc: 'Name this step\'s current state so a later step can join back against it — fork mid-pipeline, after some processing.' },
    ],
  },
  {
    name: 'Ruffling feathers', what: 'attributes: calculate, overwrite or drop columns',
    items: [
      { type: 'attributes', icon: 'table-properties', name: 'Update Attributes', desc: 'Add, overwrite or remove columns mid-pipeline, with the mapping\'s rules (SQL, functions, codelists).' },
    ],
  },
]

function galleryHtml(): string {
  return STEP_GALLERY.map(g => `
    <div class="step-gallery-group">
      <h4 class="step-gallery-group-head">${esc(g.name)} <span>(${esc(g.what)})</span></h4>
      <div class="step-gallery-grid">${g.items.map(it => `
        <div class="step-gallery-item" data-type="${it.type}">
          <div class="step-gallery-icon"><i data-lucide="${it.icon}"></i></div>
          <div class="step-gallery-info">
            <span class="name">${esc(it.name)}</span>
            <span class="desc">${esc(it.desc)}</span>
          </div>
        </div>`).join('')}
      </div>
    </div>`).join('')
}

export function openStepGalleryModal(onSelect: (type: Step['type']) => void): void {
  let modal = document.getElementById('stepGalleryModal')
  if (!modal) {
    modal = mkEl('div', { id: 'stepGalleryModal', className: 'modal-overlay' })
    modal.innerHTML = `
      <div class="modal-card step-gallery-modal">
        <div class="modal-header">
          <h3><i data-lucide="git-merge" style="width:16px;height:16px;color:var(--accent)"></i> Select Operation ${helpLink('reference/steps/', '', 'step types')}</h3>
          <button class="mini ghost" id="closeStepModalBtn" aria-label="Close modal"><i data-lucide="x" style="width:16px;height:16px"></i></button>
        </div>
        <div class="modal-body">${galleryHtml()}
        </div>
      </div>`
    document.body.appendChild(modal)

    // Wire close buttons
    modal.querySelector('#closeStepModalBtn')!.addEventListener('click', () => close())
    modal.addEventListener('click', e => {
      if (e.target === modal) close()
    })
  }

  const el = modal
  const close = () => {
    el.classList.remove('show')
    closeOverlay(el)
  }

  // Bind step-gallery item clicks dynamically
  modal.querySelectorAll<HTMLElement>('.step-gallery-item').forEach(item => {
    const newItem = item.cloneNode(true) as HTMLElement
    item.parentNode!.replaceChild(newItem, item)

    // Gallery items are divs; make them reachable and operable from the keyboard.
    newItem.tabIndex = 0
    newItem.setAttribute('role', 'button')
    const pick = () => {
      onSelect(newItem.dataset['type'] as Step['type'])
      close()
    }
    newItem.addEventListener('click', pick)
    newItem.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick() }
    })
  })

  // Show modal
  modal.classList.add('show')
  openOverlay(modal, close)
  renderIcons(modal)
}
