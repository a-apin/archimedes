export default function Landing({ onNavigate }) {
  return (
    <div className="min-h-screen bg-[var(--canvas)] overflow-x-hidden font-[var(--sans)]">

      {/* ── Hero ─────────────────────────────────────────────────── */}
      <section className="pt-6 pb-10 px-4 text-center border-b border-[var(--glass-border)] bg-[var(--canvas)] sm:px-6 sm:pb-12 lg:px-12 lg:pb-16">
        <div className="max-w-[760px] mx-auto">
          <span style={{
            display: 'inline-block',
            fontSize: '0.7rem',
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '0.12em',
            color: 'var(--accent)',
            border: '1px solid var(--accent)',
            borderRadius: '9999px',
            padding: '4px 16px',
            marginBottom: '20px',
            opacity: 0.85,
          }}>
            Agora Agents Hackathon 2026
          </span>
          <p className="font-serif italic text-[1rem] text-[var(--text-3)] mb-3 md:text-[1.1rem]">
            Linus for quantitative finance.
          </p>
          <h1 className="font-serif text-[2.1rem] font-normal leading-[1.15] mb-5 text-[var(--text-1)] sm:text-[2.6rem] lg:text-[3.1rem]">
            <span className="text-[var(--accent)]">Paper-Grounded.</span>{' '}
            <span className="text-[var(--text-3)]">Autonomously Managed.</span>
          </h1>
          <p className="text-[0.98rem] leading-[1.65] text-[var(--text-2)] max-w-[620px] mx-auto mb-8 md:text-[1.08rem]">
            Archimedes turns bleeding-edge academic research into rigor-gated,
            investable strategies — generated for your brief, executed in
            non-custodial vaults on Arc, with every decision hashed and
            verifiable on-chain.
          </p>
          <div className="flex flex-col items-stretch gap-3 sm:flex-row sm:justify-center sm:items-center">
            <button className="btn-primary" onClick={() => onNavigate('generate')}>
              Generate a Strategy →
            </button>
            <button className="btn-secondary" onClick={() => onNavigate('library', { tab: 'examples' })}>
              Browse Example Library
            </button>
          </div>
          <p className="caption mt-4 text-[var(--text-4)]">
            No wallet needed to generate. Wallet only required to deposit into a vault.
          </p>
        </div>
      </section>

      {/* ── Why Archimedes ───────────────────────────────────────── */}
      <LSection>
        <SectionTitle>Why Archimedes?</SectionTitle>
        <div className="lg-grid-4">
          <FeatureCard icon="file-text" title="Paper-Grounded Strategies" tag="SMA200 · TSMOM · Vol-Managed">
            Every strategy is extracted from bleeding-edge academic research in
            quantitative finance, machine learning, agentic systems, and pure
            mathematics. No vibes, no hype — academic rigor meets autonomous execution.
          </FeatureCard>
          <FeatureCard icon="link" title="On-Chain Provenance" tag="Commit-Reveal · Verifiable">
            Every rebalance decision gets a keccak256 hash anchored on Arc.
            Verify the agent's reasoning trail — before and after each trade.
          </FeatureCard>
          <FeatureCard icon="bot" title="Autonomous Agent" tag="Live · 5-min ticks">
            The agent evaluates 4 paper-grounded strategies against live market data,
            detects regime shifts, and rebalances autonomously — USDC on Arc.
          </FeatureCard>
          <FeatureCard icon="lock" title="Non-Custodial Vaults" tag="ERC-4626 · Your Keys">
            Your funds never pass through platform custody. ERC-4626 vault contracts
            hold your USDC and synth tokens — agent has rebalance authority only.
          </FeatureCard>
        </div>
      </LSection>

      {/* ── Built On ─────────────────────────────────────────────── */}
      <LSection>
        <SectionTitle>Built On</SectionTitle>
        <div className="flex gap-3 overflow-x-auto pb-1 justify-center flex-wrap lg:flex-nowrap max-w-full mx-auto">
          {[
            { name: 'Arc',         desc: 'EVM · Sub-second finality' },
            { name: 'Circle',      desc: 'USDC · Wallets · CCTP' },
            { name: 'Foundry',     desc: '10 deployed contracts' },
            { name: 'Claude',      desc: 'Strategy extraction · Reasoning' },
            { name: 'React + viem',desc: 'Frontend · Wallet UX' },
            { name: 'FastAPI',     desc: 'Backend · Agent runner' },
          ].map(t => (
            <div key={t.name} className="flex flex-col items-center px-5 py-3 min-w-[140px] flex-shrink-0 bg-[var(--surface-2)] border border-[var(--glass-border)] rounded-lg">
              <span className="text-sm font-semibold text-[var(--text-1)]">{t.name}</span>
              <span className="text-xs text-[var(--text-3)] mt-0.5">{t.desc}</span>
            </div>
          ))}
        </div>
      </LSection>

      {/* ── Rigor ────────────────────────────────────────────────── */}
      <LSection>
        <SectionTitle>Rigor is the Wedge</SectionTitle>
        <p className="text-sm text-center text-[var(--text-2)] mb-8 max-w-lg mx-auto">
          Every Tier 1 strategy passes four selection-bias correction gates before admission:
        </p>
        <div className="lg-grid-4">
          {[
            { n: '01', title: 'Deflated Sharpe Ratio',              desc: 'Bailey & López de Prado 2014 — corrects for multiple-testing inflation' },
            { n: '02', title: 'Probability of Backtest Overfitting', desc: 'Bailey/Borwein/López de Prado/Zhu 2014 — detects curve-fitting' },
            { n: '03', title: 'Walk-Forward OOS Sharpe',             desc: 'Rolling window validation with no in/out-of-sample cliff' },
            { n: '04', title: 'Look-Ahead Audit',                    desc: 'Static lint for future-leaking function calls in strategy code' },
          ].map(r => (
            <div key={r.n} className="bg-[var(--surface-1)] border border-[var(--glass-border)] rounded-xl p-6">
              <div className="font-mono text-[2.5rem] font-bold text-[var(--accent)] opacity-30 leading-none mb-2">{r.n}</div>
              <h4 className="text-sm font-semibold text-[var(--text-1)] mb-1.5">{r.title}</h4>
              <p className="text-xs text-[var(--text-3)] leading-relaxed">{r.desc}</p>
            </div>
          ))}
        </div>
      </LSection>

      {/* ── Circle Ecosystem (live integrations only) ───────────── */}
      <LSection>
        <SectionTitle>Circle Ecosystem Integration</SectionTitle>
        <div className="lg-grid-3">
          <FeatureCard icon="wallet"            title="Developer-Controlled Wallets" tag="Agent Signing · RSA-OAEP">
            Agent executes on-chain transactions (rebalances, trace publishing)
            via Circle-managed developer wallet with entity secret encryption.
          </FeatureCard>
          <FeatureCard icon="circle-dollar-sign" title="USDC Settlement"             tag="Native USDC · 6 decimals">
            All vault deposits, trades, and redemptions settle in USDC on Arc.
            Native USDC at 0x3600…0000 on Arc testnet.
          </FeatureCard>
          <FeatureCard icon="scroll-text"       title="Smart Contracts on Arc"       tag="10 Contracts · Foundry">
            10 Solidity contracts deployed: Vault, VaultFactory, AMMPool,
            AMMRouter, SyntheticFactory, PriceOracle, ReasoningTraceRegistry.
          </FeatureCard>
        </div>
      </LSection>

      {/* ── Footer band (closing tagline only) ──────────────────── */}
      <section className="py-6 px-4 text-center border-b border-[var(--glass-border)] sm:px-6 lg:py-8 lg:px-12">
        <p className="font-serif italic text-[1rem] text-[var(--text-3)] md:text-[1.05rem]">
          The lever is academic research. The fulcrum is autonomous AI. The world is your portfolio.
        </p>
        <p className="mt-3 text-xs text-[var(--text-4)]">
          Powered by{' '}
          <strong className="text-[var(--text-2)]">Arc</strong>
          {' × '}
          <strong className="text-[var(--text-2)]">Circle</strong>
          {' × '}
          <strong className="text-[var(--text-2)]">Canteen</strong>
        </p>
      </section>

    </div>
  )
}

