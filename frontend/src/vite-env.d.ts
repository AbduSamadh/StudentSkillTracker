/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

import '@tanstack/react-query'

declare module '@tanstack/react-query' {
  interface Register {
    queryMeta: { persist?: boolean }
  }
}
