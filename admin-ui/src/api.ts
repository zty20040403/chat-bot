export type ResourceName =
  | 'overview'
  | 'observability'
  | 'alerts'
  | 'deliveries'
  | 'usage'
  | 'tasks'
  | 'subagents'
  | 'jobs'
  | 'sandboxes'
  | 'stickers'
  | 'media'
  | 'sources'
  | 'databases'
  | 'groups'
  | 'localModel'
  | 'tools'
  | 'traces'
  | 'contextPlans'
  | 'contextDebug'
  | 'audit'
  | 'versions'

export type JsonObject = Record<string, any>

export interface RealtimeEvent {
  type: 'ready' | 'resources.changed'
  sequence: number
  resources: string[]
  timestamp: number
  versions?: Record<string, number>
}

export const RESOURCE_PATHS: Record<ResourceName, string> = {
  overview: '/overview',
  observability: '/observability',
  alerts: '/alerts?days=1&limit=200',
  deliveries: '/deliveries',
  usage: '/usage?days=90',
  tasks: '/tasks',
  subagents: '/subagents',
  jobs: '/jobs',
  sandboxes: '/sandboxes',
  stickers: '/stickers',
  media: '/media',
  sources: '/sources',
  databases: '/databases',
  groups: '/group-models',
  localModel: '/local-model',
  tools: '/tools',
  traces: '/traces',
  contextPlans: '/context-plans',
  contextDebug: '/context-debug',
  audit: '/audit',
  versions: '/resource-versions',
}

export const EVENT_RESOURCES: Record<string, ResourceName[]> = {
  overview: ['overview'],
  observability: ['observability'],
  alerts: ['alerts', 'observability'],
  deliveries: ['deliveries'],
  usage: ['usage'],
  tasks: ['tasks'],
  subagents: ['subagents', 'tasks'],
  jobs: ['jobs'],
  sandboxes: ['sandboxes'],
  stickers: ['stickers'],
  media: ['media'],
  sources: ['sources'],
  databases: ['databases'],
  groups: ['groups'],
  'local-model': ['localModel'],
  tools: ['tools'],
  traces: ['traces'],
  'context-debug': ['contextDebug', 'contextPlans'],
  audit: ['audit'],
}

export class AdminApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : `管理 API 返回 ${status}`)
    this.status = status
    this.detail = detail
  }
}

export class AdminClient {
  constructor(
    private readonly runtime: KennethbotAdminRuntime,
    private readonly token: string,
  ) {}

  async resource(name: ResourceName, signal?: AbortSignal): Promise<JsonObject> {
    return this.request(RESOURCE_PATHS[name], { signal })
  }

  async query(path: string, signal?: AbortSignal): Promise<JsonObject> {
    return this.request(path, { signal })
  }

  async mutate(
    path: string,
    method: 'POST' | 'PUT' | 'DELETE',
    body: unknown,
    expectedVersion: number | undefined,
  ): Promise<JsonObject> {
    const headers = this.headers()
    headers.set('Content-Type', 'application/json')
    headers.set('X-Admin-Actor', 'kennethbot-react-console')
    if (expectedVersion !== undefined) {
      headers.set('If-Match', `"${expectedVersion}"`)
    }
    const response = await fetch(`${this.runtime.apiBase}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    return this.decode(response)
  }

  async events(
    signal: AbortSignal,
    onEvent: (event: RealtimeEvent) => void,
  ): Promise<void> {
    const response = await fetch(`${this.runtime.apiBase}/events`, {
      headers: this.headers(),
      cache: 'no-store',
      signal,
    })
    if (!response.ok || !response.body) {
      await this.decode(response)
      return
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (!signal.aborted) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const data = block
          .split('\n')
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trim())
          .join('')
        if (data) {
          try {
            onEvent(JSON.parse(data) as RealtimeEvent)
          } catch {
            // Ignore one malformed event; the next resource event is independent.
          }
        }
        boundary = buffer.indexOf('\n\n')
      }
    }
  }

  private async request(path: string, init: RequestInit = {}): Promise<JsonObject> {
    const response = await fetch(`${this.runtime.apiBase}${path}`, {
      ...init,
      headers: this.headers(init.headers),
      cache: 'no-store',
    })
    return this.decode(response)
  }

  private headers(existing?: HeadersInit): Headers {
    const headers = new Headers(existing)
    headers.set('Accept', 'application/json')
    if (this.token) headers.set('Authorization', `Bearer ${this.token}`)
    return headers
  }

  private async decode(response: Response): Promise<JsonObject> {
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new AdminApiError(response.status, payload.detail ?? payload)
    }
    return payload as JsonObject
  }
}
