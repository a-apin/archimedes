"""Asset service — composes chain + oracle data into API responses."""

from __future__ import annotations

from archimedes.api.schemas import AssetListResponse, AssetResponse
from archimedes.chain.oracle_updater import OracleUpdater


class AssetService:
    """Serves asset data to the API layer."""

    def __init__(self):
        self.oracle = OracleUpdater()

    async def list_assets(self) -> AssetListResponse:
        """List all assets with current prices."""
        from archimedes.chain.client import chain_client

        assets: list[AssetResponse] = []

        # Add USDC
        assets.append(
            AssetResponse(
                address=chain_client.settings.usdc_address,
                symbol="USDC",
                name="USD Coin",
                asset_type="native",
                decimals=6,
                price_usd=1.0,
            )
        )

        # Add synthetic assets
        prices = await self.oracle.fetch_prices()
        price_map = {p.symbol: p for p in prices}

        for symbol, address in chain_client.settings.synth_addresses.items():
            if not address:
                continue

            price_data = price_map.get(symbol)
            price = price_data.price_usd if price_data else 0.0

            assets.append(
                AssetResponse(
                    address=address,
                    symbol=symbol,
                    name=f"Synthetic {symbol[1:]}",  # sTSLA → Synthetic TSLA
                    asset_type="synthetic",
                    decimals=18,
                    price_usd=price,
                    oracle_address=chain_client.settings.oracle_addresses.get(symbol),
                )
            )

        return AssetListResponse(assets=assets)
