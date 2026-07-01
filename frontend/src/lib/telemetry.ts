/**
 * telemetry.ts — Browser-side Application Insights (client observability).
 *
 * Initialises the App Insights web SDK against the SAME resource as the backend
 * (connection string fetched from GET /config), so client telemetry — page
 * views, JS errors, unhandled promise rejections, fetch dependencies — shares an
 * OperationId with the server spans: one end-to-end transaction from a click,
 * through the API, to SQL. trackEvent instruments the known optimistic-write
 * trouble spots (delete/undo, cache rollback).
 *
 * Everything is gated on a connection string being present. Locally — or in any
 * deployment without Application Insights — GET /config returns null and every
 * function here is a clean no-op: nothing is loaded and no telemetry is sent.
 */

import {
  ApplicationInsights,
  DistributedTracingModes,
} from '@microsoft/applicationinsights-web'
import { ReactPlugin } from '@microsoft/applicationinsights-react-js'

import type { AppConfig } from '@/types'

/**
 * The React extension, shared with the component tree via AppInsightsContext in
 * main.tsx so plugin hooks/HOCs work. Created eagerly (its constructor is inert)
 * so the provider has a stable instance even before initTelemetry() resolves.
 */
export const reactPlugin = new ReactPlugin()

let appInsights: ApplicationInsights | null = null

/** Property values App Insights accepts on a custom event/exception. */
type EventProps = Record<string, string | number | boolean>

async function fetchConnectionString(): Promise<string | null> {
  try {
    const res = await fetch('/config', { credentials: 'same-origin' })
    if (!res.ok) return null
    const body = (await res.json()) as AppConfig
    return body.applicationInsights?.connectionString ?? null
  } catch {
    return null // telemetry must never break app startup
  }
}

/**
 * Fetch the connection string and, if telemetry is configured, load the SDK.
 * Idempotent and best-effort: no config, a network failure, or an SDK error all
 * leave telemetry disabled without disturbing the app. Safe to call before render.
 */
export async function initTelemetry(): Promise<void> {
  if (appInsights) return
  const connectionString = await fetchConnectionString()
  if (!connectionString) return

  try {
    appInsights = new ApplicationInsights({
      config: {
        connectionString,
        extensions: [reactPlugin],
        // One page-view span per SPA route change (navigation is driven through
        // the History API) and correlation headers on outbound fetches, so a
        // client action ties to its server request span (shared OperationId).
        enableAutoRouteTracking: true,
        disableFetchTracking: false,
        enableCorsCorrelation: true,
        distributedTracingMode: DistributedTracingModes.AI_AND_W3C,
        enableUnhandledPromiseRejectionTracking: true,
        autoTrackPageVisitTime: true,
      },
    })
    appInsights.loadAppInsights()
    appInsights.trackPageView() // initial load; later route changes auto-track
  } catch {
    appInsights = null // stay a no-op rather than half-initialised
  }
}

/** Record a custom event. No-op until telemetry is initialised. */
export function trackEvent(name: string, properties?: EventProps): void {
  appInsights?.trackEvent({ name }, properties)
}

/** Record a handled exception. No-op until telemetry is initialised. */
export function trackException(error: Error, properties?: EventProps): void {
  appInsights?.trackException({ exception: error }, properties)
}

/**
 * Bucket a row count into a low-cardinality label for event dimensions, so
 * telemetry stays cheap and aggregatable (no unbounded numeric values, which
 * bloat cardinality without adding analytical value over ranges).
 */
export function sizeBucket(n: number): string {
  if (n <= 0) return '0'
  if (n === 1) return '1'
  if (n <= 10) return '2-10'
  if (n <= 100) return '11-100'
  if (n <= 1000) return '101-1000'
  return '1000+'
}
