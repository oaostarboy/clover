import { cn } from '@/lib/utils'

// The minimal flat four-point Clover star (brand: clover-minimal.svg). Drawn
// with currentColor so it takes the accent colour of wherever it sits.
// Never use the heart mark here.
const STAR_PATH =
  'M4215 8309 c-483 -39 -920 -439 -1189 -1091 -55 -132 -98 -259 -181 -528 -207 -674 -261 -728 -935 -935 -503 -155 -728 -252 -983 -422 -852 -570 -852 -1496 0 -2066 255 -170 480 -267 983 -422 674 -207 728 -261 935 -935 155 -503 252 -728 422 -983 570 -852 1496 -852 2066 0 170 255 267 480 422 983 207 674 261 728 935 935 503 155 728 252 983 422 852 570 852 1496 0 2066 -255 170 -480 267 -983 422 -674 207 -728 261 -935 935 -42 135 -91 289 -110 343 -305 859 -825 1323 -1430 1276z'

export function CloverStar({ className }: { className?: string }) {
  return (
    <svg aria-hidden className={cn('size-12 text-primary', className)} fill="currentColor" viewBox="0 0 860 860">
      <path d={STAR_PATH} transform="translate(0 860) scale(.1 -.1)" />
    </svg>
  )
}
