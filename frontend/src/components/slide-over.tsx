/**
 * slide-over.tsx — Accessible side panel that enters from the right.
 *
 * Built on Base UI's Dialog so the hard accessibility parts are handled by a
 * maintained primitive rather than by hand: focus is trapped inside the panel
 * while it's open (not merely moved into it once), Escape and a backdrop click
 * close it, body scroll is locked, and the dialog is correctly role/aria-modal
 * labelled. The slide and backdrop fade are CSS transitions keyed off Base UI's
 * starting/ending-style states, and Base UI keeps the panel mounted through the
 * exit so the close animation plays.
 */

import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'

import { cn } from '@/lib/utils'

interface SlideOverProps {
  open: boolean
  onClose: () => void
  /** id of the element labelling the dialog, for aria-labelledby. */
  labelledBy?: string
  className?: string
  children: React.ReactNode
}

export function SlideOver({ open, onClose, labelledBy, className, children }: SlideOverProps) {
  return (
    <DialogPrimitive.Root
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) onClose()
      }}
    >
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-foreground/20 transition-opacity duration-200 ease-out supports-[backdrop-filter]:backdrop-blur-[2px] data-starting-style:opacity-0 data-ending-style:opacity-0 motion-reduce:transition-none" />
        <DialogPrimitive.Popup
          aria-labelledby={labelledBy}
          className={cn(
            'fixed inset-y-0 right-0 z-50 flex h-full w-full max-w-xl flex-col bg-background shadow-2xl ring-1 ring-border outline-none transition-transform duration-300 ease-out data-starting-style:translate-x-full data-ending-style:translate-x-full motion-reduce:transition-none',
            className,
          )}
        >
          {children}
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
