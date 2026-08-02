import client from './client'

type FirstValueEvent =
  | 'capture_created'
  | 'capture_processed'
  | 'source_question_started'
  | 'source_question_submitted'
  | 'cited_answer_received'
  | 'citation_opened'
  | 'capture_processing_failed'
  | 'capture_retry_started'
  | 'capture_retry_recovered'
type EventProperties = Record<string, string | number | boolean | null | undefined>
interface Wrapped<T> { code: number; message: string; data: T }

export interface FirstValueFunnel {
  days: number
  captured: number
  processed: number
  questioned: number
  cited: number
  reviewed: number
  failed: number
  recovered: number
  outstanding_failures: number
  processing_rate: number
  question_rate: number
  citation_rate: number
  review_rate: number
}

export const productEventApi = {
  track(eventName: FirstValueEvent, properties: EventProperties = {}) {
    const cleanProperties = Object.fromEntries(Object.entries(properties).filter(([, value]) => value !== undefined))
    return client.post('/product-events', { event_name: eventName, properties: cleanProperties })
  },
  firstValue(days = 30) {
    return client.get<unknown, Wrapped<FirstValueFunnel>>(`/product-events/first-value?days=${days}`)
  },
}
