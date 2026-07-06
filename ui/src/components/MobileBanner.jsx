import { useState, useEffect } from 'react'

const STORAGE_KEY = 'archimedes:mobile-banner-dismissed'

export default function MobileBanner() {
  const [show, setShow] = useState(false)

  useEffect(() => {
    if (localStorage.getItem(STORAGE_KEY)) return
    const mq = window.matchMedia('(max-width: 767px)')
    const update = () => setShow(mq.matches)
    update()
    mq.addEventListener('change', update)
    return () => mq.removeEventListener('change', update)
  }, [])

  if (!show) return null

  return (
    <div
      onClick={() => { localStorage.setItem(STORAGE_KEY, '1'); setShow(false) }}
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 9999,
        padding: '8px 16px',
        background: 'var(--surface-2)',
        borderBottom: '1px solid var(--glass-border)',
        color: 'var(--text-3)',
        fontSize: '0.78rem',
        textAlign: 'center',
        cursor: 'pointer',
      }}
    >
      <span className="i-lucide-smartphone w-3.5 h-3.5" /> Best on desktop · Mobile is functional for browsing. <span style={{ textDecoration: 'underline' }}>Tap to dismiss.</span>
    </div>
  )
}
