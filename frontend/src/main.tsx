import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MotionConfig } from 'motion/react'
import { AppInsightsContext } from '@microsoft/applicationinsights-react-js'
import { ThemeProvider } from '@/components/theme-provider'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Toaster } from '@/components/ui/sonner'
import { CelebrationProvider } from '@/components/confetti'
import { ErrorBoundary } from '@/components/error-boundary'
import { ApiError } from '@/lib/api'
import { initTelemetry, reactPlugin } from '@/lib/telemetry'
import './index.css'
import App from './App.tsx'

// Kick off client telemetry (no-op when App Insights isn't configured — see
// telemetry.ts). Not awaited: it fetches /config, and blocking first paint on a
// network round-trip isn't worth the tiny window of early errors it would catch.
void initTelemetry()

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // A focus-triggered refetch re-runs the query POST and can shuffle the
      // grid mid-task; for an admin tool, refresh-on-demand is less surprising.
      refetchOnWindowFocus: false,
      // Don't retry 4xx — a denied grant or validation error won't fix itself.
      // Network failures (status 0) and 5xx get a couple of tries.
      retry: (failureCount, error) =>
        error instanceof ApiError && error.status >= 400 && error.status < 500
          ? false
          : failureCount < 2,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppInsightsContext.Provider value={reactPlugin}>
      <ErrorBoundary>
        <ThemeProvider>
          <QueryClientProvider client={queryClient}>
            {/* reducedMotion="user" makes every Motion animation honour the OS
                "reduce motion" accessibility setting automatically. */}
            <MotionConfig reducedMotion="user">
              <CelebrationProvider>
                <TooltipProvider>
                  <App />
                  <Toaster />
                </TooltipProvider>
              </CelebrationProvider>
            </MotionConfig>
          </QueryClientProvider>
        </ThemeProvider>
      </ErrorBoundary>
    </AppInsightsContext.Provider>
  </StrictMode>,
)
