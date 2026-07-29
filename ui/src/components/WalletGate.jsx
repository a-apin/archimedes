// Wallet-gate wrapper. Renders a Connect-Wallet CTA card when no wallet
// is connected; renders children when one is. Used to gate per-user
// surfaces (Library, Portfolio, Learnings) so the logged-out experience
// doesn't render "$0.00 across 0 vaults you created", "Your Strategies",
// or "27 traces (you've deployed)" — all of which imply personalization
// the user doesn't actually have without a wallet.
//
// Public pages (Generate, Corpus, Reasoning, Explore, Landing) deliberately
// do NOT use this gate — they're either browse-only or paper-grounded
// and useful without a wallet.

export default function WalletGate({ walletAddr, pageName, description, onConnect, children }) {
  if (walletAddr) return children

  return (
    <div className="wallet-gate" style={{
      maxWidth: 560,
      margin: '64px auto',
      padding: 32,
      background: 'var(--surface-1)',
      border: '1px solid var(--glass-border)',
      borderRadius: 14,
      textAlign: 'center',
    }}>
      <div style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 56,
        height: 56,
        borderRadius: '50%',
        background: 'rgba(var(--accent-rgb), 0.10)',
        border: '1px solid rgba(var(--accent-rgb), 0.30)',
        marginBottom: 18,
      }}>
        <span className="i-lucide-lock" style={{ width: 22, height: 22, color: 'var(--accent)' }} aria-hidden="true" />
      </div>
      <h2 style={{ margin: 0, marginBottom: 8, fontFamily: 'var(--font-serif)' }}>
        Connect to view {pageName}
      </h2>
      <p className="caption" style={{ color: 'var(--text-3)', maxWidth: 440, margin: '0 auto 20px', lineHeight: 1.55 }}>
        {description}
      </p>
      <button
        type="button"
        className="btn btn-primary"
        onClick={onConnect}
        style={{ minWidth: 200 }}
      >
        Connect Wallet
      </button>
      <p className="caption" style={{ marginTop: 20, fontSize: '0.72rem', color: 'var(--text-4)' }}>
        Connection requires signature proof before wallet links to account. Circle passkey controls Circle wallet only.
      </p>
    </div>
  )
}
