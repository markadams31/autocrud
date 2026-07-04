/**
 * save-button.tsx — A submit button that morphs into a spinner while saving.
 *
 * The button keeps a stable minimum width, so when the label crossfades out
 * and the spinner scales in, nothing around it reflows — the layout stays
 * perfectly still. The spinner enters on a spring for a soft, physical morph
 * rather than a hard swap.
 */

import { AnimatePresence, motion } from 'motion/react'
import { Loader2Icon } from 'lucide-react'

import type { VariantProps } from 'class-variance-authority'

import { Button, buttonVariants } from '@/components/ui/button'
import { morphSpring } from '@/lib/animations'
import { cn } from '@/lib/utils'

interface SaveButtonProps {
  loading: boolean
  disabled?: boolean
  type?: 'button' | 'submit'
  variant?: VariantProps<typeof buttonVariants>['variant']
  onClick?: () => void
  className?: string
  children: React.ReactNode
}

export function SaveButton({
  loading,
  disabled,
  type = 'submit',
  variant,
  onClick,
  className,
  children,
}: SaveButtonProps) {
  return (
    <Button
      type={type}
      variant={variant}
      onClick={onClick}
      disabled={disabled || loading}
      aria-busy={loading}
      className={cn('relative min-w-28', className)}
    >
      {/* Label — fades out in place; never unmounts, so width is preserved. */}
      <span
        className={cn(
          'inline-flex items-center gap-1.5 transition-opacity duration-150',
          loading && 'opacity-0',
        )}
      >
        {children}
      </span>

      {/* Spinner — springs in over the centre while loading. */}
      <AnimatePresence>
        {loading && (
          <motion.span
            className="absolute inset-0 inline-flex items-center justify-center"
            initial={{ opacity: 0, scale: 0.5 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.5 }}
            transition={morphSpring}
          >
            <Loader2Icon className="size-4 animate-spin" />
          </motion.span>
        )}
      </AnimatePresence>
    </Button>
  )
}
