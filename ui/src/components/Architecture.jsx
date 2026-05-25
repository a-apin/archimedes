// /architecture — single-page infographic explaining the agent + memory +
// corpus architecture that powers Generate. Linked from Generate's
// collapsible "How this works" panel and surfaced in the sidebar so
// curious users can dig in without breaking the Generate-first spine.

const AGENTS = [
  {
    name: 'Strategy Generation Agent',
    role: 'What should be done?',
    desc: 'Retrieves relevant papers from a 9,873-paper q-fin corpus, reads current market context, and synthesizes a candidate strategy that passes the rigor gate.',
    subagents: [
      { name: 'Paper Retrieval', detail: 'SPECTER2 nearest-neighbour + KG entity walk' },
      { name: 'Market Context', detail: 'Regime classifier + on-chain oracle + price history' },
      { name: 'Strategy Synthesis', detail: 'LLM fusion: brief × papers × market' },
      { name: 'Rigor Gate', detail: 'DSR + PBO + walk-forward OOS + look-ahead audit' },
    ],
    output: 'Strategy passport with paper anchors + rigor verdict',
    authority: 'None — pure synthesis',
  },
  {
    name: 'Portfolio Construction Agent',
    role: 'How exactly do we do it?',
    desc: 'Turns the strategy into a concrete set of assets, weights, and position sizes, and stress-tests it across six adverse scenarios.',
    subagents: [
      { name: 'Asset Selection', detail: 'Individual instruments, paper-anchored' },
      { name: 'Sizing', detail: 'Kelly criterion + risk parity + USDC floor' },
      { name: 'Stress Test', detail: 'Six scenario shocks (2008, COVID, vol spike, etc.)' },
    ],
    output: 'Vault deployment proposal (target weights, projected behavior)',
    authority: 'None — produces the proposal you sign',
  },
  {
    name: 'Live Execution Agent',
    role: 'Given the vault is funded, what do we do this minute?',
    desc: 'Continuous rebalance loop in the agent docker service. Each tick: read vault state from chain, evaluate the strategy DSL, decide rebalance vs hold, anchor the trace.',
    subagents: [
      { name: 'Signal Evaluation', detail: 'Does the strategy DSL say rebalance?' },
      { name: 'Drift Calculation', detail: 'Target weights vs current weights' },
      { name: 'Cost-Benefit', detail: 'Is the rebalance worth the fee + slippage?' },
      { name: 'Trade Execution', detail: 'Circle signer → AMM swap on Arc' },
      { name: 'Trace Publishing', detail: 'Canonical hash → ReasoningTraceRegistry' },
    ],
    output: 'Rebalance tx (or honest "hold" trace) — both anchored on-chain',
    authority: 'Bounded — rebalance within signed allocations only',
  },
]

const MEMORY_LAYERS = [
  {
    tag: 'A.1',
    name: 'Intra-step latent',
    substrate: 'LLM KV cache (GLM-4.7 via z.ai)',
    lifetime: 'Single forward pass',
    why: 'Where one synthesis step does its thinking.',
  },
  {
    tag: 'A.2',
    name: 'Deterministic state · audit-truth',
    substrate: 'On-chain vault state (read-only to the agent)',
    lifetime: 'Live, externally written',
    why: 'Ground truth for "what is my current position." Chain wins if the LLM\'s narrative diverges.',
  },
  {
    tag: 'B',
    name: 'Within-session scratchpad',
    substrate: 'Redis (SSE event log per job)',
    lifetime: 'Single session',
    why: 'Streams progress + carries reasoning state across sub-agent steps.',
  },
  {
    tag: 'C',
    name: 'Cross-session episodic',
    substrate: 'Postgres (StrategyStore, vaults, traces)',
    lifetime: 'Persistent',
    why: 'How the library compounds: every proposal, verdict, and reject is content-hashed and recallable.',
  },
  {
    tag: 'D',
    name: 'Investigation memory',
    substrate: 'Per-job event log + recent-traces buffer',
    lifetime: 'Task-scoped',
    why: 'Lets the Live Execution Agent reason about its own recent history without re-querying Postgres.',
  },
  {
    tag: 'E',
    name: 'Semantic knowledge',
    substrate: 'q-fin corpus (9,873 papers, SPECTER2 + clusters + KG)',
    lifetime: 'Persistent',
    why: 'The substrate the Strategy Generation Agent retrieves from.',
  },
]

const PROTOCOLS = [
  { name: 'Outcome Embargo', what: 'Papers retrieved at decision time are filtered to those published before the decision; on-chain anchor proves the filter held.' },
  { name: 'Time-Aware Retrieval', what: 'SPECTER2 similarity decays by paper age; higher decay in volatile regimes.' },
  { name: 'Hierarchy of Truth', what: 'Chain state outranks LLM narrative; peer-reviewed papers outrank uncurated sources.' },
  { name: 'Source Tracking', what: 'Every cited paper carries (arxiv_id, version, content_hash). Anchored on Arc; anyone can recompute.' },
]

