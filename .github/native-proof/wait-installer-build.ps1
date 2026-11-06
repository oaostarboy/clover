# Disposable hosted-runner readiness proof. Never kill or bypass holders.
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') { throw 'Hosted runner only' }
$history = @(); $deadline = (Get-Date).AddMinutes(12); $quiet = 0
while ((Get-Date) -lt $deadline) {
  $all = @(Get-CimInstance Win32_Process)
  $roots = @($all | Where-Object {
    $_.CommandLine -match 'clover_cli\.main\s+desktop\s+--build-only' -and
    $_.ExecutablePath -like "$env:INSTALL_DIR\venv\*"
  })
  $ids = @($roots | ForEach-Object { $_.ProcessId })
  $owned = @($all | Where-Object { $_.ProcessId -in $ids -or $_.ParentProcessId -in $ids })
  $history += @{ at=(Get-Date).ToUniversalTime().ToString('o'); processes=@($owned | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine,CreationDate) }
  $history | ConvertTo-Json -Depth 8 | Set-Content "$evidence\installer-build-readiness.json" -Encoding utf8
  if ($owned.Count -eq 0) { $quiet++; if ($quiet -ge 3) { break } } else { $quiet=0 }
  Start-Sleep -Seconds 2
}
if ($quiet -lt 3) { throw 'Owned installer build did not become quiescent; no updater started' }
# Execute real build-only command synchronously to prove successful readiness;
# earlier vanished process ancestry alone is NOT a successful build receipt.
& "$env:INSTALL_DIR\venv\Scripts\python.exe" -m clover_cli.main desktop --build-only 2>&1 |
  Tee-Object "$evidence\synchronous-desktop-build.log" | Out-Host
$buildExit = $LASTEXITCODE
@{ exit=$buildExit; at=(Get-Date).ToUniversalTime().ToString('o'); command='installed python -m clover_cli.main desktop --build-only' } |
  ConvertTo-Json | Set-Content "$evidence\synchronous-desktop-build.json" -Encoding utf8
if ($buildExit -ne 0) { throw 'Synchronous desktop build failed; not upgrade-ready' }
