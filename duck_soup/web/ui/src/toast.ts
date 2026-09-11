import { createIcons, CheckCircle, AlertTriangle, Info, X } from 'lucide'
import { qs, mkEl, esc } from './dom'

export interface ToastOptions {
  /** Optional inline action, e.g. an Undo affordance on a delete. */
  action?: { label: string; onClick: () => void }
  /** Override the auto-dismiss delay, in ms. Pass 0 to require a manual dismiss. */
  durationMs?: number
}

// Errors need longer than a confirmation: you have to actually read them.
const DEFAULT_MS = { info: 4000, ok: 4000, bad: 9000 } as const

interface Live {
  el: HTMLElement
  msg: string
  count: number
  timer: number
  remaining: number
  startedAt: number
}

const live: Live[] = []

function dismiss(entry: Live): void {
  clearTimeout(entry.timer)
  const i = live.indexOf(entry)
  if (i >= 0) live.splice(i, 1)
  entry.el.classList.remove('show')
  setTimeout(() => entry.el.remove(), 300)
}

function startTimer(entry: Live): void {
  if (entry.remaining <= 0) return
  entry.startedAt = Date.now()
  entry.timer = window.setTimeout(() => dismiss(entry), entry.remaining)
}

function pauseTimer(entry: Live): void {
  if (entry.remaining <= 0) return
  clearTimeout(entry.timer)
  entry.remaining -= Date.now() - entry.startedAt
}

export function showToast(msg: string, type: 'info' | 'ok' | 'bad' = 'info', opts: ToastOptions = {}): void {
  const container = qs<HTMLElement>('#toast-container')
  if (!container) return

  // Dedupe: a multi-file upload used to fire one identical toast per file. Collapse
  // repeats into a count instead. Toasts carrying an action are never deduped — each one
  // refers to a distinct thing to undo.
  if (!opts.action) {
    const existing = live.find(l => l.msg === msg)
    if (existing) {
      existing.count++
      const counter = existing.el.querySelector('.toast-count')
      if (counter) counter.textContent = `×${existing.count}`
      else existing.el.querySelector('.toast-msg')?.insertAdjacentHTML('afterend', `<span class="toast-count">×${existing.count}</span>`)
      pauseTimer(existing)
      existing.remaining = opts.durationMs ?? DEFAULT_MS[type]
      startTimer(existing)
      return
    }
  }

  const t = mkEl('div', { className: `toast ${type}` })
  // Errors interrupt; confirmations wait their turn.
  t.setAttribute('role', type === 'bad' ? 'alert' : 'status')
  t.setAttribute('aria-live', type === 'bad' ? 'assertive' : 'polite')

  const icon = type === 'ok' ? 'check-circle' : type === 'bad' ? 'alert-triangle' : 'info'
  t.innerHTML =
    `<i data-lucide="${icon}" style="width:16px;height:16px;flex-shrink:0"></i>` +
    `<span class="toast-msg">${esc(msg)}</span>` +
    (opts.action ? `<button type="button" class="toast-action">${esc(opts.action.label)}</button>` : '') +
    `<button type="button" class="toast-close" aria-label="Dismiss notification"><i data-lucide="x" style="width:13px;height:13px"></i></button>`

  container.appendChild(t)
  createIcons({ icons: { CheckCircle, AlertTriangle, Info, X } })

  const entry: Live = {
    el: t,
    msg,
    count: 1,
    timer: 0,
    remaining: opts.durationMs ?? DEFAULT_MS[type],
    startedAt: Date.now(),
  }
  live.push(entry)

  t.querySelector('.toast-close')!.addEventListener('click', () => dismiss(entry))
  if (opts.action) {
    t.querySelector('.toast-action')!.addEventListener('click', () => {
      opts.action!.onClick()
      dismiss(entry)
    })
  }

  // Don't time out a message while the user is reading or reaching for its action.
  t.addEventListener('mouseenter', () => pauseTimer(entry))
  t.addEventListener('mouseleave', () => startTimer(entry))
  t.addEventListener('focusin', () => pauseTimer(entry))
  t.addEventListener('focusout', () => startTimer(entry))

  setTimeout(() => t.classList.add('show'), 50)
  startTimer(entry)
}
