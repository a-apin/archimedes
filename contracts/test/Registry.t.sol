// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/ReasoningTraceRegistry.sol";
import "../src/AssetRegistry.sol";

/// @dev Minimal Vault-shaped contract for testing live role introspection.
contract MockVaultWithAgent {
    address public agent;
    address public creator;

    constructor(address _agent, address _creator) {
        agent = _agent;
        creator = _creator;
    }
}

contract ReasoningTraceRegistryTest is Test {
    ReasoningTraceRegistry public registry;

    address public owner = address(0x1);
    address public agent1 = address(0x2);
    address public agent2 = address(0x3);
    address public attacker = address(0xBAD);
    address public vault1 = address(0x10);
    address public vault2 = address(0x11);

    function setUp() public {
        vm.prank(owner);
        registry = new ReasoningTraceRegistry(owner);

        // v1.5: anchoring is gated — grant the test agents authority over the
        // (codeless) test vault addresses.
        vm.startPrank(owner);
        registry.setVaultAgent(vault1, agent1, true);
        registry.setVaultAgent(vault1, agent2, true);
        registry.setVaultAgent(vault2, agent1, true);
        registry.setVaultAgent(vault2, agent2, true);
        vm.stopPrank();
    }

    // ─── Publish Trace ───────────────────────────────────────────────

    function test_publishTrace() public {
        bytes32 hash = keccak256("trace content 1");
        bytes memory metadata = abi.encode("rebalance", "regime_shift");

        vm.prank(agent1);
        uint256 traceId = registry.publishTrace(vault1, hash, metadata);

        assertEq(traceId, 1);
        assertEq(registry.traceCount(), 1);
    }

    function test_publishTrace_auto_increment() public {
        bytes32 hash1 = keccak256("trace 1");
        bytes32 hash2 = keccak256("trace 2");

        vm.prank(agent1);
        uint256 id1 = registry.publishTrace(vault1, hash1, "");

        vm.prank(agent2);
        uint256 id2 = registry.publishTrace(vault1, hash2, "");

        assertEq(id1, 1);
        assertEq(id2, 2);
        assertEq(registry.traceCount(), 2);
    }

    function test_publishTrace_emits_event() public {
        bytes32 hash = keccak256("trace");

        vm.expectEmit(true, true, true, true);
        emit IReasoningTraceRegistry.TracePublished(1, agent1, vault1, hash, block.timestamp);
        vm.prank(agent1);
        registry.publishTrace(vault1, hash, "");
    }

    // ─── Verify Trace ────────────────────────────────────────────────

    function test_verifyTrace_correct() public {
        bytes memory content = "the actual trace content";
        bytes32 hash = keccak256(content);

        vm.prank(agent1);
        registry.publishTrace(vault1, hash, "");

        assertTrue(registry.verifyTrace(1, content));
    }

    function test_verifyTrace_tampered() public {
        bytes memory content = "the actual trace content";
        bytes32 hash = keccak256(content);

        vm.prank(agent1);
        registry.publishTrace(vault1, hash, "");

        assertFalse(registry.verifyTrace(1, "tampered content"));
    }

    function test_verifyTrace_invalid_id() public view {
        assertFalse(registry.verifyTrace(0, "anything"));
        assertFalse(registry.verifyTrace(999, "anything"));
    }

    // ─── Get Traces by Agent ─────────────────────────────────────────

    function test_getTraces_by_agent() public {
        bytes32 hash1 = keccak256("trace 1");
        bytes32 hash2 = keccak256("trace 2");
        bytes32 hash3 = keccak256("trace 3");

        vm.prank(agent1);
        registry.publishTrace(vault1, hash1, "");

        vm.prank(agent2);
        registry.publishTrace(vault2, hash2, "");

        vm.prank(agent1);
        registry.publishTrace(vault1, hash3, "");

        // Agent1 has 2 traces (IDs 1 and 3)
        bytes32[] memory agent1Hashes = registry.getTraces(agent1, 0, 1);
        assertEq(agent1Hashes.length, 2);
        assertEq(agent1Hashes[0], hash1);
        assertEq(agent1Hashes[1], hash3);

        // Agent2 has 1 trace (ID 2)
        bytes32[] memory agent2Hashes = registry.getTraces(agent2, 0, 0);
        assertEq(agent2Hashes.length, 1);
        assertEq(agent2Hashes[0], hash2);
    }

    function test_getTraces_empty_agent() public view {
        bytes32[] memory hashes = registry.getTraces(address(0x99), 0, 5);
        assertEq(hashes.length, 0);
    }

    /// @dev audit 2026-06-14: a `from` past the end used to clamp `from =
    ///      ids.length` and then underflow `to - from`, reverting with a panic
    ///      instead of returning an empty array. An out-of-range start must now
    ///      return empty cleanly so pagination / verifiers don't see a fake revert.
    function test_getTraces_from_out_of_range_returns_empty() public {
        vm.prank(agent1);
        registry.publishTrace(vault1, keccak256("only trace"), "");

        // agent1 has exactly 1 trace (index 0). A start at/after the end is empty.
        bytes32[] memory past = registry.getTraces(agent1, 5, 10);
        assertEq(past.length, 0);

        // from == length (1) with to beyond end: previously panicked, now empty.
        bytes32[] memory atEnd = registry.getTraces(agent1, 1, 3);
        assertEq(atEnd.length, 0);

        // A valid query still returns the trace (to clamps to the last index).
        bytes32[] memory valid = registry.getTraces(agent1, 0, 99);
        assertEq(valid.length, 1);
    }

    // ─── Get Trace by ID ─────────────────────────────────────────────

    function test_getTraceById() public {
        bytes32 hash = keccak256("content");
        bytes memory metadata = abi.encode("decision_type", "trigger");

        vm.prank(agent1);
        registry.publishTrace(vault1, hash, metadata);

        (
            address agent,
            address vault,
            bytes32 traceHash,
            uint256 timestamp,
            bytes memory retMetadata
        ) = registry.getTraceById(1);

        assertEq(agent, agent1);
        assertEq(vault, vault1);
        assertEq(traceHash, hash);
        assertGt(timestamp, 0);
        assertEq(retMetadata, metadata);
    }

    function test_revert_getTraceById_invalid() public {
        vm.expectRevert("Invalid trace ID");
        registry.getTraceById(0);
    }

    function test_revert_getTraceById_nonexistent() public {
        vm.expectRevert("Invalid trace ID");
        registry.getTraceById(999);
    }

    // ─── Get Traces by Vault ─────────────────────────────────────────

    function test_getTracesByVault() public {
        vm.prank(agent1);
        registry.publishTrace(vault1, keccak256("1"), "");

        vm.prank(agent2);
        registry.publishTrace(vault1, keccak256("2"), "");

        vm.prank(agent1);
        registry.publishTrace(vault2, keccak256("3"), "");

        uint256[] memory vault1Ids = registry.getTracesByVault(vault1);
        assertEq(vault1Ids.length, 2);
        assertEq(vault1Ids[0], 1);
        assertEq(vault1Ids[1], 2);

        uint256[] memory vault2Ids = registry.getTracesByVault(vault2);
        assertEq(vault2Ids.length, 1);
        assertEq(vault2Ids[0], 3);
    }

    // ─── Multiple agents and vaults ──────────────────────────────────

    function test_traceCount() public {
        assertEq(registry.traceCount(), 0);

        vm.prank(agent1);
        registry.publishTrace(vault1, keccak256("1"), "");
        assertEq(registry.traceCount(), 1);

        vm.prank(agent2);
        registry.publishTrace(vault2, keccak256("2"), "");
        assertEq(registry.traceCount(), 2);
    }

    // ─── Authorization (audit #18: no forged anchors) ────────────────

    function test_revert_publishTrace_unauthorized() public {
        vm.prank(attacker);
        vm.expectRevert("Not authorized for vault");
        registry.publishTrace(vault1, keccak256("forged"), "");
    }

    function test_revert_commit_unauthorized() public {
        vm.prank(attacker);
        vm.expectRevert("Not authorized for vault");
        registry.commit(vault1, keccak256("forged"), uint64(block.timestamp + 60), keccak256("trade"), "");
    }

    function test_revert_setVaultAgent_non_owner() public {
        vm.prank(attacker);
        vm.expectRevert();
        registry.setVaultAgent(vault1, attacker, true);
    }

    function test_setVaultAgent_revoke() public {
        vm.prank(owner);
        registry.setVaultAgent(vault1, agent1, false);

        vm.prank(agent1);
        vm.expectRevert("Not authorized for vault");
        registry.publishTrace(vault1, keccak256("trace"), "");
    }

    function test_isAuthorizedForVault_via_vault_roles() public {
        // A Vault-shaped contract exposing agent()/creator() is recognized live,
        // without an explicit setVaultAgent grant.
        address vaultAgent = address(0x20);
        address vaultCreator = address(0x21);
        MockVaultWithAgent vault = new MockVaultWithAgent(vaultAgent, vaultCreator);

        assertTrue(registry.isAuthorizedForVault(address(vault), vaultAgent));
        assertTrue(registry.isAuthorizedForVault(address(vault), vaultCreator));
        assertFalse(registry.isAuthorizedForVault(address(vault), attacker));

        vm.prank(vaultAgent);
        uint256 traceId = registry.commit(
            address(vault), keccak256("trace"), uint64(block.timestamp + 60), keccak256("trade"), ""
        );
        assertEq(traceId, 1);
    }

    // ─── Commit-reveal (v1.5 temporal binding) ───────────────────────

    function test_commit_reveal_happy_path() public {
        bytes memory content = "canonical trace json";
        bytes32 contentHash = keccak256(content);
        uint64 executionTime = uint64(block.timestamp + 10);
        bytes memory intent = abi.encode("rebalance", uint256(3), uint256(50_000e6));

        // T-2: commit BEFORE the trade
        vm.expectEmit(true, true, true, true);
        emit IReasoningTraceRegistry.TraceCommitted(
            1, contentHash, agent1, vault1, block.number, executionTime
        );
        vm.prank(agent1);
        uint256 traceId = registry.commit(vault1, contentHash, executionTime, keccak256("trade"), intent);
        assertEq(traceId, 1);

        // T-3: next block — the trade executes (simulated by advancing the chain)
        vm.roll(block.number + 1);
        vm.warp(executionTime);

        // T-5: reveal AFTER settlement
        vm.roll(block.number + 1);
        vm.warp(executionTime + 5);
        vm.expectEmit(true, false, false, true);
        emit IReasoningTraceRegistry.TraceRevealed(1, "ipfs://QmTrace", block.number);
        vm.prank(agent1);
        registry.reveal(traceId, "ipfs://QmTrace", content);

        // Commitment is promoted to revealed with the temporal binding recorded
        (
            bytes32 storedHash,
            address committer,
            address vault,
            uint256 commitBlock,
            uint64 claimedExecutionTime,
            bool revealed,
            uint256 revealBlock,
            string memory storagePointer
        ) = registry.getCommitment(traceId);

        assertEq(storedHash, contentHash);
        assertEq(committer, agent1);
        assertEq(vault, vault1);
        assertEq(claimedExecutionTime, executionTime);
        assertTrue(revealed);
        assertGt(revealBlock, commitBlock); // commit block strictly precedes reveal block
        assertEq(storagePointer, "ipfs://QmTrace");

        // The committed trace is also verifiable through the standard v1 read path
        assertTrue(registry.verifyTrace(traceId, content));
        assertEq(registry.traceCount(), 1);
    }

    function test_revert_reveal_without_commitment() public {
        // No commit at all
        vm.prank(agent1);
        vm.expectRevert("No commitment");
        registry.reveal(999, "ipfs://x", "content");

        // A v1 publishTrace anchor is NOT a commitment — reveal against it reverts
        vm.prank(agent1);
        uint256 publishedId = registry.publishTrace(vault1, keccak256("content"), "");
        vm.roll(block.number + 1);
        vm.prank(agent1);
        vm.expectRevert("No commitment");
        registry.reveal(publishedId, "ipfs://x", "content");
    }

    function test_revert_reveal_hash_mismatch() public {
        vm.prank(agent1);
        uint256 traceId = registry.commit(
            vault1, keccak256("the real trace"), uint64(block.timestamp + 10), keccak256("trade"), ""
        );

        vm.roll(block.number + 1);
        vm.warp(block.timestamp + 10);

        vm.prank(agent1);
        vm.expectRevert("Hash mismatch");
        registry.reveal(traceId, "ipfs://x", "a tampered trace");
    }

    function test_revert_commit_same_block_as_execution() public {
        // Time-lock: claiming execution in the commit block itself must fail —
        // the covered trade has to land at least one block after the commit.
        vm.prank(agent1);
        vm.expectRevert("Time-lock: execution must follow commit");
        registry.commit(vault1, keccak256("trace"), uint64(block.timestamp), keccak256("trade"), "");

        // Claimed execution in the past fails too
        vm.warp(block.timestamp + 100);
        vm.prank(agent1);
        vm.expectRevert("Time-lock: execution must follow commit");
        registry.commit(vault1, keccak256("trace"), uint64(block.timestamp - 1), keccak256("trade"), "");
    }

    function test_revert_reveal_in_commit_block() public {
        bytes memory content = "trace";
        vm.prank(agent1);
        uint256 traceId = registry.commit(
            vault1, keccak256(content), uint64(block.timestamp + 1), keccak256("trade"), ""
        );

        // Same block as the commit — temporal binding cannot hold
        vm.warp(block.timestamp + 1);
        vm.prank(agent1);
        vm.expectRevert("Time-lock: reveal in commit block");
        registry.reveal(traceId, "ipfs://x", content);
    }

    function test_revert_reveal_before_claimed_execution() public {
        bytes memory content = "trace";
        uint64 executionTime = uint64(block.timestamp + 100);
        vm.prank(agent1);
        uint256 traceId = registry.commit(vault1, keccak256(content), executionTime, keccak256("trade"), "");

        vm.roll(block.number + 1); // later block, but execution time not reached
        vm.prank(agent1);
        vm.expectRevert("Reveal before claimed execution");
        registry.reveal(traceId, "ipfs://x", content);
    }

    function test_revert_reveal_not_committer() public {
        bytes memory content = "trace";
        vm.prank(agent1);
        uint256 traceId = registry.commit(
            vault1, keccak256(content), uint64(block.timestamp + 1), keccak256("trade"), ""
        );

        vm.roll(block.number + 1);
        vm.warp(block.timestamp + 1);
        vm.prank(agent2);
        vm.expectRevert("Not committer");
        registry.reveal(traceId, "ipfs://x", content);
    }

    function test_revert_reveal_twice() public {
        bytes memory content = "trace";
        vm.prank(agent1);
        uint256 traceId = registry.commit(
            vault1, keccak256(content), uint64(block.timestamp + 1), keccak256("trade"), ""
        );

        vm.roll(block.number + 1);
        vm.warp(block.timestamp + 1);
        vm.startPrank(agent1);
        registry.reveal(traceId, "ipfs://x", content);

        vm.expectRevert("Already revealed");
        registry.reveal(traceId, "ipfs://y", content);
        vm.stopPrank();
    }

    function test_revert_commit_empty_hash() public {
        vm.prank(agent1);
        vm.expectRevert("Empty content hash");
        registry.commit(vault1, bytes32(0), uint64(block.timestamp + 1), keccak256("trade"), "");
    }

    function test_revert_getCommitment_nonexistent() public {
        vm.expectRevert("No commitment");
        registry.getCommitment(999);
    }

    function test_commit_shares_trace_id_space_with_publishTrace() public {
        vm.prank(agent1);
        uint256 id1 = registry.publishTrace(vault1, keccak256("v1 anchor"), "");

        vm.prank(agent1);
        uint256 id2 = registry.commit(
            vault1, keccak256("committed"), uint64(block.timestamp + 1), keccak256("trade"), ""
        );

        assertEq(id1, 1);
        assertEq(id2, 2);
        assertEq(registry.traceCount(), 2);

        uint256[] memory vaultIds = registry.getTracesByVault(vault1);
        assertEq(vaultIds.length, 2);
    }

    // ─── Commit-before-trade binding (#589) ──────────────────────────

    function test_revert_commit_empty_tradeId() public {
        vm.prank(agent1);
        vm.expectRevert("Empty trade id");
        registry.commit(vault1, keccak256("trace"), uint64(block.timestamp + 1), bytes32(0), "");
    }

    function test_revert_commit_duplicate_pending() public {
        bytes32 tradeId = keccak256("trade-A");
        vm.prank(agent1);
        registry.commit(vault1, keccak256("t1"), uint64(block.timestamp + 1), tradeId, "");
        // A second commit for the SAME (vault, tradeId) before it executes must revert.
        vm.prank(agent1);
        vm.expectRevert("Pending commitment exists");
        registry.commit(vault1, keccak256("t2"), uint64(block.timestamp + 1), tradeId, "");
    }

    function test_revert_executeTrade_no_commitment() public {
        // The vault marks its own trade (msg.sender == vault); no prior commit => revert.
        vm.prank(vault1);
        vm.expectRevert("No matching commitment");
        registry.executeTrade(keccak256("trade-A"));
    }

    function test_executeTrade_consumes_and_is_single_use() public {
        bytes32 tradeId = keccak256("trade-A");
        vm.prank(agent1);
        uint256 traceId = registry.commit(vault1, keccak256("trace"), uint64(block.timestamp + 1), tradeId, "");
        assertEq(registry.pendingTradeCommitment(vault1, tradeId), traceId);

        // Strictly later block, then the vault consumes the commitment.
        vm.roll(block.number + 1);
        vm.expectEmit(true, true, true, true);
        emit IReasoningTraceRegistry.TradeExecuted(traceId, vault1, tradeId, block.number);
        vm.prank(vault1);
        uint256 consumed = registry.executeTrade(tradeId);
        assertEq(consumed, traceId);
        assertEq(registry.pendingTradeCommitment(vault1, tradeId), 0); // cleared

        // Single-use: a second execute for the same trade reverts.
        vm.prank(vault1);
        vm.expectRevert("No matching commitment");
        registry.executeTrade(tradeId);
    }

    function test_revert_executeTrade_same_block_as_commit() public {
        bytes32 tradeId = keccak256("trade-A");
        vm.prank(agent1);
        registry.commit(vault1, keccak256("trace"), uint64(block.timestamp + 1), tradeId, "");
        // Same block as the commit — the trade cannot prove the commit preceded it.
        vm.prank(vault1);
        vm.expectRevert("Time-lock: execute in commit block");
        registry.executeTrade(tradeId);
    }

    function test_executeTrade_only_consumes_own_vault_commitment() public {
        // A commitment for vault1 cannot be consumed by a different vault address.
        bytes32 tradeId = keccak256("trade-A");
        vm.prank(agent1);
        registry.commit(vault1, keccak256("trace"), uint64(block.timestamp + 1), tradeId, "");
        vm.roll(block.number + 1);
        vm.prank(vault2);
        vm.expectRevert("No matching commitment");
        registry.executeTrade(tradeId);
    }
}

