# Test-fixture telemetry only. Never changes update acceptance predicates.
function Initialize-NativePhaseJournal([string]$Directory, [string]$SourceSha) {
  if ($SourceSha -notmatch '^[a-f0-9]{40}$') { throw 'Invalid phase source identity' }
  $script:NativePhaseDirectory = $Directory
  $script:NativePhaseSource = $SourceSha
  $script:NativePhaseSequence = 0
  $script:NativePhaseCurrent = $null
  $script:NativePhaseClock = [System.Diagnostics.Stopwatch]::StartNew()
  [System.IO.Directory]::CreateDirectory($Directory) | Out-Null
}
function Write-NativePhase([string]$Name, [ValidateSet('started','completed','failed')][string]$State) {
  if ($Name -notmatch '^[a-z0-9-]{1,64}$') { throw 'Invalid phase name' }
  $script:NativePhaseSequence++
  $record = [ordered]@{
    sequence = $script:NativePhaseSequence
    phase = $Name
    state = $State
    utc = [DateTimeOffset]::UtcNow.ToString('o')
    elapsed_ms = $script:NativePhaseClock.ElapsedMilliseconds
    source_sha = $script:NativePhaseSource
  }
  $json = $record | ConvertTo-Json -Compress
  $path = Join-Path $script:NativePhaseDirectory ('phase-{0:D6}.json' -f $script:NativePhaseSequence)
  $temp = $path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
  try {
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($json)
    $stream = [System.IO.FileStream]::new($temp, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    [System.IO.File]::Move($temp, $path)
  } finally {
    if ([System.IO.File]::Exists($temp)) { [System.IO.File]::Delete($temp) }
  }
  $script:NativePhaseCurrent = if ($State -eq 'started') { $Name } else { $null }
  # A second, remote log channel; not a promise of retention after runner loss.
  Write-Host ('NATIVE_PHASE ' + $json)
}
function Write-NativePhaseFailure {
  if ($script:NativePhaseCurrent) { Write-NativePhase $script:NativePhaseCurrent 'failed' }
}