function PageHeader() {
  return (
    <div className="max-w-[820px] mb-7">
      <h2 className="serif text-[2rem] mb-2.5">How Archimedes works</h2>
      <p className="body mb-2">
        A multi-agent system that turns a plain-English brief into a deployable on-chain vault —
        paper-anchored, rigor-gated, and auditable end to end.
      </p>
      <p className="body" style={{ color: 'var(--text-3)' }}>
        Three top-level agents, six memory layers, a 9,873-paper q-fin corpus, and the{' '}
        <code>ReasoningTraceRegistry</code> on Arc anchoring every decision.
      </p>
    </div>
  )
}

function HeroStrip() {
  const stats = [
    { n: '3', l: 'Top-level agents', s: 'Generation · Construction · Execution' },
    { n: '6', l: 'Memory layers', s: 'KV cache → on-chain ground truth' },
    { n: '9,873', l: 'q-fin papers', s: 'SPECTER2 + clusters + KG' },
    { n: '10', l: 'Smart contracts', s: 'Deployed on Arc testnet' },
  ]
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-7">
      {stats.map(s => (
        <div key={s.l} className="card-flat p-4">
          <div className="text-[1.8rem] font-bold">{s.n}</div>
          <div className="label mt-1">{s.l}</div>
          <div className="caption mt-1.5" style={{ color: 'var(--text-3)' }}>{s.s}</div>
        </div>
      ))}
    </div>
  )
}

function PipelineCell({ title, sub }) {
  return (
    <div className="card-flat p-3" style={{ minWidth: 0 }}>
      <div className="font-semibold" style={{ fontSize: '0.92rem' }}>{title}</div>
      <div className="caption mt-1" style={{ color: 'var(--text-3)' }}>{sub}</div>
    </div>
  )
}

function PipelineArrow() {
  return (
    <div className="hidden md:flex items-center justify-center" style={{ color: 'var(--accent)', fontSize: '1.2rem' }}>→</div>
  )
}

function PipelineFlow() {
  // Two-row horizontal flow: brief → generation → construction; deploy → execution → verify.
  return (
    <div className="card p-5 mb-7">
      <div className="label mb-3">The pipeline</div>
      <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr_auto_1fr] gap-3 items-stretch">
        <PipelineCell title="Your brief" sub="plain English, optional asset classes + risk profile" />
        <PipelineArrow />
        <PipelineCell title="Strategy Generation" sub="paper retrieval · market context · synthesis · rigor gate" />
        <PipelineArrow />
        <PipelineCell title="Portfolio Construction" sub="asset selection · Kelly sizing · stress test" />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr_auto_1fr] gap-3 items-stretch mt-3">
        <PipelineCell title="Deploy as Vault" sub="4 wallet signatures: create → approve → deposit → set allocations" />
        <PipelineArrow />
        <PipelineCell title="Live Execution" sub="60s tick rebalance loop · on-chain trace per decision" />
        <PipelineArrow />
        <PipelineCell title="Verify on-chain" sub="recompute hash · check against Arc anchor" />
      </div>
      <p className="caption mt-3" style={{ color: 'var(--text-3)' }}>
        You stay in the loop at two binding moments: reviewing the passport and signing
        the four deploy transactions. The agent gains rebalance authority only — it cannot
        withdraw, cannot change allocations, cannot change vault ownership.
      </p>
    </div>
  )
}

