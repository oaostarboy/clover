import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { getCloverConfigDefaults, getCloverConfigRecord, saveCloverConfig } from '@/clover'
import { KbdCombo } from '@/components/ui/kbd'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import {
  Archive,
  Bell,
  Clock,
  Download,
  Globe,
  Info,
  Keyboard,
  KeyRound,
  Package,
  RefreshCw,
  Search,
  Settings2,
  Smartphone,
  Starmap,
  Terminal,
  Upload,
  Wrench,
  Zap
} from '@/lib/icons'
import { isEditableTarget } from '@/lib/keybinds/combo'
import { typeToFocusChar } from '@/lib/keybinds/composer-focus-keys'
import { $commandPaletteOpen, openCommandPalettePage } from '@/store/command-palette'
import { confirm } from '@/store/confirm'
import { bindingsFor } from '@/store/keybinds'
import { notifyError } from '@/store/notifications'

import { useRouteEnumParam } from '../hooks/use-route-enum-param'
import { OverlayIconButton } from '../overlays/overlay-chrome'
import { OverlayMain, OverlayNav, type OverlayNavGroup, OverlaySplitLayout } from '../overlays/overlay-split-layout'
import { OverlayView } from '../overlays/overlay-view'
import { CRON_ROUTE, SKILLS_ROUTE } from '../routes'

import { AboutSettings } from './about-settings'
import { AppearanceSettings } from './appearance-settings'
import { ConfigSettings } from './config-settings'
import { SECTIONS } from './constants'
import { ExtrasSettings } from './extras-settings'
import { GatewaySettings } from './gateway-settings'
import { KeybindSettings } from './keybind-settings'
import { KEYS_VIEWS, KeysSettings, type KeysView } from './keys-settings'
import { NotificationsSettings } from './notifications-settings'
import { PhoneSettings } from './phone-settings'
import { PluginsSettings } from './plugins-settings'
import { PROVIDER_VIEWS, ProvidersSettings, type ProviderView } from './providers-settings'
import { SessionsSettings } from './sessions-settings'
import { TerminalSettings } from './terminal-settings'
import type { SettingsPageProps, SettingsView as SettingsViewId } from './types'

const SETTINGS_VIEWS: readonly SettingsViewId[] = [
  ...SECTIONS.map(s => `config:${s.id}` as SettingsViewId),
  'phone',
  'extras',
  'terminal',
  'providers',
  'gateway',
  // Legacy alias: the Connections page merged into Gateways. Kept in the enum
  // so saved `?tab=connections` deep links still resolve (redirected below).
  'connections',
  'keybinds',
  'keys',
  'notifications',
  'plugins',
  'sessions',
  'about'
]

