import { useStore } from '@nanostores/react'
import type * as React from 'react'

import { cn } from '@/lib/utils'
import { $calmDesktop } from '@/store/first-run'

interface SidebarPanelLabelProps extends React.ComponentProps<'span'> {
  dotClassName?: string
}

export function SidebarPanelLabel({ children, className, dotClassName, ...props }: SidebarPanelLabelProps) {
  const calm = useStore($calmDesktop)

  // New installs get a quiet section label: sentence case, muted, no tracked
  // caps and no dither square. Existing users keep the original look.
  if (calm) {
    return (
      <span
        className={cn('flex min-w-0 items-center gap-2 pl-2 text-[0.75rem] font-medium text-(--ui-text-tertiary)', className)}
        {...props}
      >
        <span className="min-w-0 truncate leading-none">{children}</span>
      </span>
    )
  }

  return (
    <span
      className={cn(
        'flex min-w-0 items-center gap-2 pl-2 text-[0.64rem] font-semibold uppercase tracking-[0.16em] text-(--theme-primary)',
        className
      )}
      {...props}
    >
      <span aria-hidden="true" className={cn('dither inline-block size-2 shrink-0 rounded-[1px]', dotClassName)} />
      <span className="min-w-0 truncate leading-none">{children}</span>
    </span>
  )
}
