import { render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const push = vi.fn()
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn(), prefetch: vi.fn() }),
}))

vi.mock('@/lib/config', () => ({
  getConfig: vi.fn(async () => ({
    apiUrl: 'http://api:5055',
    version: 'test',
    buildTime: 'now',
  })),
}))

const authState: Record<string, unknown> = {}
vi.mock('@/lib/stores/auth-store', () => ({
  useAuthStore: () => authState,
}))
vi.mock('@/lib/hooks/use-auth', () => ({
  useAuth: () => ({ login: vi.fn(), isLoading: false, error: null }),
}))

import { LoginForm } from './LoginForm'

/**
 * Deep-link preservation: the dashboard guard stashes the intended path in
 * sessionStorage before bouncing through /login; when auth turns out to be
 * unnecessary, LoginForm must continue there instead of hard-coding
 * /notebooks (the bug fixed in 3165554).
 */
describe('LoginForm deep-link restore', () => {
  beforeEach(() => {
    push.mockReset()
    window.sessionStorage.clear()
    for (const key of Object.keys(authState)) delete authState[key]
  })

  it('restores the stashed path when auth check resolves to not-required', async () => {
    Object.assign(authState, {
      hasHydrated: true,
      authRequired: null,
      isAuthenticated: false,
      checkAuthRequired: vi.fn(async () => false),
    })
    window.sessionStorage.setItem('redirectAfterLogin', '/podcasts')
    render(<LoginForm />)
    await waitFor(() => expect(push).toHaveBeenCalledWith('/podcasts'))
    expect(window.sessionStorage.getItem('redirectAfterLogin')).toBeNull()
  })

  it('falls back to /notebooks when nothing was stashed', async () => {
    Object.assign(authState, {
      hasHydrated: true,
      authRequired: null,
      isAuthenticated: false,
      checkAuthRequired: vi.fn(async () => false),
    })
    render(<LoginForm />)
    await waitFor(() => expect(push).toHaveBeenCalledWith('/notebooks'))
  })

  it('restores the stashed path when already authenticated without auth', async () => {
    Object.assign(authState, {
      hasHydrated: true,
      authRequired: false,
      isAuthenticated: true,
      checkAuthRequired: vi.fn(),
    })
    window.sessionStorage.setItem('redirectAfterLogin', '/podcasts')
    render(<LoginForm />)
    await waitFor(() => expect(push).toHaveBeenCalledWith('/podcasts'))
  })

  it('stays on the form when auth IS required', async () => {
    const checkAuthRequired = vi.fn(async () => true)
    Object.assign(authState, {
      hasHydrated: true,
      authRequired: null,
      isAuthenticated: false,
      checkAuthRequired,
    })
    render(<LoginForm />)
    await waitFor(() => expect(checkAuthRequired).toHaveBeenCalled())
    expect(push).not.toHaveBeenCalled()
  })

  it('assumes auth is required when the status check fails', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    Object.assign(authState, {
      hasHydrated: true,
      authRequired: null,
      isAuthenticated: false,
      checkAuthRequired: vi.fn(async () => {
        throw new Error('api down')
      }),
    })
    render(<LoginForm />)
    await waitFor(() => expect(errorSpy).toHaveBeenCalled())
    expect(push).not.toHaveBeenCalled()
    errorSpy.mockRestore()
  })
})
