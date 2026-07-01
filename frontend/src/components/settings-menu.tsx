/**
 * settings-menu.tsx — Footer gear menu: About + Refresh schema.
 *
 * Two entries. "About" opens a short description of the app. "Refresh schema"
 * opens a confirmation that explains what a re-reflection does, why it exists
 * (pick up DDL changes without a redeploy), and warns that it's slow and
 * process-wide before the user proceeds — Cancel/Escape backs out, Refresh
 * proceeds. The refresh re-reads the whole schema and rebuilds the API; on
 * success every cached query is invalidated so the UI rebuilds against the new
 * snapshot.
 */

import { useState } from 'react'
import { InfoIcon, RefreshCwIcon, SettingsIcon, TriangleAlertIcon } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { SaveButton } from '@/components/save-button'
import { useRefreshSchema, useSchemas, useVersion } from '@/hooks/queries'
import { messageFor } from '@/lib/errors'
import type { BuildInfo } from '@/types'

export function SettingsMenu() {
  const [aboutOpen, setAboutOpen] = useState(false)
  const [refreshOpen, setRefreshOpen] = useState(false)

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={<Button variant="ghost" size="icon-sm" aria-label="Settings" />}
        >
          <SettingsIcon />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="min-w-44">
          <DropdownMenuItem onClick={() => setAboutOpen(true)}>
            <InfoIcon />
            About
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => setRefreshOpen(true)}>
            <RefreshCwIcon />
            Refresh schema
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <AboutDialog open={aboutOpen} onOpenChange={setAboutOpen} />
      <RefreshDialog open={refreshOpen} onOpenChange={setRefreshOpen} />
    </>
  )
}

function AboutDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { data } = useSchemas()
  const { data: build } = useVersion()
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Auto CRUD</DialogTitle>
          <DialogDescription>
            A full CRUD interface generated live from your database schema. Every
            table you can access becomes a searchable, editable grid — with no
            per-table code. Forms, columns, and filters are built from the
            database’s own metadata, so the app adapts as the schema changes.
          </DialogDescription>
        </DialogHeader>
        {data?.database && (
          <p className="text-sm text-muted-foreground">
            Connected to{' '}
            <span className="font-medium text-foreground">{data.database}</span>.
          </p>
        )}
        {build && (
          <p className="text-xs text-muted-foreground">{buildLabel(build)}</p>
        )}
        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  )
}

/**
 * A one-line build label for the About dialog. The backend reports a real commit
 * SHA + build time only for a CI-built image; otherwise it's a sentinel — "dev"
 * (ran outside a container) or "unknown" (an image built without the CI
 * build-args, see app/build_info.py). Both mean "not a tracked build", so we show
 * a plain "Development build" rather than a meaningless version string.
 */
function buildLabel(build: BuildInfo): string {
  if (build.sha === 'dev' || build.sha === 'unknown') return 'Development build'
  const built = Date.parse(build.time)
  const when = Number.isNaN(built) ? '' : ` · built ${new Date(built).toLocaleDateString()}`
  return `Version ${build.sha}${when}`
}

function RefreshDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const refresh = useRefreshSchema()

  function onConfirm() {
    refresh.mutate(undefined, {
      onSuccess: (res) => {
        toast.success(
          `Schema refreshed — ${res.total} ${res.total === 1 ? 'table' : 'tables'}.`,
        )
        onOpenChange(false)
      },
      onError: (err) => {
        toast.error(messageFor(err, 'Schema refresh failed. Check the server logs.'))
      },
    })
  }

  return (
    <Dialog
      open={open}
      // Don't let Escape or a backdrop click dismiss a refresh in flight — it's
      // a slow, process-wide operation; keep the spinner visible until it lands.
      onOpenChange={(next) => {
        if (!refresh.isPending) onOpenChange(next)
      }}
    >
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <div className="flex size-9 items-center justify-center rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-500">
            <RefreshCwIcon className="size-5" />
          </div>
          <DialogTitle>Refresh schema?</DialogTitle>
          <DialogDescription>
            Re-reads the database structure and rebuilds the API in place — picking
            up new tables, columns, and constraints without a redeploy. Use it when
            the database schema has changed and the app hasn’t caught up yet.
          </DialogDescription>
        </DialogHeader>

        <div className="flex gap-2.5 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-muted-foreground">
          <TriangleAlertIcon className="mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-500" />
          <p>
            This is slow — it reflects the entire schema from scratch and can take
            many seconds on a large database. It runs process-wide, so everyone
            using the app is briefly affected. It’s safe to run, but don’t trigger
            it casually.
          </p>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={refresh.isPending}
          >
            Cancel
          </Button>
          <SaveButton type="button" loading={refresh.isPending} onClick={onConfirm}>
            Refresh
          </SaveButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
