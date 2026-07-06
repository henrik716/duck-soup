import { createIcons, CheckCircle, AlertTriangle, Info } from 'lucide'
import { qs, mkEl } from './dom'

export function showToast(msg: string, type: 'info' | 'ok' | 'bad' = 'info'): void {
  const container = qs<HTMLElement>('#toast-container')!
  const t = mkEl('div', { className: `toast ${type}` })
  const icon = type === 'ok' ? 'check-circle' : type === 'bad' ? 'alert-triangle' : 'info'
  t.innerHTML = `<i data-lucide="${icon}" style="width:16px;height:16px"></i> <span>${msg}</span>`
  container.appendChild(t)
  createIcons({ icons: { CheckCircle, AlertTriangle, Info } })
  setTimeout(() => t.classList.add('show'), 50)
  setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 300) }, 4000)
}
