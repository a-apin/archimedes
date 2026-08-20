import { useCallback, useEffect, useRef, useState } from 'react'

import { canUnlink, connectedProviderLabel, isKnownConnectedProvider } from '../account-linking'
import { linkErrorMessage } from '../auth-errors'
import { getProviders, linkSocial, listAccounts, unlinkAccount } from '../auth-client'
import { useAuth } from '../AuthContext'
import { listLinkedWallets, makePrimaryWallet, removeLinkedWallet } from '../linked-wallets'
import { providerLabel } from '../wallet-providers'

export default function AccountSettings({ walletAddr, onDisconnect, linkError }) {
  const { user, signOut } = useAuth()
  const [wallets, setWallets] = useState([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(null)

  const [connectedAccounts, setConnectedAccounts] = useState([])
  const [connectedLoaded, setConnectedLoaded] = useState(false)
  const [connectedProviders, setConnectedProviders] = useState({ google: false, github: false })
  const [connectedError, setConnectedError] = useState('')
  const [connectedNotice, setConnectedNotice] = useState('')
  const [connectedBusy, setConnectedBusy] = useState(null)
  // Set once a link/unlink attempt discovers the session is too old to
  // change connected accounts (round-2 review, blocker: auth.js now gates
  // BOTH /link-social and /unlink-account on session freshness — see
  // auth/auth.js's hooks.before). Client-side we only learn this
  // reactively, from a 403 — there is no lighter-weight signal to poll
  // ahead of a click — so once discovered it disables the whole section
  // and offers the one way out, instead of leaving live-looking buttons
  // that will just 403 again.
  const [connectedSessionStale, setConnectedSessionStale] = useState(false)

  const load = useCallback(() => listLinkedWallets().then(setWallets).catch((err) => setError(err.message)), [])
  useEffect(() => { load() }, [load])

  // connectedLoaded is tracked separately from connectedAccounts.length so
  // "Loading…" cannot render forever: before this fix, a rejected
  // listAccounts() left connectedAccounts at its initial `[]`, which the
  // render below was (mis)using as the loading sentinel — the same empty
  // state a successful-but-truly-empty load would also produce (round-2
  // review finding). Both branches below now set connectedLoaded so the
  // request always resolves out of the loading state, error or not.
  const loadConnected = useCallback(
    () => listAccounts()
      .then((accounts) => { setConnectedAccounts(accounts); setConnectedLoaded(true) })
      .catch((err) => { setConnectedError(err.message); setConnectedLoaded(true) }),
    [],
  )
  useEffect(() => { loadConnected() }, [loadConnected])
  useEffect(() => {
    getProviders().then((providers) => setConnectedProviders({ google: !!providers.google, github: !!providers.github })).catch(() => {})
  }, [])

  // Read the explicit-link round trip's success marker off the URL, once,
  // after the first successful account load — same local pattern
  // AuthPage.jsx uses for its reset-password token. Round-2 review finding:
  // this used to fire straight off the URL with no check at all, so it
  // could assert a link that never happened (any `?linked=` value rendered
  // verbatim) instead of being the confirmation toast its own comment
  // claimed it was. Now it only fires once connectedAccounts has actually
  // loaded, and only for a value that both names a real provider AND is
  // present in that freshly reloaded list — the account appearing in
  // connectedAccounts is the real proof; this becomes strictly a toast on
  // top of it, never a substitute for it.
  const linkedNoticeChecked = useRef(false)
  useEffect(() => {
    if (!connectedLoaded || linkedNoticeChecked.current) return
    linkedNoticeChecked.current = true
    const linked = new URLSearchParams(window.location.search).get('linked')
    if (!linked || !isKnownConnectedProvider(linked)) return
    if (connectedAccounts.some((account) => account.providerId === linked)) {
      setConnectedNotice(`Linked ${connectedProviderLabel(linked)}.`)
    }
  }, [connectedLoaded, connectedAccounts])

  const linkErrorNotice = linkErrorMessage(linkError)

  const link = async (provider) => {
    setConnectedBusy(provider)
    setConnectedError('')
    setConnectedNotice('')
    try {
      const origin = window.location.origin
      // Both callbacks land back on THIS page — the /callback/:id endpoint
      // (library-managed CSRF state, not this code) is what actually
      // decides success/failure before either is reached.
      await linkSocial(provider, `${origin}/app/account?linked=${provider}`, `${origin}/app/account`)
    } catch (err) {
      if (err.code === 'SESSION_NOT_FRESH') setConnectedSessionStale(true)
      setConnectedError(err.code === 'SESSION_NOT_FRESH' ? linkErrorMessage('SESSION_NOT_FRESH') : err.message)
      setConnectedBusy(null)
    }
  }

  // Guarded twice on purpose: canUnlink() keeps the button disabled/inert in
  // the last-credential state (see the export above), and the confirm below
  // still runs before the network call for any account, mirroring the
  // wallet-unlink confirm pattern above (3.3.4 — no single unconfirmed click
  // for a destructive, no-undo action).
  const unlinkConnected = async (account) => {
    if (!canUnlink(connectedAccounts.length)) return
    const label = connectedProviderLabel(account.providerId)
    if (!window.confirm(`Unlink ${label}? You will no longer be able to sign in with ${label}.`)) return
    setConnectedBusy(account.id)
    setConnectedError('')
    setConnectedNotice('')
    try {
      await unlinkAccount(account.providerId, account.accountId)
      await loadConnected()
      setConnectedNotice(`Unlinked ${label}.`)
    } catch (err) {
      if (err.code === 'SESSION_NOT_FRESH') setConnectedSessionStale(true)
      setConnectedError(err.code === 'SESSION_NOT_FRESH' ? linkErrorMessage('SESSION_NOT_FRESH') : err.message)
    } finally {
      setConnectedBusy(null)
    }
  }

  const primary = async (id) => {
    setBusy(id)
    setError('')
    try {
      await makePrimaryWallet(id)
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(null)
    }
  }

  // Unlinking deletes the account↔wallet binding that owns the user's on-chain
  // history, with no undo — relinking means re-running the SIWE signature flow,
  // and until then the wallet's data is invisible. It was a single unconfirmed
  // click, so a mis-click or a stray Enter on a focused button silently
  // stranded strategies (3.3.4). PaperTrading.jsx already confirms the
  // comparable destructive action, so this was an inconsistency, not a policy.
  const unlink = async (wallet) => {
    const label = wallet.display_address || wallet.address
    if (!window.confirm(
      `Unlink ${label}? This removes the account↔wallet binding; re-linking requires signing again with that wallet.`,
    )) return
    setBusy(wallet.id)
    setError('')
    setNotice('')
    try {
      await removeLinkedWallet(wallet.id)
      if (walletAddr?.toLowerCase() === wallet.address) onDisconnect?.()
      await load()
      setNotice(`Unlinked ${label}.`)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(null)
    }
  }

  const logout = async () => {
    await signOut()
    onDisconnect?.()
    window.location.assign('/')
  }

  return (
    <div className="max-w-[760px]">
      <h1 className="serif text-[2rem] mb-2">Account</h1>
      <p className="body mb-7">Better Auth user owns application data. Linked wallets prove on-chain control only.</p>

      <section className="card-flat p-5 mb-5">
        <h2 className="serif text-xl mb-3">Profile</h2>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt className="caption">Name</dt><dd>{user?.name || '—'}</dd>
          <dt className="caption">Email</dt><dd>{user?.email || '—'}</dd>
          <dt className="caption">User ID</dt><dd className="mono break-all">{user?.id}</dd>
        </dl>
      </section>

      <section className="card-flat p-5 mb-5">
        <h2 className="serif text-xl mb-3">Connected accounts</h2>
        <p className="caption mb-3">
          Sign in with any of these — they all reach this one account. Google and GitHub link only after you
          authorize them from here, signed in as you are now.
        </p>

        {linkErrorNotice && <div className="status mb-3" role="alert">{linkErrorNotice}</div>}

        {!connectedLoaded ? (
          <p className="body">Loading…</p>
        ) : connectedAccounts.length === 0 ? (
          <p className="body">No connected sign-in methods.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {connectedAccounts.map((account) => {
              const label = connectedProviderLabel(account.providerId)
              const disabled = connectedBusy === account.id || connectedSessionStale || !canUnlink(connectedAccounts.length)
              const title = connectedSessionStale
                ? 'Sign in again to change connected accounts.'
                : !canUnlink(connectedAccounts.length)
                  ? 'This is your only sign-in method — link another before unlinking it.'
                  : undefined
              return (
                <li key={account.id} className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--glass-border)] pt-3">
                  <div className="mono text-sm">{label}</div>
                  <button
                    className="btn-secondary"
                    disabled={disabled}
                    title={title}
                    onClick={() => unlinkConnected(account)}
                  >
                    Unlink
                  </button>
                </li>
              )
            })}
          </ul>
        )}

        {(connectedProviders.google || connectedProviders.github) && (
          <div className="mt-4 flex gap-2">
            {connectedProviders.google && !connectedAccounts.some((a) => a.providerId === 'google') && (
              <button className="btn-secondary" disabled={connectedBusy === 'google' || connectedSessionStale} onClick={() => link('google')}>
                Link Google
              </button>
            )}
            {connectedProviders.github && !connectedAccounts.some((a) => a.providerId === 'github') && (
              <button className="btn-secondary" disabled={connectedBusy === 'github' || connectedSessionStale} onClick={() => link('github')}>
                Link GitHub
              </button>
            )}
          </div>
        )}

        <div role="status" aria-live="polite" className={connectedNotice ? 'caption mt-3' : 'sr-only'}>
          {connectedNotice}
        </div>
        {connectedError && (
          <div className="status mt-3" role="alert">
            {connectedError}
            {/* SESSION_NOT_FRESH (round-2 review, blocker): the session is
                still signed in, just too old for auth.js's freshness gate
                on /link-social and /unlink-account — re-authenticating is
                the fix, not "you got logged out". logout() below drives the
                same sign-out/redirect path the page's own Sign out button
                uses; signing back in mints a brand-new session, which
                resets createdAt and clears this state on next visit. */}
            {connectedSessionStale && (
              <button className="btn-secondary ml-2" onClick={logout}>Sign in again</button>
            )}
          </div>
        )}
      </section>

      <section className="card-flat p-5 mb-5">
        <div className="flex items-center justify-between gap-3 mb-3">
          <div>
            <h2 className="serif text-xl">Linked wallets</h2>
            <p className="caption mt-1">Use Connect Wallet in top bar to add MetaMask, browser, or Circle wallets.</p>
          </div>
        </div>
        {wallets.length === 0 ? (
          <p className="body">No linked wallet. Account-only features remain available.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {wallets.map((wallet) => (
              <li key={wallet.id} className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--glass-border)] pt-3">
                <div>
                  <div className="mono text-sm">{wallet.display_address}</div>
                  <div className="caption">{providerLabel(wallet.provider)} · chain {wallet.chain_id}{wallet.is_primary ? ' · primary' : ''}</div>
                </div>
                <div className="flex gap-2">
                  {!wallet.is_primary && (
                    <button className="btn-secondary" disabled={busy === wallet.id} onClick={() => primary(wallet.id)}>
                      Make primary
                    </button>
                  )}
                  <button className="btn-secondary" disabled={busy === wallet.id} onClick={() => unlink(wallet)}>
                    Unlink
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
        <div role="status" aria-live="polite" className={notice ? 'caption mt-3' : 'sr-only'}>
          {notice}
        </div>
        {error && <div className="status mt-3" role="alert">{error}</div>}
      </section>

      <button className="btn-secondary" onClick={logout}>Sign out</button>
    </div>
  )
}