contract AssetRegistryTest is Test {
    AssetRegistry public registry;

    address public owner = address(0x1);
    address public nonOwner = address(0x2);

    function setUp() public {
        vm.prank(owner);
        registry = new AssetRegistry(owner);
    }

    // ─── Synthetic Assets ────────────────────────────────────────────

    function test_registerSynthetic() public {
        address token = address(0x100);
        bytes32 oracleId = keccak256("TSLA/USD");
        bytes memory metadata = abi.encode("Synthetic TSLA", "sTSLA", 18);

        vm.prank(owner);
        registry.registerSynthetic(token, oracleId, metadata);

        bytes memory retMeta = registry.getSynthetic(token);
        assertEq(retMeta, metadata);
    }

    function test_getAllSynthetics() public {
        address t1 = address(0x100);
        address t2 = address(0x101);

        vm.startPrank(owner);
        registry.registerSynthetic(t1, keccak256("TSLA"), "");
        registry.registerSynthetic(t2, keccak256("NVDA"), "");
        vm.stopPrank();

        address[] memory synthetics = registry.getAllSynthetics();
        assertEq(synthetics.length, 2);
        assertEq(synthetics[0], t1);
        assertEq(synthetics[1], t2);
    }

    function test_revert_registerSynthetic_duplicate() public {
        address token = address(0x100);

        vm.startPrank(owner);
        registry.registerSynthetic(token, keccak256("TSLA"), "");
        vm.expectRevert("Already registered");
        registry.registerSynthetic(token, keccak256("TSLA"), "");
        vm.stopPrank();
    }

    function test_revert_registerSynthetic_non_owner() public {
        vm.prank(nonOwner);
        vm.expectRevert();
        registry.registerSynthetic(address(0x100), keccak256("TSLA"), "");
    }

    // ─── Bridged Assets ──────────────────────────────────────────────

    function test_registerBridged() public {
        address token = address(0x200);

        vm.prank(owner);
        registry.registerBridged(token, 1, address(0x300)); // from Ethereum mainnet

        // Verify it was registered (no revert means success)
        assertTrue(true);
    }

    function test_revert_registerBridged_non_owner() public {
        vm.prank(nonOwner);
        vm.expectRevert();
        registry.registerBridged(address(0x200), 1, address(0x300));
    }

    // ─── Vaults ──────────────────────────────────────────────────────

    function test_registerVault_tier1() public {
        address vault = address(0x400);
        bytes memory metadata = abi.encode("Momentum Alpha", "vMOM");

        vm.prank(owner);
        registry.registerVault(vault, 1, metadata);

        assertEq(registry.vaultCount(), 1);
    }

    function test_registerVault_tier2() public {
        address vault = address(0x401);

        vm.prank(owner);
        registry.registerVault(vault, 2, "");

        assertEq(registry.vaultCount(), 1);
    }

    function test_revert_registerVault_duplicate() public {
        address vault = address(0x400);

        vm.startPrank(owner);
        registry.registerVault(vault, 1, "");
        vm.expectRevert("Already registered");
        registry.registerVault(vault, 1, "");
        vm.stopPrank();
    }

    // ─── Update Vault Metrics ────────────────────────────────────────

    function test_updateVaultMetrics() public {
        address vault = address(0x400);

        vm.prank(owner);
        registry.registerVault(vault, 1, "");

        uint256 newAum = 1_000_000 * 10**6;
        bytes memory metrics = abi.encode(newAum, 1500, 2.5e18); // AUM, returns, Sharpe

        vm.prank(owner);
        registry.updateVaultMetrics(vault, metrics);
    }

    function test_revert_updateVaultMetrics_not_registered() public {
        bytes memory metrics = abi.encode(uint256(1000));

        vm.prank(owner);
        vm.expectRevert("Vault not registered");
        registry.updateVaultMetrics(address(0x999), metrics);
    }

    // ─── Leaderboard ─────────────────────────────────────────────────

    function test_getLeaderboard_sorted_by_aum() public {
        address v1 = address(0x400);
        address v2 = address(0x401);
        address v3 = address(0x402);

        vm.startPrank(owner);
        registry.registerVault(v1, 1, "");
        registry.registerVault(v2, 1, "");
        registry.registerVault(v3, 2, "");

        // Set AUMs
        registry.updateVaultMetrics(v1, abi.encode(uint256(1000)));
        registry.updateVaultMetrics(v2, abi.encode(uint256(3000)));
        registry.updateVaultMetrics(v3, abi.encode(uint256(2000)));
        vm.stopPrank();

        // Get all tiers leaderboard
        address[] memory leaderboard = registry.getLeaderboard(0, 0, 0);
        assertEq(leaderboard.length, 3);
        assertEq(leaderboard[0], v2); // AUM 3000
        assertEq(leaderboard[1], v3); // AUM 2000
        assertEq(leaderboard[2], v1); // AUM 1000
    }

    function test_getLeaderboard_filter_by_tier() public {
        address v1 = address(0x400);
        address v2 = address(0x401);

        vm.startPrank(owner);
        registry.registerVault(v1, 1, "");
        registry.registerVault(v2, 2, "");

        registry.updateVaultMetrics(v1, abi.encode(uint256(5000)));
        registry.updateVaultMetrics(v2, abi.encode(uint256(1000)));
        vm.stopPrank();

        // Tier 1 only
        address[] memory tier1 = registry.getLeaderboard(1, 0, 0);
        assertEq(tier1.length, 1);
        assertEq(tier1[0], v1);

        // Tier 2 only
        address[] memory tier2 = registry.getLeaderboard(2, 0, 0);
        assertEq(tier2.length, 1);
        assertEq(tier2[0], v2);
    }

    function test_getLeaderboard_with_limit() public {
        address v1 = address(0x400);
        address v2 = address(0x401);
        address v3 = address(0x402);

        vm.startPrank(owner);
        registry.registerVault(v1, 1, "");
        registry.registerVault(v2, 1, "");
        registry.registerVault(v3, 1, "");

        registry.updateVaultMetrics(v1, abi.encode(uint256(1000)));
        registry.updateVaultMetrics(v2, abi.encode(uint256(3000)));
        registry.updateVaultMetrics(v3, abi.encode(uint256(2000)));
        vm.stopPrank();

        // Limit to top 2
        address[] memory top2 = registry.getLeaderboard(0, 0, 2);
        assertEq(top2.length, 2);
        assertEq(top2[0], v2); // 3000
        assertEq(top2[1], v3); // 2000
    }

    /// @dev #927: pages via (offset, limit) must be sorted and DISJOINT.
    function test_getLeaderboard_pagination_disjoint_pages() public {
        address v1 = address(0x400);
        address v2 = address(0x401);
        address v3 = address(0x402);

        vm.startPrank(owner);
        registry.registerVault(v1, 1, "");
        registry.registerVault(v2, 1, "");
        registry.registerVault(v3, 1, "");
        registry.updateVaultMetrics(v1, abi.encode(uint256(1000)));
        registry.updateVaultMetrics(v2, abi.encode(uint256(3000)));
        registry.updateVaultMetrics(v3, abi.encode(uint256(2000)));
        vm.stopPrank();

        // Sorted order is [v2 (3000), v3 (2000), v1 (1000)].
        address[] memory page0 = registry.getLeaderboard(0, 0, 2); // offset 0, limit 2
        assertEq(page0.length, 2);
        assertEq(page0[0], v2);
        assertEq(page0[1], v3);

        address[] memory page1 = registry.getLeaderboard(0, 2, 2); // offset 2, limit 2
        assertEq(page1.length, 1); // only one vault left past offset 2
        assertEq(page1[0], v1);

        // The two pages are disjoint and together cover the whole set.
        assertTrue(page0[0] != page1[0] && page0[1] != page1[0]);

        // An offset at or beyond the end returns empty (not a revert).
        assertEq(registry.getLeaderboard(0, 3, 2).length, 0);
    }

    function test_getLeaderboard_empty() public view {
        address[] memory leaderboard = registry.getLeaderboard(0, 0, 0);
        assertEq(leaderboard.length, 0);
    }

    // ─── vaultCount ──────────────────────────────────────────────────

    function test_vaultCount() public {
        assertEq(registry.vaultCount(), 0);

        vm.prank(owner);
        registry.registerVault(address(0x400), 1, "");
        assertEq(registry.vaultCount(), 1);

        vm.prank(owner);
        registry.registerVault(address(0x401), 2, "");
        assertEq(registry.vaultCount(), 2);
    }
}
