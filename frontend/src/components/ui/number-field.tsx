"use client"

import { NumberField as NumberFieldPrimitive } from "@base-ui/react/number-field"
import { MinusIcon, PlusIcon } from "lucide-react"

import { cn } from "@/lib/utils"

/**
 * number-field.tsx — Numeric input with increment/decrement steppers, built on
 * Base UI's NumberField. Over a bare <input type="number"> it adds: hold-to-
 * repeat steppers, scrub-to-change on the label, locale-aware display
 * formatting, and min/max clamping — while still accepting free typing.
 *
 * The border lives on the Group so the steppers sit flush inside one control;
 * focus and invalid rings mirror Input/Select via group-level state.
 */

const stepperClass =
  "flex w-8 shrink-0 items-center justify-center text-muted-foreground outline-none transition-colors select-none hover:bg-muted hover:text-foreground active:bg-muted/70 disabled:pointer-events-none disabled:opacity-40 [&_svg]:size-3.5"

interface NumberFieldProps {
  id?: string
  value: number | null
  onValueChange: (value: number | null) => void
  min?: number
  max?: number
  step?: number | "any"
  format?: Intl.NumberFormatOptions
  disabled?: boolean
  invalid?: boolean
  autoFocus?: boolean
  placeholder?: string
  className?: string
}

function NumberField({
  id,
  value,
  onValueChange,
  min,
  max,
  step,
  format,
  disabled,
  invalid,
  autoFocus,
  placeholder,
  className,
}: NumberFieldProps) {
  return (
    <NumberFieldPrimitive.Root
      id={id}
      value={value}
      onValueChange={onValueChange}
      min={min}
      max={max}
      step={step}
      format={format}
      disabled={disabled}
      className={cn("w-full", className)}
    >
      <NumberFieldPrimitive.Group
        data-slot="number-field-group"
        className="flex h-8 w-full overflow-hidden rounded-lg border border-input bg-transparent transition-colors hover:border-muted-foreground/40 focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50 has-aria-invalid:border-destructive has-aria-invalid:ring-3 has-aria-invalid:ring-destructive/20 has-disabled:pointer-events-none has-disabled:opacity-50 dark:bg-input/30"
      >
        <NumberFieldPrimitive.Decrement
          aria-label="Decrease"
          className={cn(stepperClass, "border-r border-input")}
        >
          <MinusIcon />
        </NumberFieldPrimitive.Decrement>
        <NumberFieldPrimitive.Input
          autoFocus={autoFocus}
          placeholder={placeholder}
          aria-invalid={invalid}
          className="h-full min-w-0 flex-1 bg-transparent px-2.5 text-center text-base tabular-nums outline-none placeholder:text-muted-foreground md:text-sm"
        />
        <NumberFieldPrimitive.Increment
          aria-label="Increase"
          className={cn(stepperClass, "border-l border-input")}
        >
          <PlusIcon />
        </NumberFieldPrimitive.Increment>
      </NumberFieldPrimitive.Group>
    </NumberFieldPrimitive.Root>
  )
}

export { NumberField }