export function SettingsView({ onClose, onConfigSaved, onMainModelChanged }: SettingsPageProps) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const { hash, pathname, search } = useLocation()

  // MCP moved out of Settings into Capabilities (/skills?tab=mcp). Keep old
  // `/settings?tab=mcp` deep links working — `useRouteEnumParam` would silently
  // coerce the unknown tab to the default view otherwise. Preserve `server=` so
  // an old bookmark still lands on (and highlights) the selected server.
  useEffect(() => {
    const params = new URLSearchParams(search)

    if (params.get('tab') === 'mcp') {
      const server = params.get('server')
      const suffix = server ? `&server=${encodeURIComponent(server)}` : ''
      navigate(`${SKILLS_ROUTE}?tab=mcp${suffix}`, { replace: true })
    }
  }, [navigate, search])

  const [activeView, setActiveView] = useRouteEnumParam('tab', SETTINGS_VIEWS, 'config:model' as SettingsViewId)

  // Connections merged into the unified Gateways page: land old
  // `?tab=connections` routes/bookmarks there instead of a dead entry.
  useEffect(() => {
    if (activeView === 'connections') {
      setActiveView('gateway')
    }
  }, [activeView, setActiveView])
  // Providers subnav (Accounts vs API keys) lives in its own param so each
  // sub-view is deep-linkable and survives a refresh.
  const [providerView, setProviderView] = useRouteEnumParam<ProviderView>('pview', PROVIDER_VIEWS, 'accounts')
  const [keysView] = useRouteEnumParam<KeysView>('kview', KEYS_VIEWS, 'tools')

  // Jump to a section + its sub-view in one navigate. Two sequential setters
  // would each read the same stale `search` and the second would clobber the
  // first's `tab` — so the sub-view never opened on narrow screens.
  const openSubView = useCallback(
    (tab: SettingsViewId, param: string, value: string, fallback: string) => {
      const params = new URLSearchParams(search)
      params.set('tab', tab)

      if (value === fallback) {
        params.delete(param)
      } else {
        params.set(param, value)
      }

      const qs = params.toString()
      navigate({ hash, pathname, search: qs ? `?${qs}` : '' }, { replace: true })
    },
    [hash, navigate, pathname, search]
  )

  const openProviderView = useCallback(
    (view: ProviderView) => openSubView('providers', 'pview', view, 'accounts'),
    [openSubView]
  )

  const openKeysView = useCallback((view: KeysView) => openSubView('keys', 'kview', view, 'tools'), [openSubView])

  const importInputRef = useRef<HTMLInputElement | null>(null)

  const exportConfig = async () => {
    try {
      const cfg = await getCloverConfigRecord()
      const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'clover-config.json'
      a.click()
      URL.revokeObjectURL(url)
      triggerHaptic('success')
    } catch (err) {
      notifyError(err, t.settings.exportFailed)
    }
  }

  const resetConfig = async () => {
    const ok = await confirm({
      confirmLabel: t.settings.resetToDefaults,
      destructive: true,
      title: t.settings.resetConfirm
    })

    if (!ok) {
      return
    }

    try {
      await saveCloverConfig(await getCloverConfigDefaults())
      triggerHaptic('success')
      onConfigSaved?.()
    } catch (err) {
      notifyError(err, t.settings.resetFailed)
    }
  }

  const navGroups: OverlayNavGroup[] = useMemo(() => {
    const sectionLink = (id: string) => {
      const sec = SECTIONS.find(x => x.id === id)!
      const view = `config:${id}` as SettingsViewId

      return {
        active: activeView === view,
        icon: sec.icon,
        id: view,
        label: t.settings.sections[sec.id] ?? sec.label,
        onSelect: () => setActiveView(view)
      }
    }

    const link = (view: SettingsViewId, icon: OverlayNavGroup['icon'], label: string) => ({
      active: activeView === view,
      icon,
      id: view,
      label,
      onSelect: () => setActiveView(view)
    })

    // T3's shape, Clover's words: small group captions over a wide rail.
    // Connections first (phone, notifications), then the AI itself, then the
    // everyday Clover pieces, and everything technical last under Advanced.
    // Nothing was removed, only grouped. (Check-ins have no desktop page yet,
    // so they have no slot here.)
    const everydayClover = ['chat', 'appearance']
    const advancedIds = SECTIONS.filter(x => !['model', ...everydayClover].includes(x.id)).map(x => x.id)

    const routeLink = (id: string, icon: OverlayNavGroup['icon'], label: string, route: string) => ({
      active: false,
      icon,
      id,
      label,
      onSelect: () => navigate(route)
    })

    const advanced: OverlayNavGroup[] = [
      ...advancedIds.map(sectionLink),
      link('terminal', Terminal, 'Terminal'),
      link('providers', Zap, t.settings.nav.providers),
      {
        active: activeView === 'keys' && keysView === 'tools',
        icon: Wrench,
        id: 'kview:tools',
        label: t.settings.nav.keysTools,
        onSelect: () => openKeysView('tools')
      },
      {
        active: activeView === 'keys' && keysView === 'settings',
        icon: KeyRound,
        id: 'kview:settings',
        label: t.settings.nav.apiKeys,
        onSelect: () => openKeysView('settings')
      },
      {
        active: activeView === 'providers' && providerView === 'custom-endpoints',
        icon: Globe,
        id: 'pview:custom-endpoints',
        label: t.settings.nav.providerCustomEndpoints,
        onSelect: () => openProviderView('custom-endpoints')
      },
      link('gateway', Globe, t.settings.nav.gateway),
      link('plugins', Package, t.settings.nav.plugins),
      link('keybinds', Keyboard, t.settings.nav.keybinds)
    ]

    return [
      { ...link('phone', Smartphone, 'Phone & devices'), groupLabel: 'Connections' },
      link('notifications', Bell, t.settings.nav.notifications),
      { ...sectionLink('model'), groupLabel: 'Your AI' },
      { ...sectionLink('chat'), groupLabel: 'Clover' },
      sectionLink('appearance'),
      routeLink('cron', Clock, 'Scheduled tasks', CRON_ROUTE),
      routeLink('skills', Package, 'Skills', SKILLS_ROUTE),
      link('extras', Starmap, 'Extras'),
      link('sessions', Archive, t.settings.nav.archivedChats),
      // One quiet entry: its pages open underneath once you are inside it.
      {
        active: advanced.some(item => item.active),
        children: advanced,
        groupLabel: 'Advanced',
        icon: Settings2,
        id: 'advanced',
        label: 'More settings',
        onSelect: () => advanced[0].onSelect()
      },
      { ...link('about', Info, 'About & help'), groupLabel: 'Help' }
    ]
  }, [activeView, keysView, providerView, t, setActiveView, openProviderView, openKeysView, navigate])

  // Type-to-search: printable keystrokes on the Settings surface (outside any
  // field) open the settings-scoped palette, seeded with the character — same
  // reflex as the chat surface's type-to-focus, pointed at search instead.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ($commandPaletteOpen.get() || isEditableTarget(event.target)) {
        return
      }

      const char = typeToFocusChar(event)

      if (char === null || char === ' ') {
        return
      }

      event.preventDefault()
      openCommandPalettePage('settings', char)
    }

    window.addEventListener('keydown', onKeyDown)

    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  // T3's search row heads the rail. It is chrome, not an input: clicking (or
  // just typing) opens the Ctrl+K palette scoped to settings.
  const searchCombo = bindingsFor('nav.commandPalette')[0]

  const searchRow = (
    <button
      className="mb-1 flex h-8 w-full items-center gap-2 rounded-md border border-(--ui-stroke-tertiary) px-2 text-left text-(--ui-text-tertiary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground"
      data-slot="settings-search"
      onClick={() => {
        triggerHaptic('open')
        openCommandPalettePage('settings')
      }}
      type="button"
    >
      <Search className="size-4 shrink-0" />
      <span className="flex-1 text-[length:var(--conversation-text-font-size)]">{t.settings.search.pill}</span>
      {searchCombo && <KbdCombo combo={searchCombo} size="sm" variant="ghost" />}
    </button>
  )

  const activeLabel = navGroups.flatMap(group => [group, ...(group.children ?? [])]).find(item => item.active)?.label ?? ''

  const navFooter = (
    <>
      <Tip label={t.settings.exportConfig}>
        <OverlayIconButton onClick={() => void exportConfig()}>
          <Download />
        </OverlayIconButton>
      </Tip>
      <Tip label={t.settings.importConfig}>
        <OverlayIconButton
          onClick={() => {
            triggerHaptic('open')
            importInputRef.current?.click()
          }}
        >
          <Upload />
        </OverlayIconButton>
      </Tip>
      <Tip label={t.settings.resetToDefaults}>
        <OverlayIconButton
          className="hover:text-destructive"
          onClick={() => {
            triggerHaptic('warning')
            void resetConfig()
          }}
        >
          <RefreshCw />
        </OverlayIconButton>
      </Tip>
    </>
  )

  const activeSettingsContent =
    activeView === 'config:appearance' ? (
      <AppearanceSettings />
    ) : activeView === 'about' ? (
      <AboutSettings />
    ) : activeView === 'phone' ? (
      <PhoneSettings />
    ) : activeView === 'extras' ? (
      <ExtrasSettings />
    ) : activeView === 'terminal' ? (
      <TerminalSettings />
    ) : activeView === 'gateway' || activeView === 'connections' ? (
      // 'connections' renders the unified page too so the frame before
      // the alias redirect lands doesn't flash the fallback view.
      <GatewaySettings />
    ) : activeView === 'keybinds' ? (
      <KeybindSettings />
    ) : activeView.startsWith('config:') ? (
      <ConfigSettings
        activeSectionId={activeView.slice('config:'.length)}
        importInputRef={importInputRef}
        onConfigSaved={onConfigSaved}
        onMainModelChanged={onMainModelChanged}
      />
    ) : activeView === 'providers' ? (
      <ProvidersSettings
        onClose={onClose}
        onConfigSaved={onConfigSaved}
        onMainModelChanged={onMainModelChanged}
        onViewChange={setProviderView}
        view={providerView}
      />
    ) : activeView === 'keys' ? (
      <KeysSettings view={keysView} />
    ) : activeView === 'notifications' ? (
      <NotificationsSettings />
    ) : activeView === 'plugins' ? (
      <PluginsSettings />
    ) : (
      <SessionsSettings />
    )

  return (
    <OverlayView closeLabel={t.settings.closeSettings} onClose={onClose} page>
      <OverlaySplitLayout className="grid-cols-[16rem_minmax(0,1fr)]">
        <OverlayNav footer={navFooter} groups={navGroups} top={searchRow} />

        <OverlayMain className="px-0 pb-0">
          <nav
            aria-label="Breadcrumb"
            className="flex shrink-0 items-center gap-2 px-[clamp(0.8333rem,2.6667vw,2.6667rem)] pb-3 text-[length:var(--conversation-text-font-size)]"
            data-slot="settings-breadcrumb"
          >
            <span className="text-(--ui-text-tertiary)">Settings</span>
            <span aria-hidden className="text-(--ui-text-quaternary)">
              /
            </span>
            <span className="text-foreground">{activeLabel}</span>
          </nav>
          {activeSettingsContent}
        </OverlayMain>
      </OverlaySplitLayout>
    </OverlayView>
  )
}

export { SettingsView as SettingsPage }
