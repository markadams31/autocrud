/**
 * states.tsx — Consistent empty / error / placeholder panels.
 *
 * One visual language for "nothing here yet", "couldn't load", and "pick
 * something to begin", so every dead-end in the app feels considered rather
 * than blank. The error variant maps the API's machine-readable error codes
 * to plain guidance.
 */

import type { LucideIcon } from 'lucide-react'
import {
  DatabaseIcon,
  InboxIcon,
  LockIcon,
  LogInIcon,
  ServerCrashIcon,
  TriangleAlertIcon,
} from 'lucide-react'
import { motion } from 'motion/react'

import { ApiError } from '@/lib/api'
import { easeOutExpo } from '@/lib/animations'
import { cn } from '@/lib/utils'

const enter = {
  initial: { opacity: 0, scale: 0.98, y: 4 },
  animate: { opacity: 1, scale: 1, y: 0 },
  transition: { duration: 0.3, ease: easeOutExpo },
} as const

interface EmptyStateProps {
  icon?: LucideIcon
  title: string
  description?: string
  action?: React.ReactNode
  className?: string
}

export function EmptyState({
  icon: Icon = InboxIcon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <motion.div
      {...enter}
      className={cn(
        'flex flex-col items-center justify-center gap-3 px-6 py-16 text-center',
        className,
      )}
    >
      <div className="flex size-12 items-center justify-center rounded-2xl bg-muted text-muted-foreground">
        <Icon className="size-6" />
      </div>
      <div className="space-y-1">
        <p className="text-sm font-medium">{title}</p>
        {description && (
          <p className="mx-auto max-w-sm text-sm text-pretty text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {action}
    </motion.div>
  )
}

/** Map an error to an icon + human guidance, keyed off the API error contract. */
function describeError(error: unknown): { icon: LucideIcon; title: string; description: string } {
  if (error instanceof ApiError) {
    switch (error.code) {
      case 'UNAUTHENTICATED':
        return {
          icon: LogInIcon,
          title: 'Your session has expired',
          description: 'Please sign in again to continue where you left off.',
        }
      case 'PERMISSION_DENIED':
        return {
          icon: LockIcon,
          title: 'You don’t have access',
          description: 'Your account isn’t permitted to view this data. Contact an administrator if you believe this is a mistake.',
        }
      case 'DATABASE_UNAVAILABLE':
        return {
          icon: ServerCrashIcon,
          title: 'The database is unavailable',
          description: 'The server couldn’t reach the database. This is usually temporary — try again in a moment.',
        }
      case 'NOT_FOUND':
        return {
          icon: DatabaseIcon,
          title: 'Not found',
          description: error.message,
        }
      default:
        return { icon: TriangleAlertIcon, title: 'Something went wrong', description: error.message }
    }
  }
  return {
    icon: TriangleAlertIcon,
    title: 'Something went wrong',
    description: 'An unexpected error occurred. Please try again.',
  }
}

export function ErrorState({
  error,
  action,
  className,
}: {
  error: unknown
  action?: React.ReactNode
  className?: string
}) {
  const { icon: Icon, title, description } = describeError(error)
  return (
    <motion.div
      {...enter}
      className={cn(
        'flex flex-col items-center justify-center gap-3 px-6 py-16 text-center',
        className,
      )}
    >
      <div className="flex size-12 items-center justify-center rounded-2xl bg-destructive/10 text-destructive">
        <Icon className="size-6" />
      </div>
      <div className="space-y-1">
        <p className="text-sm font-medium">{title}</p>
        <p className="mx-auto max-w-sm text-sm text-muted-foreground">{description}</p>
      </div>
      {action}
    </motion.div>
  )
}
