import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import { _resetAdminProbeCache } from './adminProbe.js'
import { getSession, signOut as endSession } from './auth-client'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [session, setSession] = useState(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      setSession(await getSession())
    } catch {
      setSession(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const signOut = useCallback(async () => {
    await endSession()
    setSession(null)
    // A cached "admin" determination must not survive into whatever
    // signs in next on this browser (owner directive 2026-08-20 admin
    // gate) — the shared probe cache is keyed on nothing session-specific,
    // so it has to be cleared explicitly here rather than expiring on its
    // own short TTL.
    _resetAdminProbeCache()
  }, [])

  const value = useMemo(() => ({
    user: session?.user ?? null,
    session: session?.session ?? null,
    loading,
    refresh,
    signOut,
  }), [session, loading, refresh, signOut])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}
