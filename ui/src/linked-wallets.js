import { apiDelete, apiGet, apiPost } from './api'
import { CIRCLE_PROVIDER_ID, signSiweMessage } from './config'

const providerName = (provider) => {
  if (provider === CIRCLE_PROVIDER_ID) return 'circle'
  if (provider?.toLowerCase().includes('metamask')) return 'metamask'
  return 'browser'
}

export async function linkConnectedWallet({ address, provider }) {
  const challenge = await apiPost('/api/wallets/challenge', {
    address,
    chain_id: 5042002,
    provider: providerName(provider),
  })
  const signature = await signSiweMessage(challenge.message)
  return apiPost('/api/wallets/verify', { message: challenge.message, signature })
}

export const listLinkedWallets = () => apiGet('/api/wallets')
// Would linking this address reclaim pre-account (SIWE-era) data for the
// current session's account? Boolean only — counts stay behind signature proof.
export const checkLegacyWallet = (address) => apiGet(`/api/wallets/check?address=${encodeURIComponent(address)}`)
export const makePrimaryWallet = (id) => apiPost(`/api/wallets/${encodeURIComponent(id)}/primary`, {})
export const removeLinkedWallet = (id) => apiDelete(`/api/wallets/${encodeURIComponent(id)}`)
