import { useCallback, useEffect, useState } from 'react'

import { useAuth } from '../AuthContext'
import { resendVerificationEmail } from '../auth-client'
import { listLinkedWallets, makePrimaryWallet, removeLinkedWallet } from '../linked-wallets'
import { providerLabel } from '../wallet-providers'

// Better Auth's /send-verification-email returns {status:true} once the send is
// QUEUED, not once it is delivered — and the auth sidecar's mailer fail-softs on
// error (auth/auth.js) precisely because SES is sandboxed today, so mail to a
// non-SES-verified address can silently never arrive. Copy here must never claim
// delivery, only that a send was requested.
const VERIFICATION_REQUESTED_MESSAGE = "Verification email requested — delivery isn't confirmed and may take a few minutes."

export default function AccountSettings({ walletAddr, onDisconnect }) {
  const { user, signOut } = useAuth()
  const [wallets, setWallets] = useState([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(null)
  const [verifyStatus, setVerifyStatus] = useState('idle') // idle | sending | sent | error
  const [verifyError, setVerifyError] = useState('')

  const load = useCallback(() => listLinkedWallets().then(setWallets).catch((err) => setError(err.message)), [])
  useEffect(() => { load() }, [load])

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

  // On-demand resend: gives testers a path to exercise the flow with an
  // SES-verified address today, and heals anyone who missed their window once
  // SES production access lands. Does not gate anything — email-verification
  // ENFORCEMENT stays off (auth/auth.js requireEmailVerification).
  const sendVerification = async () => {
    setVerifyStatus('sending')
    setVerifyError('')
    try {
      await resendVerificationEmail(user.email, `${window.location.origin}/app`)
      setVerifyStatus('sent')
    } catch (err) {
      setVerifyError(err.message)
      setVerifyStatus('error')
    }
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
        {user && (
          <div className="mt-3 flex flex-col gap-2">
            {user.emailVerified === false ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="caption">Email not verified</span>
                <button
                  className="btn-secondary"
                  type="button"
                  disabled={verifyStatus === 'sending'}
                  onClick={sendVerification}
                >
                  {verifyStatus === 'sending' ? 'Sending…' : 'Send verification email'}
                </button>
              </div>
            ) : (
              <span className="caption">Email verified ✓</span>
            )}
            {verifyStatus === 'sent' && (
              <div className="status" role="status">{VERIFICATION_REQUESTED_MESSAGE}</div>
            )}
            {verifyStatus === 'error' && (
              <div className="status" role="alert">{verifyError}</div>
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
