param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$Python = "python",
    [string]$WslPython = ".venv-wsl/bin/python",
    [string]$Capabilities = "artifacts/capabilities-wsl-frontier.json",
    [string]$EnvReport = "artifacts/env-report-wsl-frontier.json",
    [string]$ResultsRoot = "results/frontier-wsl-precision-campaign",
    [string]$PlotsRoot = "plots/frontier-wsl-precision-campaign",
    [int]$TransportRetries = 2,
    [string[]]$SelectedSlice,
    [switch]$Resume
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Utf8NoBom {
    param(
        [string]$Path,
        [string]$Content
    )

    $encoding = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Convert-WindowsPathToWsl {
    param([string]$Path)

    $full = [System.IO.Path]::GetFullPath($Path)
    if ($full -match "^([A-Za-z]):\\(.*)$") {
        $drive = $matches[1].ToLowerInvariant()
        $tail = ($matches[2] -replace "\\", "/")
        return "/mnt/$drive/$tail"
    }

    return ($full -replace "\\", "/")
}

function Quote-ForBash {
    param([string]$Value)

    $single = [string][char]39
    $double = [string][char]34
    $escaped = $Value.Replace($single, $single + $double + $single + $double + $single)
    return $single + $escaped + $single
}

function Invoke-RepoPython {
    param([string[]]$Arguments)

    $commandOutput = & $Python -m quantum_bench @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $Python -m quantum_bench $($Arguments -join ' ')"
    }
    foreach ($line in @($commandOutput)) {
        if ($null -ne $line -and "$line".Length -gt 0) {
            Write-Host $line
        }
    }
}

function Invoke-WslQuantumBench {
    param([string[]]$Arguments)

    $repoRootWsl = Convert-WindowsPathToWsl $RepoRoot
    $quotedArgs = $Arguments | ForEach-Object { Quote-ForBash $_ }
    $command = "set -euo pipefail && cd $(Quote-ForBash $repoRootWsl) && $(Quote-ForBash $WslPython) -m quantum_bench $($quotedArgs -join ' ')"
    $commandOutput = & wsl bash -lc $command
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed: $command"
    }
    foreach ($line in @($commandOutput)) {
        if ($null -ne $line -and "$line".Length -gt 0) {
            Write-Host $line
        }
    }
}

function Get-LatestRunDirectory {
    param([string]$BaseDirectory)

    $directories = @(Get-ChildItem -LiteralPath $BaseDirectory -Directory | Sort-Object LastWriteTimeUtc -Descending)
    if (-not $directories) {
        throw "No run directory was created under $BaseDirectory"
    }

    return $directories[0].FullName
}

function Get-StageOutput {
    param([string]$StageDirectory)

    $latestRun = Get-LatestRunDirectory $StageDirectory
    $summaryPath = Join-Path $latestRun "analysis-summary.json"
    $resultsPath = Join-Path $latestRun "results.json"

    if (-not (Test-Path -LiteralPath $summaryPath)) {
        Invoke-RepoPython @("report", "--input-dir", $latestRun)
    }

    $summary = Get-Content -LiteralPath $summaryPath -Raw | ConvertFrom-Json
    $rows = @(Get-Content -LiteralPath $resultsPath -Raw | ConvertFrom-Json)

    $stableRow = $summary.stable_frontier | Select-Object -First 1
    $actualFailures = @(
        $rows | Where-Object {
            ($_.success -eq $false) -and
            ($_.error_type -ne "frontier_pruned")
        }
    )
    $firstFailureQubits = @(
        $actualFailures |
        ForEach-Object {
            $value = $_.qubits
            if ($value -is [System.Array]) {
                $value = $value[0]
            }
            [int]$value
        } |
        Sort-Object -Unique
    )

    return [PSCustomObject]@{
        RunDirectory = $latestRun
        SummaryPath = $summaryPath
        ResultsPath = $resultsPath
        Summary = $summary
        StableMaxQubits = if ($null -ne $stableRow.stable_max_qubits) { [int]$stableRow.stable_max_qubits } else { $null }
        FirstFailureQubits = $firstFailureQubits
        SuccessRate = [double]$summary.counts.success_rate_non_warmup_pct
        TestedQubitMax = if ($null -ne $summary.matrix.qubit_max) { [int]$summary.matrix.qubit_max } else { $null }
    }
}

