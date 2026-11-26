import { Smartphone } from '@/lib/icons'

import { PhonePairingSection } from './phone-pairing-section'
import { SectionHeading, SettingsContent } from './primitives'

/** Phone: one page, the existing pairing flow (QR, devices, access). */
export function PhoneSettings() {
  return (
    <SettingsContent>
      <SectionHeading icon={Smartphone} title="Phone & devices" />
      <p className="-mt-1 mb-5 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
        Chat with Clover from your iPhone. Scan the code once and you are set.
      </p>
      <PhonePairingSection compact />
    </SettingsContent>
  )
}
