"""Smart-wallet (ERC-1271 / ERC-6492) signature verification for SIWE.

Circle Modular Wallets (passkey wallets) are ERC-4337 smart CONTRACT accounts
with a WebAuthn P-256 owner — `eth_account.Account.recover_message` (secp256k1
EOA recovery) can never validate their signatures. The on-chain contract
validates them via ERC-1271 `isValidSignature(bytes32,bytes)`; a *counterfactual*
(not-yet-deployed) account instead ships an ERC-6492-wrapped signature carrying
its factory + factoryData so a verifier can deploy-and-check inside a sandboxed
`eth_call`.

Both cases (plus plain EOA ecrecover, as a fallback) are handled by ONE
deployless call: the Ambire **universal signature validator** — its constructor
runs the whole validation and "returns" the boolean as the deployed runtime
code, so `eth_call` with `data = bytecode ++ abi.encode(signer, hash, sig)`
and NO `to` address yields 0x01/0x00 without anything being deployed for real.

Provenance of the bytecode blob: viem's `erc6492SignatureValidatorByteCode`
(viem 2.x, `src/constants/contracts.ts`), byte-identical to the audited Ambire
reference implementation (github.com/AmbireTech/signature-validator). This is
the exact blob every viem `publicClient.verifyMessage` call uses — extracted
from the pinned dependency in `ui/node_modules`, not hand-typed.

Fail-closed: any RPC error, timeout, or non-0x01 result → NOT verified.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# viem erc6492SignatureValidatorByteCode — see module docstring for provenance.
ERC6492_VALIDATOR_BYTECODE = "0x608060405234801561001057600080fd5b5060405161069438038061069483398101604081905261002f9161051e565b600061003c848484610048565b9050806000526001601ff35b60007f64926492649264926492649264926492649264926492649264926492649264926100748361040c565b036101e7576000606080848060200190518101906100929190610577565b60405192955090935091506000906001600160a01b038516906100b69085906105dd565b6000604051808303816000865af19150503d80600081146100f3576040519150601f19603f3d011682016040523d82523d6000602084013e6100f8565b606091505b50509050876001600160a01b03163b60000361016057806101605760405162461bcd60e51b815260206004820152601e60248201527f5369676e617475726556616c696461746f723a206465706c6f796d656e74000060448201526064015b60405180910390fd5b604051630b135d3f60e11b808252906001600160a01b038a1690631626ba7e90610190908b9087906004016105f9565b602060405180830381865afa1580156101ad573d6000803e3d6000fd5b505050506040513d601f19601f820116820180604052508101906101d19190610633565b6001600160e01b03191614945050505050610405565b6001600160a01b0384163b1561027a57604051630b135d3f60e11b808252906001600160a01b03861690631626ba7e9061022790879087906004016105f9565b602060405180830381865afa158015610244573d6000803e3d6000fd5b505050506040513d601f19601f820116820180604052508101906102689190610633565b6001600160e01b031916149050610405565b81516041146102df5760405162461bcd60e51b815260206004820152603a602482015260008051602061067483398151915260448201527f3a20696e76616c6964207369676e6174757265206c656e6774680000000000006064820152608401610157565b6102e7610425565b5060208201516040808401518451859392600091859190811061030c5761030c61065d565b016020015160f81c9050601b811480159061032b57508060ff16601c14155b1561038c5760405162461bcd60e51b815260206004820152603b602482015260008051602061067483398151915260448201527f3a20696e76616c6964207369676e617475726520762076616c756500000000006064820152608401610157565b60408051600081526020810180835289905260ff83169181019190915260608101849052608081018390526001600160a01b0389169060019060a0016020604051602081039080840390855afa1580156103ea573d6000803e3d6000fd5b505050602060405103516001600160a01b0316149450505050505b9392505050565b600060208251101561041d57600080fd5b508051015190565b60405180606001604052806003906020820280368337509192915050565b6001600160a01b038116811461045857600080fd5b50565b634e487b7160e01b600052604160045260246000fd5b60005b8381101561048c578181015183820152602001610474565b50506000910152565b600082601f8301126104a657600080fd5b81516001600160401b038111156104bf576104bf61045b565b604051601f8201601f19908116603f011681016001600160401b03811182821017156104ed576104ed61045b565b60405281815283820160200185101561050557600080fd5b610516826020830160208701610471565b949350505050565b60008060006060848603121561053357600080fd5b835161053e81610443565b6020850151604086015191945092506001600160401b0381111561056157600080fd5b61056d86828701610495565b9150509250925092565b60008060006060848603121561058c57600080fd5b835161059781610443565b60208501519093506001600160401b038111156105b357600080fd5b6105bf86828701610495565b604086015190935090506001600160401b0381111561056157600080fd5b600082516105ef818460208701610471565b9190910192915050565b828152604060208201526000825180604084015261061e816060850160208701610471565b601f01601f1916919091016060019392505050565b60006020828403121561064557600080fd5b81516001600160e01b03198116811461040557600080fd5b634e487b7160e01b600052603260045260246000fdfe5369676e617475726556616c696461746f72237265636f7665725369676e6572"

_RPC_TIMEOUT_SECONDS = 8.0


def _eip191_hash(message_text: str) -> bytes:
    """The EIP-191 personal-message digest — identical to what
    `eth_account.messages.encode_defunct` + hashing produce, and the hash a
    wallet contract's `isValidSignature` expects for a personal_sign payload."""
    from eth_utils import keccak

    body = message_text.encode("utf-8")
    return keccak(b"\x19Ethereum Signed Message:\n" + str(len(body)).encode("ascii") + body)


