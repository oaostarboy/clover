import { useStore } from '@nanostores/react'

import { Switch } from '@/components/ui/switch'
import { Starmap } from '@/lib/icons'
import { $extraHud, $extraPet, $extraStarmap, $extraWindowControls } from '@/store/first-run'

import { ListRow, SectionHeading, SettingsContent } from './primitives'

/** Extras: the fun and power-user surfaces. Off for new installs, kept as-is for existing ones. */
export function ExtrasSettings() {
  const pet = useStore($extraPet)
  const starmap = useStore($extraStarmap)
  const hud = useStore($extraHud)
  const controls = useStore($extraWindowControls)

  return (
    <SettingsContent>
      <SectionHeading icon={Starmap} title="Extras" />
      <p className="-mt-1 mb-3 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
        Fun and power features. Turn on what you like. Nothing here is needed to chat.
      </p>
      <div className="divide-y divide-(--ui-stroke-tertiary)">
        <ListRow
          action={<Switch aria-label="Pet" checked={pet} onCheckedChange={v => $extraPet.set(v)} />}
          description="A little companion that floats in the corner and reacts while Clover works."
          title="Pet"
        />
        <ListRow
          action={<Switch aria-label="Star map" checked={starmap} onCheckedChange={v => $extraStarmap.set(v)} />}
          description="A picture of what Clover has learned about you and your work."
          title="Star map"
        />
        <ListRow
          action={<Switch aria-label="Chat bar and game overlay" checked={hud} onCheckedChange={v => $extraHud.set(v)} />}
          description="A small chat bar you can keep on top of other apps, even games."
          title="Chat bar and game overlay"
        />
        <ListRow
          action={
            <Switch aria-label="Window buttons" checked={controls} onCheckedChange={v => $extraWindowControls.set(v)} />
          }
          description="Extra buttons at the top: swap sides, layout editor, sound feedback."
          title="Extra window buttons"
        />
      </div>
    </SettingsContent>
  )
}