function New-SliceProfile {
    param(
        [pscustomobject]$Slice,
        [string]$ProfileName,
        [int[]]$QubitGrid,
        [string]$ResultsDir
    )

    $benchmark = [ordered]@{
        library = "qiskit_aer"
        backend = "aer_statevector"
        devices = @($Slice.Device)
        precisions = @($Slice.Precision)
        families = @($Slice.Family)
        qubit_grid = [ordered]@{
            ($Slice.Family) = @($QubitGrid)
        }
        seeds = if ($Slice.Family -eq "ghz") {
            [ordered]@{ ghz = @(101) }
        }
        else {
            [ordered]@{ default = @(101, 202) }
        }
    }

    if ($Slice.Family -eq "random") {
        $benchmark["depths"] = [ordered]@{ random = 8 }
    }
    elseif ($Slice.Family -eq "ansatz") {
        $benchmark["depths"] = [ordered]@{ ansatz = 6 }
    }
    elseif ($Slice.Family -eq "trotter") {
        $benchmark["depths"] = [ordered]@{ trotter = 12 }
    }

    if ($Slice.Device -eq "CPU") {
        $benchmark["memory_limits"] = [ordered]@{ ram_source = "total" }
    }

    return [ordered]@{
        profile_name = $ProfileName
        execution_env = "wsl2_ubuntu_gpu_frontier"
        preliminary = $false
        frontier_stop_on_failure = $true
        results_dir = $ResultsDir
        max_reference_qubits = 12
        defaults = [ordered]@{
            warmups = 1
            repeats = 3
            thread_modes = @("all")
            timeouts = [ordered]@{
                case_s = 900
            }
            memory_limits = [ordered]@{
                ram_fraction = 0.75
                vram_fraction = 0.80
                overhead_factor = 1.5
            }
        }
        benchmarks = @($benchmark)
    }
}

function Invoke-SliceStage {
    param(
        [pscustomobject]$Slice,
        [string]$StageName,
        [int[]]$QubitGrid
    )

    $stageRoot = Join-Path $RepoRoot $ResultsRoot
    $sliceDirectoryName = "$($Slice.Name)\$StageName"
    $stageDirectory = Join-Path $stageRoot $sliceDirectoryName
    $plotsDirectory = Join-Path (Join-Path $RepoRoot $PlotsRoot) $sliceDirectoryName
    $profileDirectory = Join-Path $RepoRoot ".campaign-temp"
    $profilePath = Join-Path $profileDirectory "$($Slice.Name)-$StageName.json"
    $profileName = "$($Slice.Name)-$StageName"

    New-Item -ItemType Directory -Force -Path $stageDirectory | Out-Null
    New-Item -ItemType Directory -Force -Path $plotsDirectory | Out-Null
    New-Item -ItemType Directory -Force -Path $profileDirectory | Out-Null

    $existingSummary = $null
    if ($Resume) {
        $existingRuns = @(Get-ChildItem -LiteralPath $stageDirectory -Directory -ErrorAction SilentlyContinue)
        if ($existingRuns) {
            $latestExistingRun = Get-LatestRunDirectory $stageDirectory
            $summaryCandidate = Join-Path $latestExistingRun "analysis-summary.json"
            if (Test-Path -LiteralPath $summaryCandidate) {
                Write-Host "Reusing existing stage $($Slice.Name)/$StageName at $latestExistingRun"
                $existingSummary = Get-StageOutput $stageDirectory
            }
        }
    }

    if ($existingSummary) {
        return $existingSummary
    }

    $profile = New-SliceProfile -Slice $Slice -ProfileName $profileName -QubitGrid $QubitGrid -ResultsDir (Join-Path $ResultsRoot $sliceDirectoryName)
    Write-Utf8NoBom -Path $profilePath -Content (($profile | ConvertTo-Json -Depth 20) + [Environment]::NewLine)

    Write-Host "Running stage $($Slice.Name)/$StageName on qubits: $($QubitGrid -join ', ')"
    Invoke-RepoPython @(
        "run-wsl",
        "--profile", $profilePath,
        "--capabilities", (Join-Path $RepoRoot $Capabilities),
        "--env-report", (Join-Path $RepoRoot $EnvReport),
        "--results-dir", (Join-Path $ResultsRoot $sliceDirectoryName),
        "--repo-root", $RepoRoot,
        "--wsl-python", $WslPython,
        "--transport-retries", $TransportRetries.ToString()
    )

    $stageOutput = Get-StageOutput $stageDirectory
    try {
        Invoke-WslQuantumBench @(
            "plot",
            "--input-dir", (Convert-WindowsPathToWsl $stageOutput.RunDirectory),
            "--output-dir", (Convert-WindowsPathToWsl $plotsDirectory)
        )
    }
    catch {
        Write-Warning ("Plot generation failed for {0}/{1}: {2}" -f $Slice.Name, $StageName, $_.Exception.Message)
    }
    return $stageOutput
}

