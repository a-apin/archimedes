---
name: cypherlexicon-offchain__ai-agents
description: LLM agent definitions for CypherLexicon — lexicon definition evaluator, agent prompts, AI scoring pipeline
triggers: [cypherlexicon ai, cypherlexicon agent, cypherlexicon llm, cypherlexicon evaluation, lexicon scoring]
---

# CypherLexicon AI Agents

**Source**: cypherlexicon-offchain
**Category**: Core

## When to use this skill
Working on AI agent definitions: system prompts, evaluation criteria, LLM integration for scoring lexicon definitions, or the AI pipeline.

## Key files and folders
- **Agent definitions + scoring**: `/home/ricardo/github/CypherLexicon-offchain/backend/core/agents.js`
  - Agent configs with system prompts (CN_Macro, Generic_AI, Asia_Expert)
  - `calculateScore()` — bid-based scoring with reputation weighting
  - `calculatePoints()` / `calculateRoyalty()` — reward math
- **Auction service (scoring usage)**: `/home/ricardo/github/CypherLexicon-offchain/backend/auction/service.js`
- **Tests**: `/home/ricardo/github/CypherLexicon-offchain/tests/core/agents.test.js`
- **Dependencies**: Anthropic SDK (`@anthropic-ai/sdk`) in `/home/ricardo/github/CypherLexicon-offchain/package.json`

## Key concepts
- Agents are defined as static config objects with system prompts for LLM translation/scoring
- Scoring formula: `normalizedBid * 0.40 + rep * 0.35 + confidenceScore * 0.25`
- Three agents: CN_Macro (Chinese macro), Generic_AI (general purpose), Asia_Expert (Asian geopolitics)
- Reputation scores are static: CN_Macro=0.85, Generic_AI=0.60, Asia_Expert=0.92
- Evaluations may be sent on-chain via blockchain layer

## Related skills
- See `.agents/skills/cypherlexicon-offchain__auction-service` — the auction logic that invokes AI scoring
- See `.agents/skills/cypherlexicon-offchain__blockchain-layer` — for sending scores on-chain
