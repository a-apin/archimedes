import { useState } from 'react'
import { createPortal } from 'react-dom'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''
const STORAGE_PREFIX = 'archimedes.welcomeProfileSeen.'

const INTEREST_OPTIONS = ['Equities', 'Bonds', 'Commodities', 'Crypto', 'FX']

// WelcomeProfileModal — two modes:
//   `mode="welcome"` (default): first wallet-connect prompt. Has a "Skip"
//     button; on submit OR skip, sets the localStorage gate so it never
//     re-shows for this wallet.
//   `mode="edit"`: invoked from the wallet menu dropdown to view + edit
//     an existing profile. Pre-fills from `existingProfile`. Has Cancel
//     instead of Skip; never touches the localStorage gate.
//
// All fields remain optional in both modes.
export default function WelcomeProfileModal({ walletAddr, onDone, mode = 'welcome', existingProfile = null }) {
  const isEdit = mode === 'edit'
  const [displayName, setDisplayName] = useState(existingProfile?.display_name || '')
  const [email, setEmail] = useState(existingProfile?.email || '')
  const [selectedInterests, setSelectedInterests] = useState(existingProfile?.interests || [])
  const [attribution, setAttribution] = useState(existingProfile?.attribution || '')
  const [marketingOptIn, setMarketingOptIn] = useState(existingProfile?.marketing_opt_in || false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const toggleInterest = (interest) => {
    setSelectedInterests(prev =>
      prev.includes(interest)
        ? prev.filter(i => i !== interest)
        : [...prev, interest]
    )
  }

  const markSeen = () => {
    // Edit mode never touches the gate — only the welcome flow sets it.
    if (isEdit) return
    if (walletAddr) {
      localStorage.setItem(STORAGE_PREFIX + walletAddr.toLowerCase(), '1')
    }
  }

  const handleCancel = () => {
    // Welcome mode: cancel = skip, sets the gate.
    // Edit mode: cancel = dismiss, no gate change.
    markSeen()
    onDone?.()
  }

  const handleSubmit = async () => {
    setError('')
    setSubmitting(true)
    try {
      const payload = {
        wallet_address: walletAddr,
        display_name: displayName.trim() || null,
        email: email.trim() || null,
        interests: selectedInterests.length > 0 ? selectedInterests : null,
        attribution: attribution.trim() || null,
        marketing_opt_in: marketingOptIn,
      }
      const res = await fetch(`${API_BASE}/api/user/profile`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || `Profile save failed (${res.status})`)
      }
      markSeen()
      onDone?.(await res.json())
    } catch (e) {
      setError(e.message || 'Failed to save profile')
    } finally {
      setSubmitting(false)
    }
  }

  return createPortal(
    <div
      className="fixed inset-0 flex items-center justify-center z-[1000]"
      style={{ background: 'rgba(0,0,0,0.78)', backdropFilter: 'blur(6px)' }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="welcome-modal-title"
    >
      <div
        className="card-elevated p-6 max-w-[520px] w-[92vw]"
        style={{ background: 'var(--surface-1)', maxHeight: '90vh', overflowY: 'auto' }}
      >
        <div className="caption mb-2 uppercase tracking-wider text-[var(--text-4)]">
          {isEdit ? 'Your Profile' : 'Welcome to Archimedes'}
        </div>
        <h3 id="welcome-modal-title" className="font-serif text-[1.5rem] mb-1">
          {isEdit ? 'Edit your profile' : 'Personalize your experience'}
        </h3>
        <p className="caption mb-4 leading-relaxed">
          {isEdit
            ? 'Update what we show alongside your wallet. All fields remain optional; leave any blank to clear it.'
            : 'All fields are optional. Your wallet is your identity — this just helps us show a friendly name and tailor the experience. You can skip this entirely.'}
        </p>

        <div className="grid grid-cols-1 gap-3">
          <label className="block">
            <span className="caption block mb-1">Display name</span>
            <input
              type="text"
              value={displayName}
              onChange={e => setDisplayName(e.target.value)}
              placeholder="Alice"
              maxLength={128}
              className="chat-input w-full p-2.5"
              disabled={submitting}
            />
          </label>

          <label className="block">
            <span className="caption block mb-1">Email <span className="text-[var(--text-4)]">(optional)</span></span>
            <input
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder="you@example.com"
              maxLength={256}
              className="chat-input w-full p-2.5"
              disabled={submitting}
            />
          </label>

          <div>
            <span className="caption block mb-2">Interests</span>
            <div className="flex flex-wrap gap-2">
              {INTEREST_OPTIONS.map(interest => (
                <button
                  key={interest}
                  type="button"
                  className={`tag ${selectedInterests.includes(interest) ? 'tag-positive' : 'tag-muted'}`}
                  onClick={() => toggleInterest(interest)}
                  disabled={submitting}
                  style={{ cursor: 'pointer' }}
                >
                  {interest}
                </button>
              ))}
            </div>
          </div>

          <label className="block">
            <span className="caption block mb-1">Attribution <span className="text-[var(--text-4)]">(optional)</span></span>
            <input
              type="text"
              value={attribution}
              onChange={e => setAttribution(e.target.value)}
              placeholder="How did you hear about us?"
              maxLength={256}
              className="chat-input w-full p-2.5"
              disabled={submitting}
            />
          </label>

          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={marketingOptIn}
              onChange={e => setMarketingOptIn(e.target.checked)}
              disabled={submitting}
            />
            <span className="body text-sm">Keep me updated on new strategies and features</span>
          </label>
        </div>

        {error && <div className="info-box warning mt-3">{error}</div>}

        <div className="flex justify-between gap-3 mt-5">
          <button
            className="btn btn-outline"
            onClick={handleCancel}
            disabled={submitting}
          >
            {isEdit ? 'Cancel' : 'Skip for now'}
          </button>
          <button
            className="btn btn-primary"
            onClick={handleSubmit}
            disabled={submitting}
          >
            {submitting ? 'Saving…' : (isEdit ? 'Save Changes' : 'Save Profile')}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
