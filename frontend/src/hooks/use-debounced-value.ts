import { useEffect, useState } from 'react'

/**
 * Returns a value that only updates after `delay` ms of no changes. Used to
 * keep search-as-you-type from firing a query request on every keystroke.
 */
export function useDebouncedValue<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value)

  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(id)
  }, [value, delay])

  return debounced
}
