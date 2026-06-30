import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError } from '@/lib/api'
import { queryKeys } from '@/hooks/queries'
import { useUndoableDelete } from '@/hooks/use-undoable-delete'
import type { QueryRequest, QueryResponse } from '@/types'

vi.mock('@/lib/api', async (importActual) => {
  const actual = await importActual<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, deleteRow: vi.fn(), bulkDelete: vi.fn() } }
})

// A Sonner stand-in: toast() returns a stable id; success/error/dismiss are spies.
vi.mock('sonner', () => {
  const toast = Object.assign(vi.fn(() => 'toast-id'), {
    success: vi.fn(),
    error: vi.fn(),
    dismiss: vi.fn(),
  })
  return { toast }
})
import { toast } from 'sonner'

const REQ = {
  search: '',
  filters: {},
  sort: { column: '', direction: 'asc' },
  page: 1,
  page_size: 50,
} as unknown as QueryRequest

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const key = queryKeys.rows('dbo', 'Widget', REQ)
  qc.setQueryData(key, {
    data: [
      { WidgetID: 1, Name: 'A' },
      { WidgetID: 2, Name: 'B' },
    ],
    total: 2,
    page: 1,
    page_size: 50,
    pages: 1,
  } satisfies QueryResponse)

  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children)
  const { result } = renderHook(() => useUndoableDelete('dbo', 'Widget', ['WidgetID']), { wrapper })
  const ids = () => (qc.getQueryData(key) as QueryResponse).data.map((r) => r.WidgetID)
  return { qc, key, result, ids }
}

/** The Undo callback handed to the first toast() call. */
const undoFromToast = () =>
  (toast as unknown as { mock: { calls: unknown[][] } }).mock.calls[0][1] as {
    action: { onClick: () => void }
  }

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
})

describe('useUndoableDelete.remove', () => {
  it('removes the row from the cache immediately and offers Undo', () => {
    const { result, ids } = setup()
    result.current.remove({ WidgetID: 1, Name: 'A' }, { label: 'A' })
    expect(ids()).toEqual([2]) // gone at once, before any server call
    expect(toast).toHaveBeenCalled()
    expect(undoFromToast().action.onClick).toBeTypeOf('function')
  })

  it('Undo cancels the delete entirely — no server call, row restored', async () => {
    const { result, ids } = setup()
    result.current.remove({ WidgetID: 1, Name: 'A' }, { label: 'A' })

    undoFromToast().action.onClick() // click Undo within the window
    await vi.advanceTimersByTimeAsync(6000) // let the whole window elapse

    expect(api.deleteRow).not.toHaveBeenCalled()
    expect(ids()).toEqual([1, 2]) // restored
    expect(toast.dismiss).toHaveBeenCalledWith('toast-id')
  })

  it('fires the real delete when the window elapses, then confirms success', async () => {
    const { result } = setup()
    ;(api.deleteRow as ReturnType<typeof vi.fn>).mockResolvedValue({ deleted: '1' })
    result.current.remove({ WidgetID: 1, Name: 'A' }, { label: 'A' })

    await vi.advanceTimersByTimeAsync(5000) // UNDO_WINDOW_SECONDS

    expect(api.deleteRow).toHaveBeenCalledTimes(1)
    expect(toast.success).toHaveBeenCalledWith('Deleted A.', { id: 'toast-id', duration: 2500 })
  })

  it('restores the row and shows a FINITE-duration error toast carrying the backend reason', async () => {
    // Two guards: the error toast must set its own duration (or it inherits the
    // pending toast's duration:Infinity and never dismisses), and it surfaces the
    // backend's precise reason — which table still references the row.
    const { result, ids } = setup()
    ;(api.deleteRow as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError(409, {
        code: 'CONSTRAINT_VIOLATION' as never,
        message: 'This record cannot be deleted because it is still referenced by ppm.Task.',
      }),
    )
    result.current.remove({ WidgetID: 1, Name: 'A' }, { label: 'A' })

    await vi.advanceTimersByTimeAsync(5000)

    expect(api.deleteRow).toHaveBeenCalledTimes(1)
    expect(ids()).toEqual([1, 2]) // failed delete → row brought back
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining('still referenced by ppm.Task'),
      { id: 'toast-id', duration: 6000 },
    )
  })
})