function Get-IntegerRange {
    param(
        [int]$Start,
        [int]$EndInclusive
    )

    if ($EndInclusive -lt $Start) {
        return @()
    }

    $values = New-Object System.Collections.Generic.List[int]
    for ($i = $Start; $i -le $EndInclusive; $i++) {
        $values.Add($i)
    }
    return $values.ToArray()
}

function Resolve-SliceStages {
    param([pscustomobject]$Slice)

    $allStageOutputs = New-Object System.Collections.Generic.List[object]

    $primaryOutput = Invoke-SliceStage -Slice $Slice -StageName "primary" -QubitGrid $Slice.PrimaryGrid
    $allStageOutputs.Add($primaryOutput)

    if ($null -eq $primaryOutput.StableMaxQubits) {
        $fallbackOutput = Invoke-SliceStage -Slice $Slice -StageName "fallback-low" -QubitGrid $Slice.FallbackGrid
        $allStageOutputs.Add($fallbackOutput)

        if ($null -eq $fallbackOutput.StableMaxQubits) {
            $microOutput = Invoke-SliceStage -Slice $Slice -StageName "fallback-micro" -QubitGrid $Slice.MicroGrid
            $allStageOutputs.Add($microOutput)
        }
    }

    $bestStable = $null
    foreach ($stageOutput in $allStageOutputs) {
        if ($null -ne $stageOutput.StableMaxQubits) {
            if ($null -eq $bestStable -or $stageOutput.StableMaxQubits -gt $bestStable) {
                $bestStable = $stageOutput.StableMaxQubits
            }
        }
    }

    $firstActualFailure = $null
    foreach ($stageOutput in $allStageOutputs) {
        foreach ($qubits in $stageOutput.FirstFailureQubits) {
            if ($null -eq $firstActualFailure -or $qubits -lt $firstActualFailure) {
                $firstActualFailure = $qubits
            }
        }
    }

    if ($null -ne $bestStable -and $null -ne $firstActualFailure -and ($firstActualFailure - $bestStable) -gt 1) {
        $refinementGrid = Get-IntegerRange -Start ($bestStable + 1) -EndInclusive ($firstActualFailure - 1)
        if ($refinementGrid.Count -gt 0) {
            $refineOutput = Invoke-SliceStage -Slice $Slice -StageName "refine-gap" -QubitGrid $refinementGrid
            $allStageOutputs.Add($refineOutput)
        }
    }

    while ($true) {
        $bestStable = $null
        $firstActualFailure = $null
        $maxTestedQubits = $null

        foreach ($stageOutput in $allStageOutputs) {
            if ($null -ne $stageOutput.StableMaxQubits) {
                if ($null -eq $bestStable -or $stageOutput.StableMaxQubits -gt $bestStable) {
                    $bestStable = $stageOutput.StableMaxQubits
                }
            }

            foreach ($qubits in $stageOutput.FirstFailureQubits) {
                if ($null -eq $firstActualFailure -or $qubits -lt $firstActualFailure) {
                    $firstActualFailure = $qubits
                }
            }

            if ($null -ne $stageOutput.TestedQubitMax) {
                if ($null -eq $maxTestedQubits -or $stageOutput.TestedQubitMax -gt $maxTestedQubits) {
                    $maxTestedQubits = $stageOutput.TestedQubitMax
                }
            }
        }

        if ($null -eq $bestStable -or $null -ne $firstActualFailure -or $null -eq $maxTestedQubits) {
            break
        }

        if ($bestStable -lt $maxTestedQubits) {
            break
        }

        $nextQubit = $bestStable + 1
        $extendOutput = Invoke-SliceStage -Slice $Slice -StageName ("extend-{0}q" -f $nextQubit) -QubitGrid @($nextQubit)
        $allStageOutputs.Add($extendOutput)

        if ($extendOutput.StableMaxQubits -ne $nextQubit) {
            break
        }
    }

    return $allStageOutputs
}

