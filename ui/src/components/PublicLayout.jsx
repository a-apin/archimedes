export default function PublicLayout({ user, children }) {
  return (
    <div className="min-h-screen bg-[var(--canvas)]">
      <header className="flex items-center justify-between gap-4 px-4 py-3 border-b border-[var(--glass-border)] sm:px-6 lg:px-12">
        <a href="/" className="flex items-center gap-2 text-[var(--text-1)] no-underline">
          <span className="font-serif text-xl text-[var(--accent)]">Λ</span>
          <strong>Archimedes</strong>
        </a>
        <nav className="flex items-center gap-3" aria-label="Public navigation">
          <a href="/architecture" className="caption">Architecture</a>
          <a className={user ? 'btn-primary' : 'btn-secondary'} href={user ? '/app' : '/sign-in'}>
            {user ? 'Open app' : 'Sign in'}
          </a>
        </nav>
      </header>
      {children}
    </div>
  )
}
