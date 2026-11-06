# Shared measurement only; native caller must validate each real process sample.
function Measure-StableGateway {
  param(
    [Parameter(Mandatory)][scriptblock]$ReadState,
    [ValidateRange(0.001,3600)][double]$DurationSeconds = 90,
    [ValidateRange(0.001,60)][double]$IntervalSeconds = 3
  )
  $first = & $ReadState
  if (-not $first.pid -or -not $first.start_time) { throw 'Missing gateway identity' }
  $clock = [Diagnostics.Stopwatch]::StartNew()
  $rows = [Collections.Generic.List[object]]::new()
  $rows.Add([pscustomobject]@{
    observed_at = [DateTimeOffset]::UtcNow.ToString('o')
    elapsed_seconds = 0.0
    state = $first
  })
  do {
    $remaining = $DurationSeconds - $clock.Elapsed.TotalSeconds
    if ($remaining -gt 0) {
      Start-Sleep -Milliseconds ([int][Math]::Ceiling(1000 * [Math]::Min($IntervalSeconds, $remaining)))
    }
    $state = & $ReadState
    if ($state.pid -ne $first.pid -or $state.start_time -ne $first.start_time) {
      throw 'Gateway identity drift in observation window'
    }
    $rows.Add([pscustomobject]@{
      observed_at = [DateTimeOffset]::UtcNow.ToString('o')
      elapsed_seconds = $clock.Elapsed.TotalSeconds
      state = $state
    })
  } while ($rows[$rows.Count - 1].elapsed_seconds -lt $DurationSeconds)
  $clock.Stop()
  return $rows.ToArray()
}
