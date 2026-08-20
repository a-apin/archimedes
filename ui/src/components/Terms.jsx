import PolicyBanner from "./PolicyBanner";

// /terms — the public terms of service.
//
// Same standard as Privacy.jsx: every factual claim about what the service
// does today is grounded in code, and the PR that added this page carries the
// statement-to-file map. The two claims most likely to rot, and the code that
// currently makes them true:
//
//   - "no real funds move" — PAYMENTS_DRY_RUN is pinned "true" in the live
//     task definition (infra/ecs.tf) and defaults to "true" in code
//     (backend/archimedes/main.py, services/generation_payment.py). If that
//     flag is ever flipped, THIS PAGE IS PART OF THAT CHANGE.
//   - the generation limits (10/account/day, 20/IP/day) — defaults in
//     backend/archimedes/services/generation_quota.py and .env.example. The
//     page says "currently" and names them as defaults rather than promising
//     them, so a tuning change doesn't instantly make the page false.
//
// One deliberate non-claim: on-chain activity itself is NOT simulated.
// AGENT_DRY_RUN defaults to false, so trace publishes and trades are real
// transactions — on a testnet, with test assets. The page says that plainly
// rather than letting "dry run" imply nothing happens.
export default function Terms() {
	return (
		<div className="page-content policy-page">
			<PolicyBanner />

			<header>
				<p className="public-kicker">Terms</p>
				<h1>Terms of Service</h1>
				<p className="policy-meta">
					Last updated: [pending owner approval] · Archimedes
					(archimedes-arc.com) is operated by APRIN&nbsp;Labs
				</p>
			</header>

			<section>
				<h2>What this is</h2>
				<p>
					Archimedes reads quantitative-finance research and turns it into
					candidate trading strategies, then puts each one through statistical
					tests designed to catch results that only look good. It runs on the Arc
					public testnet.
				</p>
				<p>
					Using the site means you accept these terms. If you do not, please do
					not use it.
				</p>
			</section>

			<section>
				<h2>No real money changes hands</h2>
				<p>
					This is a testnet service. The assets are test assets with no monetary
					value, obtained free from a faucet.
				</p>
				<p>
					The payment flow you may encounter — a price quote, a payment header,
					a receipt — runs end to end so it can be tested, but settlement is
					switched off in production. Nothing is charged, nothing is collected,
					and no balance is moved. If that ever changes, it will be an announced
					change with this page updated first, not a silent flip.
				</p>
				<p>
					One thing that is <em>not</em> simulated: transactions on the testnet
					are real transactions. Deploying a vault, publishing a trace, or
					executing a paper trade genuinely writes to a public chain. Real chain,
					real permanence — play money.
				</p>
				<p>
					<strong>
						Do not connect a wallet holding assets you care about.
					</strong>{" "}
					Use a fresh wallet made for this.
				</p>
			</section>

			<section>
				<h2>This is not investment advice</h2>
				<p>
					Nothing here is a recommendation to buy, sell, or hold anything. We are
					not a broker, an adviser, or a fiduciary, and no relationship of that
					kind is created by using the site.
				</p>
				<p>
					A strategy Archimedes produces is a research artifact with a
					statistical verdict attached — not a promise and not a forecast. The
					rigor gate exists to reduce known sources of false confidence:
					multiple-testing inflation, overfitting to one lucky sample,
					look-ahead leakage. Reducing those is not the same as predicting the
					future. Past performance, simulated or real, tells you nothing certain
					about what comes next, and no gate removes market risk.
				</p>
				<p>
					A verdict of &ldquo;pass&rdquo; means a strategy survived our tests. It
					does not mean it will make money. If you take anything from here into a
					live market, that decision and its consequences are entirely yours.
				</p>
			</section>

			<section>
				<h2>Your account</h2>
				<ul>
					<li>You must be 18 or older.</li>
					<li>
						Give accurate registration details and keep your password to
						yourself. You are responsible for what happens under your account.
					</li>
					<li>
						Accounts are for one person. Do not share, sell, or transfer one.
					</li>
					<li>
						Linking a wallet or a sign-in provider is something you do
						deliberately; we never merge accounts on your behalf.
					</li>
					<li>Tell us if you think your account has been compromised.</li>
				</ul>
			</section>

			<section>
				<h2>Limits and fair use</h2>
				<p>
					Generation costs us real compute, so it is capped. Currently the
					defaults are ten generations per account per day and twenty per network
					address per day, and individual endpoints are rate limited on top of
					that. These numbers are operational settings, not entitlements — we may
					change them, and we will not treat a limit as a promise.
				</p>
				<p>
					Do not try to get around the limits: creating extra accounts for that
					purpose, rotating addresses, or scripting the interface to defeat a cap
					are all misuse, whatever the technical means.
				</p>
			</section>

			<section>
				<h2>Acceptable use</h2>
				<p>Do not:</p>
				<ul>
					<li>Break the law, or help someone else break it, using this service.</li>
					<li>
						Attack, overload, probe, or attempt to gain unauthorised access to
						the service or anyone else&rsquo;s account or data.
					</li>
					<li>
						Use it to plan or carry out market manipulation, or to deceive people
						about a strategy&rsquo;s provenance or results.
					</li>
					<li>
						Present output from here as reviewed, endorsed, or guaranteed by us,
						or as advice from a licensed professional.
					</li>
					<li>
						Upload malicious content, or attempt to make the system act outside
						its intended function.
					</li>
					<li>Scrape or bulk-extract beyond what the interface offers.</li>
				</ul>
			</section>

			<section>
				<h2>What you make here</h2>
				<p>
					You keep whatever rights you have in the briefs you write and the
					strategies generated from them. To run the service we need permission
					to store, process, and show that material back to you, and to send your
					brief to the language model that answers it — that permission is what
					you are giving by using the product, and it is limited to operating and
					improving the service.
				</p>
				<p>
					Some actions publish deliberately: publishing a strategy to the
					marketplace, publishing a reasoning trace on-chain, or pinning a
					provenance record. Those make the material public, and they cannot be
					undone. Do not publish anything you would not want permanently
					readable by anyone.
				</p>
				<p>
					If you send us feedback or bug reports, we may act on them freely and
					without obligation to you.
				</p>
			</section>

			<section>
				<h2>Availability</h2>
				<p>
					This is early software under active development. Features appear,
					change, and are removed. We make no uptime commitment. Testnet state,
					including deployed contracts and data, may be reset — by us or by the
					network — and we may have to clear or rebuild data during that. Keep
					your own copy of anything you would be sorry to lose.
				</p>
			</section>

			<section>
				<h2>Suspension and termination</h2>
				<p>
					We may suspend or close an account that breaks these terms, abuses the
					service, or puts it or its users at risk — immediately where the risk
					is immediate, and otherwise with notice where we reasonably can. We may
					also stop offering the service entirely.
				</p>
				<p>
					You can stop using it whenever you like, and can ask us to delete your
					data as described in the{" "}
					<a href="/privacy">Privacy Policy</a>. Published on-chain and IPFS
					records survive account closure — nobody can delete those.
				</p>
			</section>

			<section>
				<h2>No warranty</h2>
				<p>
					The service is provided &ldquo;as is&rdquo; and &ldquo;as
					available&rdquo;, without warranties of any kind, express or implied,
					including any implied warranty of merchantability, fitness for a
					particular purpose, or non-infringement, to the fullest extent the law
					allows.
				</p>
				<p>
					Specifically, we do not warrant that generated strategies are correct,
					profitable, novel, or suitable for any purpose; that backtests are free
					of error; that the papers cited support the strategy as the model
					claims; or that the service will be uninterrupted or secure.
				</p>
			</section>

			<section>
				<h2>Limitation of liability</h2>
				<p>
					To the fullest extent the law allows, we are not liable for lost
					profits, lost data, trading losses, or any indirect or consequential
					loss arising out of your use of the service — including any decision
					you make on the basis of something it produced.
				</p>
				<p className="policy-meta">
					[OWNER TO SPECIFY] — whether to state a liability cap here, and what it
					should be.
				</p>
			</section>

			<section>
				<h2>Changes to these terms</h2>
				<p>
					We will update this page as the service changes, and the &ldquo;last
					updated&rdquo; line records when. If a change materially affects your
					rights or what we do, we will say so plainly rather than editing
					quietly. Continuing to use the service after a change means you accept
					the updated terms.
				</p>
			</section>

			<section>
				<h2>Governing law</h2>
				<p className="policy-meta">
					[OWNER TO SPECIFY] — governing law and the venue for disputes. Left
					blank deliberately: this is a jurisdiction question tied to where the
					operating entity is registered, and it is the owner&rsquo;s call, not a
					detail to guess at.
				</p>
			</section>

			<section>
				<h2>Contact</h2>
				<p>
					Questions about these terms go to the project&rsquo;s public issue
					tracker:{" "}
					<a
						href="https://github.com/a-apin/archimedes/issues"
						target="_blank"
						rel="noopener noreferrer"
					>
						github.com/a-apin/archimedes/issues
					</a>
					.
				</p>
			</section>
		</div>
	);
}
