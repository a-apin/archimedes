"""Shared configuration defaults for the marketplace money seam.

Single source of truth for chain-name defaults so that payment charging,
Gateway withdrawal, and settlement all read from the same constant.
"""

import os

DEFAULT_GATEWAY_CHAIN = "arcTestnet"


def gateway_chain() -> str:
    """The Circle Gateway chain every money-path caller must settle on.

    Centralised because the default being shared was not enough. Three call
    sites read ``GATEWAY_CHAIN`` and applied this default; a fourth
    (``services/revenue_sweep.py``) passed the constant straight through as a
    keyword argument and never consulted the environment at all, so pointing
    the deployment at mainnet moved the paywall and left the revenue sweep on
    testnet (#1495).

    That divergence was invisible: a sweep querying an empty testnet balance
    logs "below threshold — skip", which on day one of mainnet is exactly what
    a working system looks like.

    Reading the environment through one function means a new call site cannot
    reintroduce the split by forgetting a ``getenv``; the only way to obtain the
    chain is to ask for it.
    """
    return os.getenv("GATEWAY_CHAIN", DEFAULT_GATEWAY_CHAIN).strip()
