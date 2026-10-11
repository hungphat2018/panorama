# Samples the player's own GPU engine utilisation per engine type (3d, copy, videodecode, ...), its
# dedicated GPU memory and the machine's total CPU load once a second, while a process with the given
# name is alive. Writes <OutCsv> (one row per second) and <OutCsv>.summary.json (mean and max of each
# column, first 3 s skipped). Percentages are of one engine of the GPU, as Task Manager shows them.
#
#   pwsh -NoProfile -File sample-gpu-cpu.ps1 -ProcessName perf0-master -OutCsv out\run-load.csv
param(
    [Parameter(Mandatory)][string]$ProcessName,
    [Parameter(Mandatory)][string]$OutCsv,
    [int]$WaitSeconds = 40,
    [int]$MaxSeconds = 200,
    [int]$SkipSeconds = 3
)
$ErrorActionPreference = 'Stop'
$clock = [Diagnostics.Stopwatch]::StartNew()

$proc = $null
while (-not $proc -and $clock.Elapsed.TotalSeconds -lt $WaitSeconds) {
    $proc = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $proc) { Start-Sleep -Milliseconds 50 }
}
if (-not $proc) {
    Set-Content -Path $OutCsv -Value "error: process $ProcessName not found"
    exit 1
}
$procId = $proc.Id
$t0 = $clock.Elapsed.TotalSeconds

$engRe = [regex]'^pid_(\d+)_luid_0x[0-9a-f]+_(0x[0-9a-f]+)_phys_\d+_eng_\d+_engtype_(.+)$'
$memRe = [regex]'^pid_(\d+)_'
$rows = New-Object System.Collections.Generic.List[hashtable]
$columns = New-Object System.Collections.Generic.HashSet[string]

while (-not $proc.HasExited -and $clock.Elapsed.TotalSeconds -lt $MaxSeconds) {
    try {
        # Only this process' instances: expanding every process' GPU instances takes about 3 s per sample.
        $c = Get-Counter -Counter @(
            "\GPU Engine(pid_${procId}_*)\Utilization Percentage",
            "\GPU Process Memory(pid_${procId}_*)\Dedicated Usage",
            '\Processor Information(_Total)\% Processor Time') -SampleInterval 1 -MaxSamples 1 -ErrorAction Stop
    } catch { continue }

    $row = @{ t_s = [math]::Round($clock.Elapsed.TotalSeconds - $t0, 2) }
    foreach ($s in $c.CounterSamples) {
        $path = $s.Path
        if ($path -like '*gpu engine*') {
            $m = $engRe.Match($s.InstanceName)
            if (-not $m.Success) { continue }
            if ([int]$m.Groups[1].Value -eq $procId) {
                $pKey = "proc_" + ($m.Groups[3].Value -replace '\s+', '_')
                $row[$pKey] = ($row[$pKey] + 0.0) + $s.CookedValue
                $null = $columns.Add($pKey)
            }
        } elseif ($path -like '*gpu process memory*') {
            $m = $memRe.Match($s.InstanceName)
            if ($m.Success -and [int]$m.Groups[1].Value -eq $procId) {
                $row['proc_vram_mb'] = ($row['proc_vram_mb'] + 0.0) + $s.CookedValue / 1MB
                $null = $columns.Add('proc_vram_mb')
            }
        } elseif ($path -like '*processor time*') {
            $row['cpu_total_pct'] = $s.CookedValue
            $null = $columns.Add('cpu_total_pct')
        }
    }
    $rows.Add($row)
}

$cols = @('t_s') + ($columns | Sort-Object)
$lines = New-Object System.Collections.Generic.List[string]
$lines.Add($cols -join ',')
foreach ($r in $rows) {
    $lines.Add((($cols | ForEach-Object { if ($r.ContainsKey($_)) { '{0:N2}' -f $r[$_] } else { '0' } }) -join ','))
}
Set-Content -Path $OutCsv -Value $lines

$summary = [ordered]@{ pid = $procId; samples = $rows.Count; skipped_first_s = $SkipSeconds; columns = [ordered]@{} }
foreach ($col in ($columns | Sort-Object)) {
    $vals = @($rows | Where-Object { $_.t_s -ge $SkipSeconds } | ForEach-Object { if ($_.ContainsKey($col)) { [double]$_[$col] } else { 0.0 } })
    if ($vals.Count -gt 0) {
        $m = $vals | Measure-Object -Average -Maximum
        $summary.columns[$col] = [ordered]@{ mean = [math]::Round($m.Average, 2); max = [math]::Round($m.Maximum, 2) }
    }
}
$summary | ConvertTo-Json -Depth 5 | Set-Content -Path ($OutCsv + '.summary.json')
