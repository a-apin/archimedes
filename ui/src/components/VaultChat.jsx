import { useState, useEffect, useRef, useCallback } from "react";
import { getAddress } from "../config";
import { apiGet, apiPost } from "../api";

// ─── Message Bubble ─────────────────────────────────────────

function MessageBubble({ msg, isOwn }) {
	const addr = msg.wallet_address || "";
	const shortAddr =
		addr.length > 12 ? `${addr.slice(0, 6)}...${addr.slice(-4)}` : addr;

	const time = msg.created_at
		? new Date(msg.created_at).toLocaleTimeString([], {
				hour: "2-digit",
				minute: "2-digit",
			})
		: "";

	return (
		<div
			className={`chat-msg ${msg.is_ai ? "chat-msg-ai" : ""} ${isOwn ? "chat-msg-own" : ""}`}
		>
			<div className="chat-msg-header">
				<span className="chat-msg-avatar">
					<span
						className={msg.is_ai ? "i-lucide-bot" : "i-lucide-user"}
						style={{ width: 14, height: 14 }}
					/>
				</span>
				<span className="chat-msg-sender">
					{msg.is_ai ? "Archimedes AI" : shortAddr}
				</span>
				<span className="chat-msg-time">{time}</span>
			</div>
			<div className="chat-msg-body">
				{msg.message.split("\n").map((line, i) => {
					// Simple bold rendering for AI messages: **text** → <strong>
					const parts = msg.is_ai
						? line.split(/(\*\*[^*]+\*\*)/g).map((part, j) => {
								if (part.startsWith("**") && part.endsWith("**")) {
									return <strong key={j}>{part.slice(2, -2)}</strong>;
								}
								return <span key={j}>{part}</span>;
							})
						: [line];
					return (
						<span key={i}>
							{parts}
							{i < msg.message.split("\n").length - 1 && <br />}
						</span>
					);
				})}
			</div>
		</div>
	);
}

// ─── Chat Panel (embedded in vault detail) ──────────────────

export default function VaultChat({ vaultAddress, isOpen = true, onToggle }) {
	const [messages, setMessages] = useState([]);
	const [input, setInput] = useState("");
	const [sending, setSending] = useState(false);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState("");
	const messagesEndRef = useRef(null);
	const inputRef = useRef(null);
	const wallet = getAddress();

	const scrollToBottom = () => {
		messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
	};

	const loadMessages = useCallback(async () => {
		if (!vaultAddress) return;
		try {
			const data = await apiGet(`/api/vaults/${vaultAddress}/chat?limit=100`);
			setMessages(data.messages || []);
			setLoading(false);
		} catch (err) {
			setError(err.message);
			setLoading(false);
		}
	}, [vaultAddress]);

	// Initial load + polling
	useEffect(() => {
		loadMessages();
		const interval = setInterval(loadMessages, 5000); // Poll every 5s
		return () => clearInterval(interval);
	}, [loadMessages]);

	// Auto-scroll on new messages
	useEffect(() => {
		scrollToBottom();
	}, [messages.length]);

	// Focus input when chat opens
	useEffect(() => {
		if (isOpen && inputRef.current) {
			inputRef.current.focus();
		}
	}, [isOpen]);

	const sendMessage = async () => {
		const text = input.trim();
		if (!text || sending) return;
		if (!wallet) {
			setError("Connect your wallet to send messages");
			return;
		}

		setSending(true);
		setError("");
		try {
			const result = await apiPost(`/api/vaults/${vaultAddress}/chat`, {
				wallet_address: wallet,
				message: text,
			});

			// Add user message immediately
			setMessages((prev) => [...prev, result.message]);

			// Add AI response if triggered
			if (result.ai_response) {
				setMessages((prev) => [...prev, result.ai_response]);
			}

			setInput("");
		} catch (err) {
			setError(err.message);
		}
		setSending(false);
	};

	const handleKeyDown = (e) => {
		if (e.key === "Enter" && !e.shiftKey) {
			e.preventDefault();
			sendMessage();
		}
	};

	if (!vaultAddress) return null;

	return (
		<div
			className={`chat-panel ${isOpen ? "chat-panel-open" : "chat-panel-collapsed"}`}
		>
			{/* Header */}
			<button
				type="button"
				className="chat-header"
				onClick={onToggle}
				aria-expanded={isOpen}
			>
				<span className="chat-header-left">
					<span
						className="chat-icon i-lucide-message-circle"
						style={{ width: 15, height: 15 }}
						aria-hidden="true"
					/>
					<span className="chat-title">Vault Chat</span>
					<span className="chat-count">{messages.length}</span>
				</span>
				<span
					className={`chat-toggle ${isOpen ? "i-lucide-chevron-down" : "i-lucide-chevron-up"}`}
					style={{ width: 14, height: 14 }}
					aria-hidden="true"
				/>
			</button>

			{isOpen && (
				<>
					{/* Messages */}
					<div className="chat-messages">
						{loading ? (
							<div className="chat-empty">Loading messages…</div>
						) : messages.length === 0 ? (
							<div className="chat-empty">
								<div style={{ marginBottom: 8 }}>
									<span
										className="i-lucide-message-circle"
										style={{
											width: 28,
											height: 28,
											display: "block",
											margin: "0 auto",
											color: "var(--text-3)",
										}}
									/>
								</div>
								<div>No messages yet</div>
								<div className="chat-hint">
									Be the first to say something, or @archimedes to talk to the
									AI
								</div>
							</div>
						) : (
							messages.map((msg) => (
								<MessageBubble
									key={msg.id}
									msg={msg}
									isOwn={
										!msg.is_ai &&
										msg.wallet_address?.toLowerCase() === wallet?.toLowerCase()
									}
								/>
							))
						)}
						<div ref={messagesEndRef} />
					</div>

					{/* Input */}
					<div className="chat-input-area">
						{error && (
							<div className="chat-error" role="alert">
								{error}
							</div>
						)}
						{wallet ? (
							<div className="chat-input-row">
								<input
									ref={inputRef}
									type="text"
									name="vault_chat_message"
									aria-label="Message"
									className="chat-input"
									value={input}
									onChange={(e) => setInput(e.target.value)}
									onKeyDown={handleKeyDown}
									placeholder="Type a message… (@archimedes to summon AI)"
									disabled={sending}
								/>
								<button
									type="button"
									className="chat-send-btn"
									onClick={sendMessage}
									disabled={sending || !input.trim()}
									aria-label={sending ? "Sending message" : "Send message"}
								>
									<span
										className={sending ? "i-lucide-loader" : "i-lucide-send"}
										style={{ width: 14, height: 14 }}
										aria-hidden="true"
									/>
								</button>
							</div>
						) : (
							<div className="chat-hint" style={{ padding: "12px 16px" }}>
								Connect your wallet to join the conversation
							</div>
						)}
					</div>
				</>
			)}
		</div>
	);
}
