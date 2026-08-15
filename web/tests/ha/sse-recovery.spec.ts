import { expect, test } from '@playwright/test'

type SseEvent = { id: number; event: string; data: Record<string, unknown> }

test('real ChatService survives browser refresh/network loss without duplicate messages', async ({ page, context }) => {
  test.setTimeout(60_000)
  await page.goto('/probe/health')
  const requestId = crypto.randomUUID()
  const message = `browser-ha-${Date.now()}`

  const first = await page.evaluate(async ({ requestId, message }) => {
    const controller = new AbortController()
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_request_id: requestId, message }),
      signal: controller.signal,
    })
    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let conversationId = ''
    let lastId = 0
    let content = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const blocks = buffer.split('\n\n')
      buffer = blocks.pop() ?? ''
      for (const block of blocks) {
        const id = Number(block.match(/^id:\s*(\d+)/m)?.[1] ?? 0)
        const event = block.match(/^event:\s*(\w+)/m)?.[1]
        const raw = block.match(/^data:\s*(.*)$/m)?.[1]
        if (!raw) continue
        const data = JSON.parse(raw)
        if (data.conversation_id) conversationId = data.conversation_id
        if (id) lastId = id
        if (event === 'token') content += data.text
        if (event === 'token') {
          controller.abort()
          return { conversationId, lastId, content }
        }
      }
    }
    throw new Error('stream ended before first token')
  }, { requestId, message })

  await context.setOffline(true)
  await page.waitForTimeout(150)
  await context.setOffline(false)

  const tail = await page.evaluate(async ({ requestId, lastId }) => {
    const response = await fetch(`/api/chat/runs/by-request/${requestId}/events`, {
      headers: { 'Last-Event-ID': String(lastId) },
    })
    const text = await response.text()
    const events: SseEvent[] = []
    for (const block of text.split('\n\n')) {
      const raw = block.match(/^data:\s*(.*)$/m)?.[1]
      if (!raw) continue
      events.push({
        id: Number(block.match(/^id:\s*(\d+)/m)?.[1] ?? 0),
        event: block.match(/^event:\s*(\w+)/m)?.[1] ?? 'message',
        data: JSON.parse(raw),
      })
    }
    return events
  }, { requestId, lastId: first.lastId })

  const resumed = tail.filter((event) => event.event === 'token').map((event) => String(event.data.text)).join('')
  expect(first.content + resumed).toBe(`ECHO:${message}`)
  expect(tail.filter((event) => event.event === 'done')).toHaveLength(1)
  expect(new Set(tail.map((event) => event.id)).size).toBe(tail.length)

  // A retry of the original POST must replay the same Run, not insert another pair of messages.
  await page.evaluate(async ({ requestId, message, conversationId }) => {
    await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_request_id: requestId, conversation_id: conversationId, message }),
    }).then((response) => response.text())
  }, { requestId, message, conversationId: first.conversationId })
  const messages = await page.evaluate(async (conversationId) => {
    const response = await fetch(`/api/conversations/${conversationId}/messages`)
    return (await response.json()).data
  }, first.conversationId)
  expect(messages.filter((item: { role: string }) => item.role === 'user')).toHaveLength(1)
  expect(messages.filter((item: { role: string }) => item.role === 'assistant')).toHaveLength(1)
})
