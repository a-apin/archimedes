import { useEffect, useState } from "react";

import { disconnectWallet, reconnectWallet } from "./config";
import { listLinkedWallets } from "./linked-wallets";
import AccountSettings from "./components/AccountSettings";
import CorpusExplorer from "./components/CorpusExplorer";
import Explore from "./components/Explore";
import Generate from "./components/Generate";
import Insights from "./components/Insights";
import Leaderboard from "./components/Leaderboard";
import Layout from "./components/Layout";
import Learnings from "./components/Learnings";
import MarketplacePage from "./components/MarketplacePage";
import OnboardingTour, {
	hasCompletedOnboarding,
} from "./components/OnboardingTour";
import Portfolio from "./components/Portfolio";
import PublishPage from "./components/PublishPage";
import QuantLab from "./components/QuantLab";
import Reasoning from "./components/Reasoning";
import Strategies from "./components/Strategies";
import StrategyDetailPage from "./components/StrategyDetailPage";
import StrategyPassport from "./components/StrategyPassport";
import SubscriptionsPage from "./components/SubscriptionsPage";
import VaultDetail from "./components/VaultDetail";
import WalletGate from "./components/WalletGate";

const openConnectModal = () =>
	window.dispatchEvent(new Event("open-wallet-modal"));

export default function AuthenticatedApp({
	route,
	features,
	navigateToPage,
	user,
}) {
	const [walletAddr, setWalletAddr] = useState(null);
	const [tourOpen, setTourOpen] = useState(() => !hasCompletedOnboarding());
	const [journeyStage, setJourneyStage] = useState(null);

	useEffect(() => {
		if (route.page !== "generate") setJourneyStage(null);
	}, [route.page]);

	useEffect(() => {
		reconnectWallet().then(async (result) => {
			if (!result) return;
			try {
				const wallets = await listLinkedWallets();
				if (
					wallets.some(
						(wallet) =>
							wallet.address === result.address.toLowerCase() &&
							wallet.chain_id === 5042002,
					)
				) {
					setWalletAddr(result.address);
				}
			} catch {
				// Account remains usable without wallet service.
			}
		});
	}, []);

	useEffect(() => {
		const handler = async (event) => {
			const address = event.detail.address;
			if (!address) {
				setWalletAddr(null);
				return;
			}
			try {
				const wallets = await listLinkedWallets();
				setWalletAddr(
					wallets.some((wallet) => wallet.address === address.toLowerCase())
						? address
						: null,
				);
			} catch {
				setWalletAddr(null);
			}
		};
		window.addEventListener("wallet-changed", handler);
		return () => window.removeEventListener("wallet-changed", handler);
	}, []);

	const handleDisconnect = () => {
		disconnectWallet();
		setWalletAddr(null);
	};
	const selectVault = (address) =>
		navigateToPage("vault-detail", { vaultAddress: address });
	const selectTrace = (traceId) => navigateToPage("reasoning", { traceId });

	const renderPage = () => {
		switch (route.page) {
			case "explore":
				return <Explore />;
			case "leaderboard":
				return <Leaderboard />;
			case "generate":
				return (
					<Generate
						onNavigate={navigateToPage}
						onStageChange={setJourneyStage}
					/>
				);
			case "library":
				return (
					<Strategies
						highlightStrategyId={route.highlight}
						defaultTab={route.tab}
						onNavigate={navigateToPage}
					/>
				);
			case "strategy":
				return (
					<StrategyPassport
						strategyId={route.strategyId}
						onNavigate={navigateToPage}
						walletAddr={walletAddr}
					/>
				);
			case "corpus":
				return <CorpusExplorer />;
			case "quant":
				return <QuantLab />;
			case "portfolio":
				return (
					<WalletGate
						walletAddr={walletAddr}
						pageName="Portfolio"
						description="Portfolio needs a verified linked wallet because vault deposits and withdrawals are on-chain actions."
						onConnect={openConnectModal}
					>
						<Portfolio
							walletAddr={walletAddr}
							onSelectVault={selectVault}
							onSelectTrace={selectTrace}
							onNavigate={navigateToPage}
						/>
					</WalletGate>
				);
			case "reasoning":
				return <Reasoning onNavigate={navigateToPage} />;
			case "learnings":
				return (
					<WalletGate
						walletAddr={walletAddr}
						pageName="Learnings"
						description="Link wallet controlling your deployed vaults to review their outcomes."
						onConnect={openConnectModal}
					>
						<Learnings onNavigate={navigateToPage} />
					</WalletGate>
				);
			case "insights":
				return <Insights />;
			case "vault-detail":
				return (
					<VaultDetail
						address={route.vaultAddress}
						onBack={() => navigateToPage("portfolio")}
					/>
				);
			case "marketplace":
				return <MarketplacePage onNavigate={navigateToPage} />;
			case "market-strategy":
				return (
					<StrategyDetailPage
						strategyId={route.strategyId}
						onNavigate={navigateToPage}
					/>
				);
			case "publish":
				return <PublishPage onNavigate={navigateToPage} />;
			case "subscriptions":
				return <SubscriptionsPage onNavigate={navigateToPage} />;
			case "account":
				return (
					<AccountSettings
						walletAddr={walletAddr}
						onDisconnect={handleDisconnect}
					/>
				);
			default:
				return null;
		}
	};

	return (
		<>
			<Layout
				page={route.page}
				setPage={navigateToPage}
				walletAddr={walletAddr}
				onConnect={setWalletAddr}
				onDisconnect={handleDisconnect}
				onOpenTour={() => setTourOpen(true)}
				user={user}
				features={features}
				journeyStage={journeyStage}
			>
				{renderPage()}
			</Layout>
			<OnboardingTour
				open={tourOpen}
				onClose={() => {
					setTourOpen(false);
					try {
						localStorage.setItem("archimedes.onboarding.v1", "completed");
					} catch {
						/* non-fatal */
					}
				}}
				setPage={navigateToPage}
			/>
		</>
	);
}
