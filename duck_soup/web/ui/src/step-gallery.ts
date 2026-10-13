import { mkEl, esc, helpLink } from './dom'
import { openOverlay, closeOverlay } from './overlay'
import type { Step } from './types'
import { renderIcons } from './icons'
import { matchesQuery, type SearchableStep } from './gallery-search'

interface GalleryItem extends SearchableStep { type: Step['type']; icon: string }
interface GalleryGroup { name: string; what: string; items: GalleryItem[] }

// The gallery's cards, grouped like the step reference (Joins, Geometry, Rows, Attributes),
// each group with a duck name and its plain meaning. `aliases` are what the search also
// finds a card by: the FME transformers and QGIS tools it stands in for (see the FME
// guide, docs/guides/fme.md), and everyday words for it.
export const STEP_GALLERY: GalleryGroup[] = [
  {
    name: 'Paddling together', what: 'joins: add columns from another source',
    items: [
      { type: 'spatial_join', icon: 'map-pin', name: 'Spatial Join', desc: 'Copy attributes from the feature in another source that intersects, contains or lies within each feature.',
        aliases: ['PointOnAreaOverlayer', 'SpatialRelator', 'SpatialFilter', 'Join attributes by location', 'Count points in polygon', 'point in polygon'] },
      { type: 'attribute_join', icon: 'link', name: 'Attribute Join', desc: 'Link attributes from another source using matching column values.',
        aliases: ['FeatureMerger', 'DatabaseJoiner', 'Joiner', 'Join attributes by field value', 'lookup', 'vlookup'] },
      { type: 'nearest_neighbor', icon: 'radar', name: 'Nearest Neighbour', desc: 'Find the closest feature in another source and optionally record the distance.',
        aliases: ['NeighborFinder', 'Join attributes by nearest', 'Distance to nearest hub', 'closest', 'nearest neighbor'] },
      { type: 'intersect_overlay', icon: 'git-merge', name: 'Intersect Overlay', desc: 'One row per overlapping pair, with the overlap as its geometry.',
        aliases: ['AreaOnAreaOverlayer', 'LineOnAreaOverlayer', 'Intersection', 'intersect'] },
      { type: 'line_overlay', icon: 'split', name: 'Line Overlay', desc: 'Cut lines where another line layer lies on them and copy its attributes, keeping the rest of the network.',
        aliases: ['LineOnLineOverlayer', 'identity', 'linear referencing'] },
      { type: 'http_lookup', icon: 'globe', name: 'HTTP Lookup', desc: 'Call a web API for each row (e.g. an address search) and add values from its JSON response.',
        aliases: ['HTTPCaller', 'geocode', 'geocoder', 'address search', 'REST', 'API', 'web service', 'JSONFlattener'] },
    ],
  },
  {
    name: 'Shaping up', what: 'geometry: change the shapes',
    items: [
      { type: 'make_points', icon: 'map-pinned', name: 'Make Points', desc: 'Make point geometry from two coordinate columns, such as the longitude and latitude a geocoding lookup returned.',
        aliases: ['VertexCreator', 'Create points layer from table', 'xy', 'coordinates', 'lat lon', 'longitude latitude'] },
      { type: 'buffer', icon: 'maximize-2', name: 'Buffer Geometry', desc: 'Expand or shrink features by a specified buffer distance.',
        aliases: ['Bufferer', 'offset', 'grow', 'shrink'] },
      { type: 'centroid', icon: 'crosshair', name: 'Centroid', desc: 'Replace polygon or line features with their center point.',
        aliases: ['CenterPointReplacer', 'Centroids', 'centre point', 'center point'] },
      { type: 'clip', icon: 'scissors', name: 'Clip', desc: 'Intersect base features with a clip mask to keep only overlapping areas.',
        aliases: ['Clipper', 'cut', 'mask'] },
      { type: 'erase', icon: 'eraser', name: 'Erase / Difference', desc: 'Remove geometries that overlap with a mask source.',
        aliases: ['Clipper outside', 'Difference', 'cut out', 'subtract'] },
      { type: 'dissolve', icon: 'layers', name: 'Dissolve', desc: 'Merge features that share the same group-by values, with per-group aggregates (sum, count, …).',
        aliases: ['Dissolver', 'Aggregator', 'union', 'group by', 'aggregate'] },
      { type: 'combine_lines', icon: 'spline', name: 'Combine Lines', desc: 'Join lines that meet end to end into longer lines, one per connected chain with the same group-by values.',
        aliases: ['LineCombiner', 'LineJoiner', 'Merge lines', 'Dissolve lines', 'line merge'] },
      { type: 'split_lines', icon: 'scissors-line-dashed', name: 'Split Lines', desc: 'Cut lines where they cross or touch each other, so every crossing becomes a shared end point (noding).',
        aliases: ['Intersector', 'TopologyBuilder', 'Split with lines', 'Split lines at intersections', 'node', 'noding', 'planarize', 'network'] },
      { type: 'simplify', icon: 'waypoints', name: 'Simplify / Generalize', desc: 'Remove vertices within a tolerance, one feature at a time or keeping the boundaries neighbouring polygons share.',
        aliases: ['Generalizer', 'Simplify', 'Douglas Peucker', 'Visvalingam', 'generalise', 'thin vertices'] },
      { type: 'snap_to_grid', icon: 'grid-3x3', name: 'Snap to Grid', desc: 'Round every vertex to a grid, e.g. to centimetres, keeping the geometry valid.',
        aliases: ['CoordinateRounder', 'Snap points to grid', 'round coordinates', 'precision'] },
      { type: 'clean_coverage', icon: 'puzzle', name: 'Clean Coverage', desc: 'Make polygons fit edge to edge: snap nearly matching edges, remove overlaps, fill small gaps.',
        aliases: ['Snapper', 'AreaGapAndOverlapCleaner', 'Remove overlaps', 'Fill gaps', 'topology', 'v.clean'] },
      { type: 'fill_holes', icon: 'paint-bucket', name: 'Fill Holes', desc: 'Remove the holes in polygons, all of them or only those smaller than an area.',
        aliases: ['DonutHoleExtractor', 'Delete holes', 'interior rings', 'donut'] },
      { type: 'eliminate_slivers', icon: 'merge', name: 'Eliminate Slivers', desc: 'Merge polygons smaller than an area into the neighbour they share the longest border with.',
        aliases: ['Eliminate selected polygons', 'SliverRemover', 'small polygons', 'merge into neighbour', 'generalize'] },
      { type: 'explode', icon: 'ungroup', name: 'Multipart to Singleparts', desc: 'Split multipolygons, multilines and collections into a row per part.',
        aliases: ['Deaggregator', 'explode', 'split multipart', 'singlepart'] },
    ],
  },
  {
    name: 'Pecking order', what: 'rows: keep some, add more, or set a copy aside',
    items: [
      { type: 'filter', icon: 'filter', name: 'Filter', desc: 'Drop rows where a SQL condition is false — e.g. reject unmatched joins.',
        aliases: ['Tester', 'TestFilter', 'AttributeFilter', 'Extract by attribute', 'Select by expression', 'where', 'condition'] },
      { type: 'remove_duplicates', icon: 'copy-minus', name: 'Remove Duplicates', desc: 'Keep the first row of each set with the same column values and/or the same geometry.',
        aliases: ['DuplicateFilter', 'Matcher', 'Delete duplicate geometries', 'Delete duplicates by attribute', 'unique', 'distinct'] },
      { type: 'merge', icon: 'combine', name: 'Merge / Union', desc: 'Append rows from another source, matched by column name.',
        aliases: ['Merge vector layers', 'append', 'union all', 'combine sources'] },
      { type: 'snapshot', icon: 'camera', name: 'Snapshot', desc: 'Name this step\'s current state so a later step can join back against it — fork mid-pipeline, after some processing.',
        aliases: ['branch', 'fork', 'copy', 'self join'] },
    ],
  },
  {
    name: 'Ruffling feathers', what: 'attributes: calculate, overwrite or drop columns',
    items: [
      { type: 'attributes', icon: 'table-properties', name: 'Update Attributes', desc: 'Add, overwrite or remove columns mid-pipeline, with the mapping\'s rules (SQL, functions, codelists).',
        aliases: ['AttributeCreator', 'AttributeManager', 'AttributeRenamer', 'AttributeRemover', 'AttributeKeeper', 'ExpressionEvaluator', 'StringConcatenator', 'Field calculator', 'Refactor fields', 'calculate', 'rename', 'replace geometry'] },
    ],
  },
]

