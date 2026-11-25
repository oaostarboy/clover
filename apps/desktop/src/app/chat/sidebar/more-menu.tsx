import { useStore } from '@nanostores/react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu'
import { $extraStarmap } from '@/store/first-run'

import {
  AGENTS_ROUTE,
  ARTIFACTS_ROUTE,
  COMMAND_CENTER_ROUTE,
  CRON_ROUTE,
  MESSAGING_ROUTE,
  PROFILES_ROUTE,
  SETTINGS_ROUTE,
  SKILLS_ROUTE,
  STARMAP_ROUTE,
  WEBHOOKS_ROUTE
} from '../../routes'

export interface MoreEntry {
  detail: string
  icon: string
  id: string
  label: string
  route: string
}

export interface MoreGroup {
  entries: MoreEntry[]
  label: string
}

/**
 * Everything that is not "chat" lives behind one quiet door, grouped, each with
 * one plain line saying what it is. Nothing was removed: every page that used
 * to sit in the sidebar is one click away here.
 */
export function moreGroups(starmap: boolean): MoreGroup[] {
  const groups: MoreGroup[] = [
    {
      label: 'Do more',
      entries: [
        { id: 'skills', label: 'Skills', detail: 'Things Clover knows how to do.', icon: 'symbol-misc', route: SKILLS_ROUTE },
        { id: 'cron', label: 'Scheduled jobs', detail: 'Tasks that run on their own, on a timer.', icon: 'watch', route: CRON_ROUTE },
        { id: 'artifacts', label: 'Files Clover made', detail: 'Documents, images and code from your chats.', icon: 'files', route: ARTIFACTS_ROUTE }
      ]
    },
    {
      label: 'Connect',
      entries: [
        { id: 'messaging', label: 'Messaging', detail: 'Talk to Clover from Telegram, Slack and more.', icon: 'comment', route: MESSAGING_ROUTE },
        { id: 'webhooks', label: 'Webhooks', detail: 'Let other apps start a chat.', icon: 'plug', route: WEBHOOKS_ROUTE }
      ]
    },
    {
      label: 'Manage',
      entries: [
        { id: 'agents', label: 'Helpers', detail: 'See who is working for you right now.', icon: 'organization', route: AGENTS_ROUTE },
        { id: 'profiles', label: 'Profiles', detail: 'Separate Clovers for work, home or anything else.', icon: 'account', route: PROFILES_ROUTE },
        { id: 'command-center', label: 'Command center', detail: 'One page with the whole picture.', icon: 'dashboard', route: COMMAND_CENTER_ROUTE },
        ...(starmap
          ? [{ id: 'starmap', label: 'Star map', detail: 'How your memory and skills connect.', icon: 'star-empty', route: STARMAP_ROUTE }]
          : [])
      ]
    }
  ]

  return groups
}

interface Props {
  compact: boolean
  onOpen: (route: string) => void
  settingsActive: boolean
}

/** Bottom corner of the sidebar: Settings, and a More menu. */
export function SidebarCorner({ compact, onOpen, settingsActive }: Props) {
  const starmap = useStore($extraStarmap)

  return (
    <div className="flex shrink-0 items-center gap-1 px-1 pb-2 pt-1" data-testid="sidebar-corner" data-tour="sidebar-more">
      <Button
        aria-label="Settings"
        className={settingsActive ? 'bg-(--ui-control-active-background) text-foreground' : 'text-(--ui-text-secondary)'}
        onClick={() => onOpen(SETTINGS_ROUTE)}
        size={compact ? 'icon' : 'sm'}
        variant="ghost"
      >
        <Codicon name="settings-gear" />
        {!compact && <span className="text-[0.8125rem]">Settings</span>}
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button aria-label="More" className="ml-auto text-(--ui-text-secondary)" size="icon" variant="ghost">
            <Codicon name="ellipsis" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-72" side="top">
          {moreGroups(starmap).map((group, i) => (
            <div key={group.label}>
              {i > 0 && <DropdownMenuSeparator />}
              <DropdownMenuLabel className="px-2.5 pt-1.5 pb-0.5 text-xs font-medium text-(--ui-text-tertiary)">{group.label}</DropdownMenuLabel>
              {group.entries.map(entry => (
                <DropdownMenuItem
                  className="flex-col items-start gap-0 rounded-md px-2.5 py-1.5"
                  key={entry.id}
                  onSelect={() => onOpen(entry.route)}
                >
                  <span className="text-[0.8125rem] text-foreground">{entry.label}</span>
                  <span className="text-xs text-(--ui-text-secondary)">{entry.detail}</span>
                </DropdownMenuItem>
              ))}
            </div>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