$slices = @(
    [pscustomobject]@{ Name = "cpu-double-ghz"; Device = "CPU"; Precision = "double"; Family = "ghz"; PrimaryGrid = @(20, 22, 24, 26, 28, 29); FallbackGrid = @(12, 14, 16, 18, 20); MicroGrid = @(8, 10, 12) },
    [pscustomobject]@{ Name = "cpu-double-random"; Device = "CPU"; Precision = "double"; Family = "random"; PrimaryGrid = @(20, 22, 24, 26, 28, 29); FallbackGrid = @(12, 14, 16, 18, 20); MicroGrid = @(8, 10, 12) },
    [pscustomobject]@{ Name = "cpu-double-ansatz"; Device = "CPU"; Precision = "double"; Family = "ansatz"; PrimaryGrid = @(20, 22, 24, 26, 28, 29); FallbackGrid = @(12, 14, 16, 18, 20); MicroGrid = @(8, 10, 12) },
    [pscustomobject]@{ Name = "cpu-double-trotter"; Device = "CPU"; Precision = "double"; Family = "trotter"; PrimaryGrid = @(20, 22, 24, 26, 28, 29); FallbackGrid = @(12, 14, 16, 18, 20); MicroGrid = @(8, 10, 12) },
    [pscustomobject]@{ Name = "gpu-double-ghz"; Device = "GPU"; Precision = "double"; Family = "ghz"; PrimaryGrid = @(18, 20, 22, 24, 26, 27, 28); FallbackGrid = @(10, 12, 14, 16, 18); MicroGrid = @(8, 10, 12) },
    [pscustomobject]@{ Name = "gpu-double-random"; Device = "GPU"; Precision = "double"; Family = "random"; PrimaryGrid = @(18, 20, 22, 24, 26, 27, 28); FallbackGrid = @(10, 12, 14, 16, 18); MicroGrid = @(8, 10, 12) },
    [pscustomobject]@{ Name = "gpu-double-ansatz"; Device = "GPU"; Precision = "double"; Family = "ansatz"; PrimaryGrid = @(18, 20, 22, 24, 26, 27, 28); FallbackGrid = @(10, 12, 14, 16, 18); MicroGrid = @(8, 10, 12) },
    [pscustomobject]@{ Name = "gpu-double-trotter"; Device = "GPU"; Precision = "double"; Family = "trotter"; PrimaryGrid = @(18, 20, 22, 24, 26, 27, 28); FallbackGrid = @(10, 12, 14, 16, 18); MicroGrid = @(8, 10, 12) },
    [pscustomobject]@{ Name = "gpu-single-ghz"; Device = "GPU"; Precision = "single"; Family = "ghz"; PrimaryGrid = @(20, 22, 24, 26, 27, 28, 29); FallbackGrid = @(12, 14, 16, 18, 20); MicroGrid = @(8, 10, 12) },
    [pscustomobject]@{ Name = "gpu-single-random"; Device = "GPU"; Precision = "single"; Family = "random"; PrimaryGrid = @(20, 22, 24, 26, 27, 28, 29); FallbackGrid = @(12, 14, 16, 18, 20); MicroGrid = @(8, 10, 12) },
    [pscustomobject]@{ Name = "gpu-single-ansatz"; Device = "GPU"; Precision = "single"; Family = "ansatz"; PrimaryGrid = @(20, 22, 24, 26, 27, 28, 29); FallbackGrid = @(12, 14, 16, 18, 20); MicroGrid = @(8, 10, 12) },
    [pscustomobject]@{ Name = "gpu-single-trotter"; Device = "GPU"; Precision = "single"; Family = "trotter"; PrimaryGrid = @(20, 22, 24, 26, 27, 28, 29); FallbackGrid = @(12, 14, 16, 18, 20); MicroGrid = @(8, 10, 12) }
)

if ($SelectedSlice) {
    $allowed = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($sliceName in $SelectedSlice) {
        [void]$allowed.Add($sliceName)
    }
    $slices = @($slices | Where-Object { $allowed.Contains($_.Name) })
    if (-not $slices) {
        throw "No matching slices were selected."
    }
}

$campaignRecords = New-Object System.Collections.Generic.List[object]

foreach ($slice in $slices) {
    Write-Host ""
    Write-Host "=== Slice: $($slice.Name) ==="
    $stageOutputs = Resolve-SliceStages -Slice $slice

    $bestStable = $null
    $firstActualFailure = $null
    foreach ($stageOutput in $stageOutputs) {
        if ($null -ne $stageOutput.StableMaxQubits) {
            if ($null -eq $bestStable -or $stageOutput.StableMaxQubits -gt $bestStable) {
                $bestStable = $stageOutput.StableMaxQubits
            }
        }

        foreach ($failureQubits in $stageOutput.FirstFailureQubits) {
            if ($null -eq $firstActualFailure -or $failureQubits -lt $firstActualFailure) {
                $firstActualFailure = $failureQubits
            }
        }
    }

    foreach ($stageOutput in $stageOutputs) {
        $campaignRecords.Add([PSCustomObject]@{
            slice = $slice.Name
            device = $slice.Device
            precision = $slice.Precision
            family = $slice.Family
            run_directory = $stageOutput.RunDirectory
            stable_max_qubits = $stageOutput.StableMaxQubits
            first_failure_qubits = @($stageOutput.FirstFailureQubits)
            success_rate_non_warmup_pct = $stageOutput.SuccessRate
            report_path = (Join-Path $stageOutput.RunDirectory "analysis-report.md")
            summary_path = $stageOutput.SummaryPath
            results_path = $stageOutput.ResultsPath
        })
    }

    Write-Host "Confirmed stable frontier for $($slice.Name): $bestStable"
    if ($null -ne $firstActualFailure) {
        Write-Host "First actual failure for $($slice.Name): $firstActualFailure"
    }
}

