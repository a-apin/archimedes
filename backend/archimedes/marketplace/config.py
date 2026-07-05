"""Shared configuration defaults for the marketplace money seam.

Single source of truth for chain-name defaults so that payment charging,
Gateway withdrawal, and settlement all read from the same constant.
"""

DEFAULT_GATEWAY_CHAIN = "arcTestnet"
