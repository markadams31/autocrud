import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'
import { invalidateTableWrites, queryKeys, useUpdateRow } from '@/hooks/queries'
import type { QueryRequest, QueryResponse, Row } from '@/types'

// Mock only the network surface; keep the real encodePk (row identity) etc.
vi.mock('@/lib/api', async (importActual) => {
  const actual = await importActual<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, updateRow: vi.fn() } }
})

const REQ = {
  search: '',
  filters: {},
  sort: { column: '', direction: 'asc' },
  page: 1,
  page_size: 50,
} as unknown as QueryRequest

function wrapper(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

function seededClient(): { qc: QueryClient; key: readonly unknown[] } {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const key = queryKeys.rows('dbo', 'Widget', REQ)
  const page: QueryResponse = {
    data: [
      { WidgetID: 1, Name: 'A' },
      { WidgetID: 2, Name: 'B' },
    ],
    total: 2,
    page: 1,
    page_size: 50,
    pages: 1,
  }
  qc.setQueryData(key, page)
  return { qc, key }
}

const nameOf = (qc: QueryClient, key: readonly unknown[], id: number) =>
  (qc.getQueryData(key) as QueryResponse).data.find((r: Row) => r.WidgetID === id)?.Name

describe('useUpdateRow — optimistic update', () => {
  it('patches the cached row immediately, then reconciles on success', async () => {
    const { qc, key } = seededClient()
    ;(api.updateRow as ReturnType<typeof vi.fn>).mockResolvedValue({ WidgetID: 1, Name: 'X' })
    const invalidate = vi.spyOn(qc, 'invalidateQueries')

    const { result } = renderHook(() => useUpdateRow('dbo', 'Widget', ['WidgetID']), {
      wrapper: wrapper(qc),
    })
    result.current.mutate({ row: { WidgetID: 1, Name: 'A' }, values: { Name: 'X' } })

    // Optimistic: the cache shows 'X' before the server responds; sibling untouched.
    await waitFor(() => expect(nameOf(qc, key, 1)).toBe('X'))
    expect(nameOf(qc, key, 2)).toBe('B')

    await waitFor(() => expect(api.updateRow).toHaveBeenCalledTimes(1))
    // onSettled invalidates the table's rows and every FK option list.
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.rowsAll('dbo', 'Widget') }),
    )
  })

  it('rolls the optimistic patch back when the update fails', async () => {
    const { qc, key } = seededClient()
    ;(api.updateRow as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'))

    const { result } = renderHook(() => useUpdateRow('dbo', 'Widget', ['WidgetID']), {
      wrapper: wrapper(qc),
    })
    result.current.mutate({ row: { WidgetID: 1, Name: 'A' }, values: { Name: 'X' } })

    await waitFor(() => expect(api.updateRow).toHaveBeenCalled())
    // The cache is restored to the pre-mutation snapshot, not left showing 'X'.
    await waitFor(() => expect(nameOf(qc, key, 1)).toBe('A'))
  })
})

describe('invalidateTableWrites', () => {
  it('invalidates the table rows AND every FK option list', () => {
    const qc = new QueryClient()
    const invalidate = vi.spyOn(qc, 'invalidateQueries')
    invalidateTableWrites(qc, 'dbo', 'Widget')
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.rowsAll('dbo', 'Widget') })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.optionsAll })
  })
})