function galleryHtml(): string {
  return `
    <div class="step-gallery-search">
      <i data-lucide="search" style="width:14px;height:14px"></i>
      <input type="search" id="stepGallerySearch" placeholder="Search steps, or a QGIS / FME tool (Delete holes, Deaggregator, …)" aria-label="Search steps" autocomplete="off" spellcheck="false">
    </div>
    <p class="step-gallery-empty" hidden>No step matches. The <a href="#" data-gallery-clear>whole list</a> is also grouped below by what it does.</p>` +
    STEP_GALLERY.map(g => `
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

/** Show the cards matching `query` (and only the groups with any); returns how many match. */
function applySearch(modal: HTMLElement, query: string): number {
  let shown = 0
  modal.querySelectorAll<HTMLElement>('.step-gallery-group').forEach((groupEl, gi) => {
    const group = STEP_GALLERY[gi]!
    let inGroup = 0
    groupEl.querySelectorAll<HTMLElement>('.step-gallery-item').forEach((itemEl, ii) => {
      const hit = matchesQuery(group.items[ii]!, query, `${group.name} ${group.what}`)
      itemEl.hidden = !hit
      if (hit) inGroup++
    })
    groupEl.hidden = inGroup === 0
    shown += inGroup
  })
  modal.querySelector<HTMLElement>('.step-gallery-empty')!.hidden = shown > 0
  return shown
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
  const visibleItems = () => [...el.querySelectorAll<HTMLElement>('.step-gallery-item')].filter(i => !i.hidden)

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

  // The search, fresh on every open (cloned like the items, to drop the last open's handlers).
  const oldSearch = el.querySelector<HTMLInputElement>('#stepGallerySearch')!
  const search = oldSearch.cloneNode(true) as HTMLInputElement
  oldSearch.replaceWith(search)
  search.value = ''
  applySearch(el, '')
  search.addEventListener('input', () => applySearch(el, search.value))
  search.addEventListener('keydown', e => {
    // Enter adds the first card that matches; the arrow key moves on to the cards.
    if (e.key === 'Enter') {
      e.preventDefault()
      const first = search.value.trim() ? visibleItems()[0] : undefined
      first?.click()
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      visibleItems()[0]?.focus()
    }
  })
  el.querySelector('[data-gallery-clear]')?.addEventListener('click', e => {
    e.preventDefault()
    search.value = ''
    applySearch(el, '')
    search.focus()
  })

  // Show modal
  modal.classList.add('show')
  openOverlay(modal, close)
  renderIcons(modal)
  setTimeout(() => search.focus(), 0)
}