$campaignDirectory = Join-Path $RepoRoot $ResultsRoot
New-Item -ItemType Directory -Force -Path $campaignDirectory | Out-Null

$campaignSummaryPath = Join-Path $campaignDirectory "campaign-summary.json"
$campaignMarkdownPath = Join-Path $campaignDirectory "campaign-summary.md"

$finalSummary = $campaignRecords |
    Group-Object slice |
    ForEach-Object {
        $group = $_.Group
        $bestStable = $null
        $firstActualFailure = $null
        $stageDirectories = New-Object System.Collections.Generic.List[string]

        foreach ($record in $group) {
            $stageDirectories.Add($record.run_directory)
            if ($null -ne $record.stable_max_qubits) {
                if ($null -eq $bestStable -or $record.stable_max_qubits -gt $bestStable) {
                    $bestStable = $record.stable_max_qubits
                }
            }

            foreach ($failureQubits in $record.first_failure_qubits) {
                if ($null -eq $firstActualFailure -or $failureQubits -lt $firstActualFailure) {
                    $firstActualFailure = $failureQubits
                }
            }
        }

        [PSCustomObject]@{
            slice = $_.Name
            device = $group[0].device
            precision = $group[0].precision
            family = $group[0].family
            confirmed_stable_max_qubits = $bestStable
            first_actual_failure_qubits = $firstActualFailure
            stage_runs = $stageDirectories
        }
    } |
    Sort-Object device, precision, family

$campaignPayload = [ordered]@{
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    results_root = (Join-Path $RepoRoot $ResultsRoot)
    plots_root = (Join-Path $RepoRoot $PlotsRoot)
    capabilities_path = (Join-Path $RepoRoot $Capabilities)
    env_report_path = (Join-Path $RepoRoot $EnvReport)
    stages = $campaignRecords
    final_summary = $finalSummary
}

Write-Utf8NoBom -Path $campaignSummaryPath -Content (($campaignPayload | ConvertTo-Json -Depth 20) + [Environment]::NewLine)

$generatedAt = (Get-Date).ToUniversalTime().ToString("o")
$markdownLines = New-Object System.Collections.Generic.List[string]
$markdownLines.Add("# WSL Frontier Precision Campaign")
$markdownLines.Add("")
$markdownLines.Add("Generated at: ``$generatedAt``")
$markdownLines.Add("")
$markdownLines.Add("| Slice | Device | Precision | Family | Confirmed stable max qubits | First actual failure qubits |")
$markdownLines.Add("|---|---|---|---|---:|---:|")
foreach ($row in $finalSummary) {
    $stableText = if ($null -ne $row.confirmed_stable_max_qubits) { $row.confirmed_stable_max_qubits } else { "-" }
    $failureText = if ($null -ne $row.first_actual_failure_qubits) { $row.first_actual_failure_qubits } else { "-" }
    $markdownLines.Add("| $($row.slice) | $($row.device) | $($row.precision) | $($row.family) | $stableText | $failureText |")
}

$markdownLines.Add("")
$markdownLines.Add("## Stage Runs")
$markdownLines.Add("")
foreach ($row in $campaignRecords) {
    $failuresText = if ($row.first_failure_qubits.Count -gt 0) { ($row.first_failure_qubits -join ", ") } else { "-" }
    $stableText = if ($null -ne $row.stable_max_qubits) { $row.stable_max_qubits } else { "-" }
    $runName = Split-Path $row.run_directory -Leaf
    $markdownLines.Add("- $runName | slice=$($row.slice) | stable=$stableText | first_failure=$failuresText | report=$($row.report_path)")
}

Write-Utf8NoBom -Path $campaignMarkdownPath -Content (($markdownLines -join [Environment]::NewLine) + [Environment]::NewLine)

Write-Host ""
Write-Host "Campaign summary written to $campaignSummaryPath"
Write-Host "Campaign markdown written to $campaignMarkdownPath"
