<#
run_all.ps1 -- run the full synthesis pipeline (fine -> macro -> overall) for a
track by calling the EXISTING CLIs in dependency order. Stops at the first failure
(each step needs the previous step's manifest). Re-run without -Fresh to resume
(units with a final.pdf are skipped).

Usage (from anywhere; it cd's to its own dir so config paths resolve):
  .\run_all.ps1                                  # frontier, default models
  .\run_all.ps1 -Track knowledge
  .\run_all.ps1 -Track both                       # frontier then knowledge
  .\run_all.ps1 -Track frontier --fresh --review-model opus   # extra flags -> run_* steps
  $env:TITLE="My Title"; .\run_all.ps1
  .\run_all.ps1 -Py "conda run -n litkg python"   # if python isn't already the litkg one
#>
param(
  [string]$Track = "frontier",                    # frontier | knowledge | both
  [string]$Title = "",
  [string]$Py = "python",
  [Parameter(ValueFromRemainingArguments = $true)] [string[]] $Extra
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not $Extra) { $Extra = @() }

# split $Py so overrides like "conda run -n litkg python" work
$pyTokens = $Py -split '\s+'
$exe = $pyTokens[0]
$pyPrefix = if ($pyTokens.Count -gt 1) { $pyTokens[1..($pyTokens.Count - 1)] } else { @() }

function Run([string[]] $cmdArgs) {
  $all = @($pyPrefix + $cmdArgs)
  Write-Host ">>> $exe $($all -join ' ')" -ForegroundColor Cyan
  & $exe @all
  if ($LASTEXITCODE -ne 0) { throw "step failed (exit $LASTEXITCODE): $($cmdArgs -join ' ')" }
}

function Invoke-Track([string]$tk) {
  $ttl = $Title
  if (-not $ttl) {
    $ttl = if ($tk -eq "frontier") { "Tile Drainage: Research Frontier" }
           else { "Tile Drainage: State of Knowledge" }
  }
  Write-Host "########## TRACK: $tk ##########" -ForegroundColor Green
  Write-Host "=== [a] fine: prep + run ==="
  Run @("prep_inputs.py", "--step", "fine", "--track", $tk)
  Run (@("run_stage.py", "--step", "fine", "--track", $tk) + $Extra)
  Write-Host "=== [b] macro: prep + run ==="
  Run @("prep_inputs.py", "--step", "macro", "--track", $tk)
  Run (@("run_stage.py", "--step", "macro", "--track", $tk) + $Extra)
  Write-Host "=== [c] overall: prep + run ==="
  Run @("prep_inputs.py", "--step", "overall", "--track", $tk, "--title", $ttl)
  Run (@("run_overall.py", "--track", $tk) + $Extra)
  Write-Host "=== done -> latex/agentic/$tk/overall/paper/main.pdf ===" -ForegroundColor Green
}

$tracks = if ($Track -eq "both") { @("frontier", "knowledge") } else { @($Track) }
foreach ($tk in $tracks) { Invoke-Track $tk }
