import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

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
