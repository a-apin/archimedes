.PHONY: setup wallet fund compile test deploy feed balance

# ─── Setup ───────────────────────────────────────────

setup:
	cd wallet-setup && npm install

register:
	cd wallet-setup && node --env-file=../.env register-entity-secret.mjs

wallet:
	cd wallet-setup && node --env-file=../.env create-wallet.mjs

fund:
	open https://console.circle.com/wallets/dev/wallets

# ─── Contracts ───────────────────────────────────────

compile:
	cd contracts && forge build

test:
	cd contracts && forge test -vv

deploy:
	cd wallet-setup && node --env-file=../.env deploy.mjs

# ─── Oracle ──────────────────────────────────────────

feed:
	cd wallet-setup && node --env-file=../.env feed-price.mjs

setvault:
	cd wallet-setup && node --env-file=../.env setvault.mjs

# ─── UI ─────────────────────────────────────────────

ui:
	cd ui && npm run dev

# ─── Queries ─────────────────────────────────────────

balance:
	cd wallet-setup && node --env-file=../.env -e "\
		import { initiateDeveloperControlledWalletsClient } from '@circle-fin/developer-controlled-wallets';\
		const c = initiateDeveloperControlledWalletsClient({ apiKey: process.env.CIRCLE_API_KEY, entitySecret: process.env.CIRCLE_ENTITY_SECRET });\
		const r = await c.getWalletTokenBalance({ id: process.env.WALLET_ID });\
		console.log(JSON.stringify(r.data, null, 2));"
