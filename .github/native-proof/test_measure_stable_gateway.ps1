$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/measure-stable-gateway.ps1"
$state = [pscustomobject]@{pid=42;start_time=100;code_sha='fixture'}
$rows = @(Measure-StableGateway -ReadState { $state } -DurationSeconds 0.06 -IntervalSeconds 0.01)
if ($rows.Count -lt 2) { throw 'Missing boundary samples' }
if ($rows[-1].elapsed_seconds -lt 0.06) { throw 'Observation span too short' }
if ($rows[0].elapsed_seconds -ne 0) { throw 'Missing initial boundary' }
foreach ($row in $rows) {
  if (-not $row.observed_at -or $row.state.pid -ne 42) { throw 'Incomplete sample' }
}
$script:reads = 0
$refused = $false
try {
  Measure-StableGateway -ReadState {
    $script:reads++
    [pscustomobject]@{pid=$(if ($script:reads -eq 1) {42} else {43});start_time=100}
  } -DurationSeconds 0.06 -IntervalSeconds 0.01 | Out-Null
} catch { $refused = $_.Exception.Message -match 'identity drift' }
if (-not $refused) { throw 'Accepted changed gateway identity' }
$refused = $false
try { Measure-StableGateway -ReadState { throw 'sampler unreadable' } -DurationSeconds 0.06 | Out-Null }
catch { $refused = $_.Exception.Message -match 'sampler unreadable' }
if (-not $refused) { throw 'Swallowed sample failure' }
'PASS: measured final boundary, stable identity, identity drift refusal, sample error refusal'
