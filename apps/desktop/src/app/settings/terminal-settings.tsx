import { Terminal } from '@/lib/icons'

import { SectionHeading, SettingsContent } from './primitives'
import { TerminalBackendPanel } from './terminal-backend-panel'

/** Advanced → Terminal: where Clover runs commands. Same panel as Capabilities. */
export function TerminalSettings() {
  return (
    <SettingsContent>
      <SectionHeading icon={Terminal} title="Terminal" />
      <p className="-mt-1 mb-5 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
        Where Clover runs commands on your behalf. Most people never need to change this.
      </p>
      <TerminalBackendPanel />
    </SettingsContent>
  )
}
