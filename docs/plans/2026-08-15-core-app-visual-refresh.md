# Core App Visual Refresh Implementation Plan

> **REQUIRED SUB-SKILL:** Use the executing-plans skill to implement this plan task-by-task.

**Goal:** Give authenticated Generate → Strategy Passport → Portfolio journey same identity as public site, adapted into denser operational workspace.

**Architecture:** Add `.app-site` theme scope at shared shell so public design remains isolated and both authenticated themes keep working. Reuse existing components, data flow, controls, and route classes; add only structural class names and one truthful journey rail. Page-specific CSS turns existing markup into workbench, dossier, and portfolio-ledger layouts without new dependencies.

**Tech Stack:** React 19, Vite 8, UnoCSS utilities already installed, plain CSS, Node test runner.

---

### Task 1: Visual contract

**Files:**

- Create: `ui/test/app-visuals.test.js`

1. Assert `.app-site`, proof-rail stages, scoped dark/light tokens, visible focus, and reduced motion.
2. Assert Generate workbench/context rail, Passport dossier/authority rail, and Portfolio ledger/activity split.
3. Run `cd ui && npm test -- test/app-visuals.test.js`; expect failures because classes and styles do not exist.

### Task 2: Shared authenticated shell

**Files:**

- Modify: `ui/src/components/Layout.jsx`
- Modify: `ui/src/App.css`

1. Add `app-site` root scope and replace lambda tile with existing Archimedean spiral mark.
2. Add five-stage `Brief → Debate → Gate → Vault → Monitor` rail only to core route pages.
3. Add scoped tokens for dark and light themes; restyle sidebar, topbar, navigation, cards, inputs, buttons, focus, and mobile drawer.
4. Run focused visual-contract test; shell assertions must pass.

### Task 3: Generate workbench

**Files:**

- Modify: `ui/src/components/Generate.jsx`
- Modify: `ui/src/App.css`

1. Add explicit page heading and `generate-workbench` structure.
2. Keep brief form primary; move existing model selector into right context rail with truthful pipeline inputs.
3. Keep generation register below workbench and preserve all submission/error behavior.
4. Run focused test; Generate assertions must pass.

### Task 4: Strategy Passport dossier

**Files:**

- Modify: `ui/src/components/StrategyPassport.jsx`
- Modify: `ui/src/App.css`

1. Add dossier header and two-column workspace.
2. Keep deploy controls first in DOM; render them as sticky authority rail on desktop.
3. Group methodology, sources, backtest, rigor, and provenance as evidence column.
4. Preserve strictness/deploy gating and honest unknown/error states.
5. Run focused test; Passport assertions must pass.

### Task 5: Portfolio ledger

**Files:**

- Modify: `ui/src/components/Portfolio.jsx`
- Modify: `ui/src/App.css`

1. Convert four KPI cards into ruled ledger strip.
2. Wrap vault positions and stress controls in primary column; agent trace in secondary audit column.
3. Preserve wallet ownership math, polling, grouped skips, loading, and partial-read warnings.
4. Run focused test; Portfolio assertions must pass.

### Task 6: Verification and visual critique

**Files:**

- Verify all touched files.

1. Run `cd ui && npm test`; expect zero failures.
2. Run `cd ui && npm run lint`; expect no issues.
3. Run `cd ui && npm run build`; expect exit 0.
4. Run LSP and pi-lens diagnostics on touched files; expect no findings.
5. Capture desktop/tablet/mobile screenshots where local authentication permits; inspect overflow, hierarchy, contrast, focus, and honest API failure states.
6. Run `git diff --check`; expect no whitespace errors.

No dependency additions, API changes, wallet-flow changes, or authenticated-page feature changes. No commit without explicit request.
