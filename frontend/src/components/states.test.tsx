import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { EmptyState } from '@/components/states'
import { ErrorState } from '@/components/states'
import { ApiError } from '@/lib/api'

const err = (code: string, message = 'detail text') =>
  new ApiError(400, { code: code as never, message })

describe('ErrorState — maps the API error contract to guidance', () => {
  it('UNAUTHENTICATED → session-expired prompt', () => {
    render(<ErrorState error={err('UNAUTHENTICATED')} />)
    expect(screen.getByText('Your session has expired')).toBeInTheDocument()
  })

  it('PERMISSION_DENIED → no-access guidance', () => {
    render(<ErrorState error={err('PERMISSION_DENIED')} />)
    expect(screen.getByText('You don’t have access')).toBeInTheDocument()
  })

  it('DATABASE_UNAVAILABLE → try-again guidance', () => {
    render(<ErrorState error={err('DATABASE_UNAVAILABLE')} />)
    expect(screen.getByText('The database is unavailable')).toBeInTheDocument()
  })

  it('NOT_FOUND → shows the server-supplied message as the description', () => {
    render(<ErrorState error={err('NOT_FOUND', 'No such table')} />)
    expect(screen.getByText('Not found')).toBeInTheDocument()
    expect(screen.getByText('No such table')).toBeInTheDocument()
  })

  it('an unmapped code falls back to the generic title + its message', () => {
    render(<ErrorState error={err('CONSTRAINT_VIOLATION', 'rule broken')} />)
    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
    expect(screen.getByText('rule broken')).toBeInTheDocument()
  })

  it('a non-ApiError gets a safe generic message (no detail leaked)', () => {
    render(<ErrorState error={new Error('stack trace with secrets')} />)
    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
    expect(screen.getByText('An unexpected error occurred. Please try again.')).toBeInTheDocument()
    expect(screen.queryByText(/secrets/)).not.toBeInTheDocument()
  })
})

describe('EmptyState', () => {
  it('renders the title and optional description', () => {
    render(<EmptyState title="Nothing here" description="Pick a table to begin." />)
    expect(screen.getByText('Nothing here')).toBeInTheDocument()
    expect(screen.getByText('Pick a table to begin.')).toBeInTheDocument()
  })
})
