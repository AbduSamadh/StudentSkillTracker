import './lib/i18n'
import './styles.css'

import { createAsyncStoragePersister } from '@tanstack/query-async-storage-persister'
import { QueryClient } from '@tanstack/react-query'
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client'
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { registerSW } from 'virtual:pwa-register'

import App from './App'
import { ApiError } from './lib/api'
import { AuthProvider } from './lib/auth'
import { idbStorage } from './lib/offline/db'
import { startBackgroundSync } from './lib/offline/queue'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Keep cached data for a day so capture screens open at a venue with no signal.
      gcTime: 24 * 60 * 60 * 1000,
      retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
      refetchOnWindowFocus: false,
      networkMode: 'offlineFirst',
    },
  },
})

const persister = createAsyncStoragePersister({ storage: idbStorage, key: 'stem-query-cache' })

registerSW({ immediate: true })
startBackgroundSync()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{
        persister,
        maxAge: 24 * 60 * 60 * 1000,
        // Only what capture screens need is stored on the device.
        dehydrateOptions: { shouldDehydrateQuery: (q) => q.state.status === 'success' && q.meta?.persist === true },
      }}
    >
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </PersistQueryClientProvider>
  </React.StrictMode>,
)
