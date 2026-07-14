# Archimedes agents subpackage.
#
# Houses the multi-agent architecture:
#   - strategy_fusion:   research-grounded strategy synthesis (Fusion agent)
#   - debate_engine:      multi-agent debate society (sole generation path, #834/#1064)
#   - portfolio_agent:    portfolio construction + rebalancing (Portfolio agent)
#   - generation_pipeline: unified entry point that auto-routes to the above
#   - generation_json:    shared LLM JSON-extraction + proposal DTOs
#
# The interactive Strategy Architect (single-agent generator) was retired
# in #1064 — the debate society is now the sole strategy-generation path.
#
# Re-exported from services/ for backwards compatibility.
