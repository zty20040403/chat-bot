/// <reference types="vite/client" />

interface KennethbotAdminRuntime {
  prefix: string
  apiBase: string
  version: string
  requiresToken: boolean
}

interface Window {
  __KENNETHBOT_ADMIN__: KennethbotAdminRuntime
}