/* ── Sub-components ──────────────────────────────────────────── */

function LSection({ children }) {
  return (
    <section className="l-section">
      {children}
    </section>
  )
}

function SectionTitle({ children }) {
  return (
    <h2 style={{fontFamily:'var(--serif)',fontSize:'1.6rem',fontWeight:400,textAlign:'center',marginBottom:'1.5rem',color:'var(--text-1)',width:'100%'}}>
      {children}
    </h2>
  )
}

function FeatureCard({ icon, title, tag, children }) {
  return (
    <div className="flex flex-col bg-[var(--surface-1)] border border-[var(--glass-border)] rounded-xl p-6 hover:border-[var(--accent)] transition-colors duration-200">
      <span className={`i-lucide-${icon} w-7 h-7 mb-3`} style={{color: '#D4A853'}} />
      <h3 className="text-[1rem] font-semibold mb-2 text-[var(--text-1)]">{title}</h3>
      <p className="text-[0.85rem] leading-[1.6] text-[var(--text-2)] mb-3 flex-1">{children}</p>
      <span className="inline-block self-start text-[0.7rem] font-semibold px-2.5 py-1 rounded" style={{color: '#D4A853', background: 'rgba(212,168,83,0.08)'}}>
        {tag}
      </span>
    </div>
  )
}
