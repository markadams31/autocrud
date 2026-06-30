/**
 * error-boundary.tsx — Last line of defence for render-time crashes.
 *
 * TanStack Query handles *data* errors (those surface as ErrorState panels).
 * This catches the other kind: an exception thrown while React renders, which
 * would otherwise blank the whole page. We use `react-error-boundary` rather
 * than a bespoke class component — it gives us the fallback wiring plus an
 * `onError` hook (a natural seam for a real error reporter) for free.
 */

import type { ErrorInfo, ReactNode } from 'react'
import { ErrorBoundary as ReactErrorBoundary, type FallbackProps } from 'react-error-boundary'
import { RotateCwIcon, TriangleAlertIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'

function AppErrorFallback(_props: FallbackProps) {
  return (
    <div className="flex h-screen w-full flex-col items-center justify-center gap-4 bg-background p-6 text-center text-foreground">
      <div className="flex size-12 items-center justify-center rounded-2xl bg-destructive/10 text-destructive">
        <TriangleAlertIcon className="size-6" />
      </div>
      <div className="space-y-1">
        <p className="text-base font-semibold">Something went wrong</p>
        <p className="mx-auto max-w-md text-sm text-pretty text-muted-foreground">
          The app hit an unexpected error and couldn’t continue. Reloading usually
          clears it. If it keeps happening, contact an administrator.
        </p>
      </div>
      <Button onClick={() => window.location.reload()}>
        <RotateCwIcon />
        Reload the app
      </Button>
    </div>
  )
}

function logError(error: unknown, info: ErrorInfo) {
  // Surfaced to the console (and any console-attached telemetry). A real
  // deployment would forward this to an error reporter here.
  console.error('Unhandled UI error:', error, info.componentStack)
}

export function ErrorBoundary({ children }: { children: ReactNode }) {
  return (
    <ReactErrorBoundary FallbackComponent={AppErrorFallback} onError={logError}>
      {children}
    </ReactErrorBoundary>
  )
}