function AgentCards() {
  return (
    <div className="mb-7">
      <div className="label mb-3">The three agents</div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {AGENTS.map(a => (
          <div key={a.name} className="card p-4">
            <div className="font-semibold mb-1" style={{ fontSize: '1rem' }}>{a.name}</div>
            <div className="caption mb-3" style={{ color: 'var(--accent)' }}>{a.role}</div>
            <p className="body mb-3" style={{ fontSize: '0.9rem', lineHeight: 1.5 }}>{a.desc}</p>
            <div className="label mb-2">Sub-agents</div>
            <ul className="mb-3" style={{ paddingLeft: '1.1rem', margin: 0, fontSize: '0.85rem' }}>
              {a.subagents.map(s => (
                <li key={s.name} style={{ marginBottom: 4 }}>
                  <strong>{s.name}</strong> — <span style={{ color: 'var(--text-3)' }}>{s.detail}</span>
                </li>
              ))}
            </ul>
            <div className="caption" style={{ color: 'var(--text-3)' }}>
              <div><strong>Output:</strong> {a.output}</div>
              <div className="mt-1"><strong>Trade authority:</strong> {a.authority}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function MemoryPillar() {
  return (
    <div className="mb-7">
      <div className="label mb-3">The 5-layer shared memory pillar</div>
      <p className="caption mb-3" style={{ color: 'var(--text-3)' }}>
        Adapted from a 5-layer cognitive memory model, with the on-chain audit-truth layer
        split out so the LLM can never hallucinate over real vault state.
      </p>
      <div className="flex flex-col gap-2">
        {MEMORY_LAYERS.map(m => (
          <div
            key={m.tag}
            className="card-flat"
            style={{
              padding: '12px 16px',
              display: 'grid',
              gridTemplateColumns: 'auto 1fr 1fr 1fr',
              gap: 16,
              alignItems: 'center',
            }}
          >
            <span
              className="font-bold"
              style={{
                background: 'var(--accent)',
                color: 'var(--bg-1)',
                padding: '4px 10px',
                borderRadius: 4,
                fontSize: '0.82rem',
                minWidth: 38,
                textAlign: 'center',
              }}
            >
              {m.tag}
            </span>
            <div>
              <div className="font-semibold" style={{ fontSize: '0.9rem' }}>{m.name}</div>
              <div className="caption mt-0.5" style={{ color: 'var(--text-3)' }}>{m.lifetime}</div>
            </div>
            <div className="caption" style={{ fontSize: '0.82rem' }}>{m.substrate}</div>
            <div className="caption" style={{ color: 'var(--text-3)', fontSize: '0.82rem' }}>{m.why}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function CorpusPanel() {
  return (
    <div className="card p-5 mb-7">
      <div className="label mb-3">The q-fin corpus — Layer E in detail</div>
      <p className="body mb-4">
        9,873 peer-reviewed papers ingested via PyMuPDF, embedded with SPECTER2, clustered
        with HDBSCAN, and linked with REBEL + SciSpacy into a knowledge graph. The
        Strategy Generation Agent's <strong>Paper Retrieval</strong> sub-agent uses this
        substrate every time you submit a brief.
      </p>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { n: '5,000', l: 'q-fin foundations', s: 'arxiv q-fin.*' },
          { n: '2,000', l: 'ML for finance', s: 'cs.LG + stat.ML' },
          { n: '500', l: 'Agentic AI', s: 'TradingAgents, Xia, StockBench, …' },
          { n: '1,500', l: 'Mathematics', s: 'optimization · probability · stats' },
        ].map(b => (
          <div key={b.l} className="card-flat p-3">
            <div className="font-bold" style={{ fontSize: '1.1rem' }}>{b.n}</div>
            <div className="caption" style={{ color: 'var(--text-1)' }}>{b.l}</div>
            <div className="caption" style={{ color: 'var(--text-3)' }}>{b.s}</div>
          </div>
        ))}
      </div>
      <p className="caption mt-3" style={{ color: 'var(--text-3)' }}>
        Seed target: ~10,000 papers. Currently ingested: 9,873 — REBEL knowledge-graph extraction
        on the full set runs as we expand the corpus.
      </p>
    </div>
  )
}

function ProtocolsPanel() {
  return (
    <div className="card p-5 mb-7">
      <div className="label mb-3">Reasoning protocols</div>
      <p className="body mb-3">
        Four named protocols from Xia et al. 2026 close the most common failure modes in
        trading-agent design. Archimedes implements all four as enforced mechanisms.
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {PROTOCOLS.map(p => (
          <div key={p.name} className="card-flat p-3">
            <div className="font-semibold mb-1" style={{ fontSize: '0.92rem' }}>{p.name}</div>
            <div className="caption" style={{ color: 'var(--text-3)' }}>{p.what}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function OnChainPanel() {
  return (
    <div className="card p-5 mb-7">
      <div className="label mb-3">On-chain — what lives on Arc</div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="card-flat p-3">
          <div className="font-semibold mb-1">Vault</div>
          <div className="caption" style={{ color: 'var(--text-3)' }}>
            ERC-4626 non-custodial container. You own the shares; the agent has rebalance
            authority only.
          </div>
        </div>
        <div className="card-flat p-3">
          <div className="font-semibold mb-1">ReasoningTraceRegistry</div>
          <div className="caption" style={{ color: 'var(--text-3)' }}>
            Every agent decision is canonical-hashed and anchored. Verify by recomputing the
            hash and checking it against the on-chain anchor.
          </div>
        </div>
        <div className="card-flat p-3">
          <div className="font-semibold mb-1">PriceOracle &amp; AMM</div>
          <div className="caption" style={{ color: 'var(--text-3)' }}>
            Per-synth oracle pushes provide ground-truth prices; AMM router routes the
            rebalance swaps.
          </div>
        </div>
      </div>
    </div>
  )
}

function CallToAction({ onNavigate }) {
  return (
    <div className="card p-5 text-center">
      <h3 className="serif mb-2" style={{ fontSize: '1.4rem' }}>Ready to try it?</h3>
      <p className="body mb-4" style={{ color: 'var(--text-3)', maxWidth: 520, margin: '0 auto 16px' }}>
        Describe a strategy in plain English. The pipeline above runs on your brief — no
        wallet required to generate; wallet only needed to deploy a vault.
      </p>
      <div className="flex justify-center gap-3 flex-wrap">
        <button className="btn btn-primary" onClick={() => onNavigate?.('generate')}>
          Generate a Strategy →
        </button>
        <button className="btn btn-outline" onClick={() => onNavigate?.('corpus')}>
          Explore the Corpus
        </button>
      </div>
    </div>
  )
}

export default function Architecture({ onNavigate }) {
  return (
    <div>
      <PageHeader />
      <HeroStrip />
      <PipelineFlow />
      <AgentCards />
      <MemoryPillar />
      <CorpusPanel />
      <ProtocolsPanel />
      <OnChainPanel />
      <CallToAction onNavigate={onNavigate} />
    </div>
  )
}
