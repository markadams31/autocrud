import { beforeEach, describe, expect, it, vi } from 'vitest'

import { validationToast } from '@/lib/validation-toast'
import { toast } from 'sonner'

vi.mock('sonner', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), dismiss: vi.fn() }),
}))

describe('validationToast', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('show() raises the highlighted-fields error toast under the given stable id', () => {
    validationToast('record-form').show()
    expect(toast.error).toHaveBeenCalledWith('Please fix the highlighted fields.', {
      id: 'record-form',
    })
  })

  it('dismiss() dismisses exactly that id', () => {
    validationToast('bulk-edit').dismiss()
    expect(toast.dismiss).toHaveBeenCalledWith('bulk-edit')
  })

  it('binds each form to its own id (no cross-talk)', () => {
    validationToast('form-a').show()
    validationToast('form-b').dismiss()
    expect(toast.error).toHaveBeenCalledWith(expect.any(String), { id: 'form-a' })
    expect(toast.dismiss).toHaveBeenCalledWith('form-b')
  })
})
