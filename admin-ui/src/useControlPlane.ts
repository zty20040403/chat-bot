import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AdminApiError,
  AdminClient,
  EVENT_RESOURCES,
  type JsonObject,
  type ResourceName,
  type RealtimeEvent,
} from './api'

const INITIAL_RESOURCES: ResourceName[] = [
  'overview',
  'observability',
  'deliveries',
  'usage',
  'tasks',
  'jobs',
  'sandboxes',
  'stickers',
  'media',
  'sources',
  'databases',
  'groups',
  'tools',
  'traces',
  'contextPlans',
  'audit',
  'versions',
]

export function useControlPlane(runtime: KennethbotAdminRuntime) {
  const [token, setTokenState] = useState(() => localStorage.getItem('kennethbot.admin.token') ?? '')
  const [data, setData] = useState<Partial<Record<ResourceName, JsonObject>>>({})
  const [versions, setVersions] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState<Set<ResourceName>>(new Set())
  const [online, setOnline] = useState(false)
  const [error, setError] = useState('')
  const eventSequence = useRef(0)
  const client = useMemo(() => new AdminClient(runtime, token), [runtime, token])
  const authenticated = !runtime.requiresToken || Boolean(token)

  const setToken = useCallback((value: string) => {
    const normalized = value.trim()
    if (normalized) localStorage.setItem('kennethbot.admin.token', normalized)
    else localStorage.removeItem('kennethbot.admin.token')
    setTokenState(normalized)
    setData({})
    setVersions({})
    setError('')
  }, [])

  const refresh = useCallback(
    async (resource: ResourceName, signal?: AbortSignal) => {
      setLoading((current) => new Set(current).add(resource))
      try {
        const payload = await client.resource(resource, signal)
        setData((current) => ({ ...current, [resource]: payload }))
        if (resource === 'versions' && payload.versions) {
          setVersions(payload.versions as Record<string, number>)
        } else if (typeof payload.resource === 'string' && typeof payload.resource_version === 'number') {
          setVersions((current) => ({
            ...current,
            [payload.resource]: payload.resource_version,
          }))
        }
        setOnline(true)
        setError('')
      } catch (reason) {
        if (signal?.aborted) return
        setOnline(false)
        if (reason instanceof AdminApiError && reason.status === 401) {
          setError('管理 Token 无效或已过期')
        } else {
          setError(reason instanceof Error ? reason.message : '管理 API 暂时不可用')
        }
      } finally {
        setLoading((current) => {
          const next = new Set(current)
          next.delete(resource)
          return next
        })
      }
    },
    [client],
  )

  const refreshMany = useCallback(
    async (resources: ResourceName[], signal?: AbortSignal) => {
      await Promise.allSettled([...new Set(resources)].map((resource) => refresh(resource, signal)))
    },
    [refresh],
  )

  useEffect(() => {
    if (!authenticated) return
    const controller = new AbortController()
    void refreshMany(INITIAL_RESOURCES, controller.signal)
    return () => controller.abort()
  }, [authenticated, refreshMany])

  useEffect(() => {
    if (!authenticated) return
    const controller = new AbortController()
    let retry = 1000
    const run = async () => {
      while (!controller.signal.aborted) {
        try {
          await client.events(controller.signal, (event: RealtimeEvent) => {
            if (event.sequence && event.sequence <= eventSequence.current) return
            eventSequence.current = Math.max(eventSequence.current, event.sequence || 0)
            if (event.versions) setVersions((current) => ({ ...current, ...event.versions }))
            if (event.type === 'resources.changed') {
              const resources = event.resources.flatMap((resource) => EVENT_RESOURCES[resource] ?? [])
              resources.push('audit', 'versions')
              void refreshMany(resources, controller.signal)
            }
          })
          retry = 1000
        } catch (reason) {
          if (controller.signal.aborted) break
          setOnline(false)
          setError(reason instanceof Error ? reason.message : '实时连接已断开')
        }
        await new Promise((resolve) => window.setTimeout(resolve, retry))
        retry = Math.min(retry * 2, 15000)
      }
    }
    void run()
    return () => controller.abort()
  }, [authenticated, client, refreshMany])

  useEffect(() => {
    if (!authenticated) return
    const timer = window.setInterval(() => {
      void refreshMany(['overview', 'observability', 'databases'])
    }, 60000)
    return () => window.clearInterval(timer)
  }, [authenticated, refreshMany])

  const mutate = useCallback(
    async (
      resource: string,
      path: string,
      method: 'POST' | 'PUT' | 'DELETE',
      body: unknown,
      refreshResources: ResourceName[],
    ) => {
      try {
        const payload = await client.mutate(path, method, body, versions[resource])
        if (typeof payload.resource_version === 'number') {
          setVersions((current) => ({ ...current, [resource]: payload.resource_version }))
        }
        await refreshMany([...refreshResources, 'audit', 'versions'])
        setError('')
        return payload
      } catch (reason) {
        if (reason instanceof AdminApiError && reason.status === 409) {
          await refreshMany([...refreshResources, 'versions'])
          setError('数据已被其他管理员修改，已加载最新版本，请重试')
        } else {
          setError(reason instanceof Error ? reason.message : '修改失败')
        }
        throw reason
      }
    },
    [client, refreshMany, versions],
  )

  return {
    token,
    setToken,
    authenticated,
    data,
    versions,
    loading,
    online,
    error,
    clearError: () => setError(''),
    refresh,
    refreshMany,
    mutate,
  }
}
