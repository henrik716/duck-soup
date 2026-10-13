import { describe, expect, it } from 'vitest'
import { sharedRequests } from './request-share'

interface Answer { ok: boolean; pending?: boolean; n: number }

/** A request counter, with a clock the test moves. */
function setup(ttl = 1000) {
  let calls = 0
  let t = 0
  const shared = sharedRequests<Answer>(a => a.ok && !a.pending, ttl, () => t)
  const run = (answer: Omit<Answer, 'n'> = { ok: true }) => () => Promise.resolve({ ...answer, n: ++calls })
  return { shared, run, calls: () => calls, tick: (ms: number) => { t += ms } }
}

describe('sharedRequests', () => {
  it('shares a request in flight between callers with the same key', async () => {
    const { shared, run, calls } = setup()
    const [a, b] = await Promise.all([shared.get('k', run()), shared.get('k', run())])
    expect(calls()).toBe(1)
    expect(a).toEqual(b)
    expect(a).not.toBe(b)  // each caller gets its own copy
  })

  it('keeps a good answer for the ttl, then asks again', async () => {
    const { shared, run, calls, tick } = setup(1000)
    await shared.get('k', run())
    tick(999)
    await shared.get('k', run())
    expect(calls()).toBe(1)
    tick(1)
    await shared.get('k', run())
    expect(calls()).toBe(2)
  })

  it('asks again after an error, a pending answer or a failed request', async () => {
    const { shared, run, calls } = setup()
    await shared.get('err', run({ ok: false }))
    await shared.get('err', run({ ok: false }))
    await shared.get('pending', run({ ok: false, pending: true }))
    await shared.get('pending', run({ ok: true }))
    expect(calls()).toBe(4)
    await expect(shared.get('boom', () => Promise.reject(new Error('down')))).rejects.toThrow('down')
    expect((await shared.get('boom', run())).ok).toBe(true)
  })

  it('keeps different keys apart, and forgets everything on clear', async () => {
    const { shared, run, calls } = setup()
    await shared.get('a', run())
    await shared.get('b', run())
    expect(calls()).toBe(2)
    shared.clear()
    await shared.get('a', run())
    expect(calls()).toBe(3)
  })
})
