import { createIcons } from 'lucide'
import { mkEl, appIcons } from './dom'
import type { Step } from './types'

export function openStepGalleryModal(onSelect: (type: Step['type']) => void): void {
  let modal = document.getElementById('stepGalleryModal')
  if (!modal) {
    modal = mkEl('div', { id: 'stepGalleryModal', className: 'modal-overlay' })
    modal.innerHTML = `
      <div class="modal-card step-gallery-modal">
        <div class="modal-header">
          <h3><i data-lucide="git-merge" style="width:16px;height:16px;color:var(--accent)"></i> Select Operation</h3>
          <button class="mini ghost" id="closeStepModalBtn" aria-label="Close modal"><i data-lucide="x" style="width:16px;height:16px"></i></button>
        </div>
        <div class="modal-body">
          <div class="step-gallery-grid">
            <div class="step-gallery-item" data-type="spatial_join">
              <div class="step-gallery-icon"><i data-lucide="map-pin"></i></div>
              <div class="step-gallery-info">
                <span class="name">Spatial Join</span>
                <span class="desc">Copy attributes from the nearest or intersecting feature in another source.</span>
              </div>
            </div>
            <div class="step-gallery-item" data-type="attribute_join">
              <div class="step-gallery-icon"><i data-lucide="link"></i></div>
              <div class="step-gallery-info">
                <span class="name">Attribute Join</span>
                <span class="desc">Link attributes from another source using matching column values.</span>
              </div>
            </div>
            <div class="step-gallery-item" data-type="nearest_neighbor">
              <div class="step-gallery-icon"><i data-lucide="radar"></i></div>
              <div class="step-gallery-info">
                <span class="name">Nearest Neighbour</span>
                <span class="desc">Find the closest feature in another source and optionally record the distance.</span>
              </div>
            </div>
            <div class="step-gallery-item" data-type="buffer">
              <div class="step-gallery-icon"><i data-lucide="maximize-2"></i></div>
              <div class="step-gallery-info">
                <span class="name">Buffer Geometry</span>
                <span class="desc">Expand or shrink features by a specified buffer distance.</span>
              </div>
            </div>
            <div class="step-gallery-item" data-type="centroid">
              <div class="step-gallery-icon"><i data-lucide="crosshair"></i></div>
              <div class="step-gallery-info">
                <span class="name">Centroid</span>
                <span class="desc">Replace polygon or line features with their center point.</span>
              </div>
            </div>
            <div class="step-gallery-item" data-type="clip">
              <div class="step-gallery-icon"><i data-lucide="scissors"></i></div>
              <div class="step-gallery-info">
                <span class="name">Clip</span>
                <span class="desc">Intersect base features with a clip mask to keep only overlapping areas.</span>
              </div>
            </div>
            <div class="step-gallery-item" data-type="erase">
              <div class="step-gallery-icon"><i data-lucide="eraser"></i></div>
              <div class="step-gallery-info">
                <span class="name">Erase / Difference</span>
                <span class="desc">Remove geometries that overlap with a mask source.</span>
              </div>
            </div>
            <div class="step-gallery-item" data-type="dissolve">
              <div class="step-gallery-icon"><i data-lucide="layers"></i></div>
              <div class="step-gallery-info">
                <span class="name">Dissolve</span>
                <span class="desc">Merge geometries that share the same group-by attribute value.</span>
              </div>
            </div>
            <div class="step-gallery-item" data-type="intersect_overlay">
              <div class="step-gallery-icon"><i data-lucide="git-merge"></i></div>
              <div class="step-gallery-info">
                <span class="name">Intersect Overlay</span>
                <span class="desc">Perform full overlap analysis, keeping intersecting intersections.</span>
              </div>
            </div>
            <div class="step-gallery-item" data-type="filter">
              <div class="step-gallery-icon"><i data-lucide="filter"></i></div>
              <div class="step-gallery-info">
                <span class="name">Filter</span>
                <span class="desc">Drop rows where a SQL condition is false — e.g. reject unmatched joins.</span>
              </div>
            </div>
            <div class="step-gallery-item" data-type="merge">
              <div class="step-gallery-icon"><i data-lucide="combine"></i></div>
              <div class="step-gallery-info">
                <span class="name">Merge / Union</span>
                <span class="desc">Append rows from another source, matched by column name.</span>
              </div>
            </div>
            <div class="step-gallery-item" data-type="snapshot">
              <div class="step-gallery-icon"><i data-lucide="camera"></i></div>
              <div class="step-gallery-info">
                <span class="name">Snapshot</span>
                <span class="desc">Name this step's current state so a later step can join back against it — fork mid-pipeline, after some processing.</span>
              </div>
            </div>
          </div>
        </div>
      </div>`
    document.body.appendChild(modal)

    // Wire close buttons
    modal.querySelector('#closeStepModalBtn')!.addEventListener('click', () => {
      modal!.classList.remove('show')
    })
    modal.addEventListener('click', e => {
      if (e.target === modal) modal!.classList.remove('show')
    })
  }

  // Bind step-gallery item clicks dynamically
  modal.querySelectorAll<HTMLElement>('.step-gallery-item').forEach(item => {
    const newItem = item.cloneNode(true) as HTMLElement
    item.parentNode!.replaceChild(newItem, item)
    newItem.addEventListener('click', () => {
      const type = newItem.dataset['type'] as Step['type']
      onSelect(type)
      modal!.classList.remove('show')
    })
  })

  // Show modal
  modal.classList.add('show')
  createIcons({ icons: appIcons })
}
