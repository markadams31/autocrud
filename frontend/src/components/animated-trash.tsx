/**
 * animated-trash.tsx — A trash icon whose lid lifts on hover.
 *
 * Geometry mirrors Lucide's `trash-2` so it sits flush beside the other icons,
 * but the lid (top bar + handle) is a separate group that tilts up when the
 * enclosing Button is hovered (Button carries `group/button`). A small, on-brand
 * bit of delight for delete actions; it sizes itself from the Button like any
 * other icon, and is decorative (aria-hidden) since the Button is labelled.
 */

import { cn } from '@/lib/utils'

export function AnimatedTrash({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={cn(className)}
    >
      {/* Can + inner lines — static. */}
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
      <line x1="10" x2="10" y1="11" y2="17" />
      <line x1="14" x2="14" y1="11" y2="17" />
      {/* Lid — tilts up on hover, hinged at its right end. */}
      <g className="origin-right [transform-box:fill-box] transition-transform duration-200 ease-out group-hover/button:-rotate-12">
        <path d="M3 6h18" />
        <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      </g>
    </svg>
  )
}
