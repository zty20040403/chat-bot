/// <reference types="vite/client" />

interface GaojiAdminRuntime {
  prefix: string
  apiBase: string
  version: string
  requiresLogin: boolean
}

interface Window {
  __GAOJI_ADMIN__: GaojiAdminRuntime
}
