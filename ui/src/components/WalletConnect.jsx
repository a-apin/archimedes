import { useState, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { connectWallet, getAvailableProviders } from '../config'

export default function WalletConnect({ address, displayName, onConnect, onDisconnect, onEditProfile }) {
  const [showModal, setShowModal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef(null)

  const available = getAvailableProviders()

  // Close the connected-wallet dropdown on outside click or Escape.
  useEffect(() => {
    if (!menuOpen) return undefined
    const onDocClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false)
    }
    const onKey = (e) => { if (e.key === 'Escape') setMenuOpen(false) }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [menuOpen])

  const handleConnect = async (providerId) => {
    setBusy(true)
    setError('')
    try {
      const result = await connectWallet(providerId)
      setShowModal(false)
      onConnect(result.address)
    } catch (err) {
      setError(err.message)
    }
    setBusy(false)
  }

  if (address) {
    const shortAddr = `${address.slice(0, 6)}…${address.slice(-4)}`
    return (
      <div className="wallet-menu" ref={menuRef} style={{ position: 'relative' }}>
        <button
          type="button"
          className="wallet-chip"
          onClick={() => setMenuOpen(v => !v)}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          title={displayName ? `${displayName} · ${shortAddr}` : shortAddr}
        >
          <span className="dot" />
          <span>{displayName || shortAddr}</span>
          <span
            className={`i-lucide-chevron-${menuOpen ? 'up' : 'down'}`}
            style={{ width: 14, height: 14, marginLeft: 4 }}
            aria-hidden="true"
          />
        </button>
        {menuOpen && (
          <div
            className="wallet-dropdown"
            role="menu"
            style={{
              position: 'absolute',
              right: 0,
              top: 'calc(100% + 6px)',
              minWidth: 220,
              background: 'var(--surface-1)',
              border: '1px solid var(--glass-border)',
              borderRadius: 10,
              boxShadow: '0 10px 30px rgba(0,0,0,0.45)',
              padding: 6,
              zIndex: 50,
            }}
          >
            <div
              className="caption"
              style={{ padding: '6px 10px', color: 'var(--text-4)', borderBottom: '1px solid var(--glass-border)', marginBottom: 4 }}
            >
              <div style={{ color: 'var(--text-2)', fontWeight: 600, fontSize: '0.85rem' }}>
                {displayName || 'Wallet'}
              </div>
              <div className="mono" style={{ fontSize: '0.72rem' }}>{shortAddr}</div>
            </div>
            <button
              type="button"
              role="menuitem"
              className="wallet-menu-item"
              onClick={() => { setMenuOpen(false); onEditProfile?.() }}
              style={{
                display: 'flex', alignItems: 'center', gap: 8,
                width: '100%', padding: '8px 10px', textAlign: 'left',
                background: 'transparent', border: 'none', color: 'var(--text-1)',
                fontSize: '0.85rem', cursor: 'pointer', borderRadius: 6,
              }}
            >
              <span className="i-lucide-user-pen" style={{ width: 16, height: 16 }} />
              <span>Profile</span>
            </button>
            <button
              type="button"
              role="menuitem"
              className="wallet-menu-item"
              onClick={() => { setMenuOpen(false); onDisconnect?.() }}
              style={{
                display: 'flex', alignItems: 'center', gap: 8,
                width: '100%', padding: '8px 10px', textAlign: 'left',
                background: 'transparent', border: 'none', color: 'var(--text-1)',
                fontSize: '0.85rem', cursor: 'pointer', borderRadius: 6,
              }}
            >
              <span className="i-lucide-log-out" style={{ width: 16, height: 16 }} />
              <span>Disconnect</span>
            </button>
          </div>
        )}
      </div>
    )
  }

  return (
    <>
      <button className="wallet-chip" onClick={() => setShowModal(true)}>
        Connect Wallet
      </button>

      {showModal && createPortal(
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3>Connect Wallet</h3>
            <p className="caption">Select a wallet to interact with Arc Testnet contracts.</p>

            {available.length === 0 ? (
              <div className="info-box warning mt-3">
                No wallets detected. Install <a href="https://metamask.io" target="_blank" rel="noreferrer">MetaMask</a> or{' '}
                <a href="https://www.coinbase.com/wallet" target="_blank" rel="noreferrer">Coinbase Wallet</a>.
              </div>
            ) : (
              <div className="wallet-options">
                {available.map(p => (
                  <button key={p.id} className="wallet-option" onClick={() => handleConnect(p.id)} disabled={busy}>
                    <span className={`wallet-icon ${p.icon} w-5 h-5`} />
                    <span>{p.name}</span>
                  </button>
                ))}
              </div>
            )}

            {error && <div className="status mt-3">{error}</div>}

            <button className="btn btn-outline mt-3 w-full" onClick={() => setShowModal(false)}>Cancel</button>
          </div>
        </div>,
        document.body
      )}
    </>
  )
}
