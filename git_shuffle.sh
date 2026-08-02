#!/bin/bash
set -e

# Delete the bad branches locally
git branch -D onder/1103-vault-oracle-redis || true
git branch -D onder/1120-strategy-visibility-dedup || true
git branch -D onder/794-pyth-adapter || true
git branch -D onder/827-aggregatorv3-consolidation || true

# Apply 1103 and 1120 to onder/audit-0712-backend-fixes
git checkout origin/onder/audit-0712-backend-fixes -b onder/audit-0712-backend-fixes || git checkout onder/audit-0712-backend-fixes
git checkout temp_work_backup -- backend/archimedes/services/vault_service.py backend/archimedes/chain/oracle_updater.py backend/archimedes/scripts/bootstrap_vaults.py backend/archimedes/services/strategy_visibility.py backend/tests/services/test_strategy_visibility.py backend/archimedes/api/selection_bias_routes.py backend/archimedes/api/strategies_routes.py backend/archimedes/api/vaults_routes.py
git add -A
git commit -m "Fix: Vault Redis Oracle snapshot, bootstrap SSOT, and Strategy visibility dedup (#1103, #1102, #1120)" || true

# Apply 794 and 827 to dbrowneup/stork-aggregator-adapter
git checkout origin/dbrowneup/stork-aggregator-adapter -b dbrowneup/stork-aggregator-adapter || git checkout dbrowneup/stork-aggregator-adapter
git checkout temp_work_backup -- contracts/src/PriceOracle.sol contracts/src/adapters/PythAdapter.sol contracts/test/PythAdapter.t.sol contracts/src/interfaces/IAggregatorV3.sol
git add -A
git commit -m "Feature: Add PythAdapter, and Consolidate IAggregatorV3 (#794, #827)" || true

# Return to main
git checkout main