async def _eth_call(rpc_url: str, data: str) -> str:
    """One deployless eth_call (no `to`). Isolated so tests mock THIS boundary."""
    from web3 import AsyncHTTPProvider, AsyncWeb3

    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url, request_kwargs={"timeout": _RPC_TIMEOUT_SECONDS}))
    try:
        result = await w3.eth.call({"data": data})
    finally:
        await w3.provider.disconnect()
    return result.to_0x_hex() if hasattr(result, "to_0x_hex") else "0x" + bytes(result).hex()


async def verify_smart_wallet_signature(wallet: str, message_text: str, signature: str, rpc_url: str) -> bool:
    """True iff `signature` over `message_text` is valid for the smart-contract
    wallet at `wallet` (ERC-1271 for deployed accounts, ERC-6492 for
    counterfactual ones). Never raises — every failure path returns False."""
    from eth_abi import encode as abi_encode

    try:
        sig_bytes = bytes.fromhex(signature.removeprefix("0x"))
        msg_hash = _eip191_hash(message_text)
        args = abi_encode(["address", "bytes32", "bytes"], [wallet, msg_hash, sig_bytes])
        data = ERC6492_VALIDATOR_BYTECODE + args.hex()
        result = await asyncio.wait_for(_eth_call(rpc_url, data), timeout=_RPC_TIMEOUT_SECONDS + 2)
        valid = int(result, 16) == 1
        if not valid:
            # A CLEAN 0x00 (validator ran, rejected) — distinct from an
            # exception (RPC/revert). Signals a hash- or signature-format
            # mismatch, not connectivity. Log enough to debug without leaking
            # the signature: hash + sig length + 6492-wrapped detection.
            is_6492 = sig_bytes[-32:].hex() == "6492" * 16 if len(sig_bytes) >= 32 else False
            logger.warning(
                "smart-wallet SIWE INVALID (validator ran, rejected) wallet=%s hash=%s sig_len=%d erc6492_wrapped=%s",
                wallet[:12],
                "0x" + msg_hash.hex(),
                len(sig_bytes),
                is_6492,
            )
        return valid
    except Exception as exc:
        # Fail closed: an unreachable RPC, a revert (e.g. undeployed account +
        # unwrapped sig), or a malformed signature must never authenticate. An
        # exception here is a DIFFERENT failure class than a clean 0x00 above.
        # Mirror the INVALID branch's correlation metadata (wallet + sig length)
        # so the two log lines can be joined. Compute byte length via fromhex so
        # the value is accurate; fall back to -1 when the string is non-hex or
        # not a string at all (hex-decode failure may itself be what raised).
        try:
            sig_len = len(bytes.fromhex(signature.removeprefix("0x"))) if isinstance(signature, str) else -1
        except (ValueError, AttributeError):
            sig_len = -1
        logger.warning(
            "smart-wallet SIWE ERRORED (fail-closed, %s) wallet=%s sig_len=%d: %s",
            type(exc).__name__,
            wallet[:12],
            sig_len,
            str(exc)[:300],
        )
        return False
