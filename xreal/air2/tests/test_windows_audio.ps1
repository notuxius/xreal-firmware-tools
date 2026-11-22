# Run with Windows PowerShell. Uses a disposable current-user registry key.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\setup-windows-audio.ps1')
function Assert($condition, $message) { if (-not $condition) { throw $message } }
function Restart-Target($id) {
    $script:restarts++
    if ($script:failRestart) { throw 'test restart failure' }
}
$root = 'HKCU:\Software\XrealAudioTest-' + [guid]::NewGuid()
$store = "$root\Originals"
New-Item -Path $root | Out-Null
try {
    $nvidia = @{ Device='test-nvidia'; Path="$root\Audio"; Port='NVIDIA'
        Names=@('PerformanceIdleTime','ConservationIdleTime'); Bytes=[byte[]](0,0,0,0) }
    New-Item -Path $nvidia.Path | Out-Null
    New-ItemProperty -Path $nvidia.Path -Name PerformanceIdleTime -PropertyType Binary -Value ([byte[]](9,0,0,0)) | Out-Null
    $script:failRestart = $true
    try { Set-PortFix $nvidia $store $false; throw 'expected restart failure' }
    catch { if ($_.Exception.Message -ne 'test restart failure') { throw } }
    $script:failRestart = $false
    Set-PortFix $nvidia $store $false
    Set-PortFix $nvidia $store $false
    Assert ($script:restarts -eq 2) 'Repeat apply restarted the device'
    Assert ((Read-Binary (Get-Item $nvidia.Path) 'PerformanceIdleTime') -ceq 'AAAAAA==') 'Timeout not disabled'

    $intel = @{ Device='test-intel'; Path="$root\Override"; Port='Intel'; Names=@('1'); Bytes=(IntelAudioBlock) }
    Set-PortFix $intel $store $false
    $block = Get-ItemPropertyValue -Path $intel.Path -Name 1
    Assert ($block.Length -eq 128 -and ($block | Measure-Object -Sum).Sum % 256 -eq 0) 'Invalid CTA'
    Set-PortFix $nvidia $store $true
    Assert ((Read-Binary (Get-Item $nvidia.Path) 'PerformanceIdleTime') -ceq 'CQAAAA==') 'Original bytes lost'
    Assert ('ConservationIdleTime' -notin (Get-Item $nvidia.Path).GetValueNames()) 'Absent value not restored'
    Set-PortFix $intel $store $true
    Assert (-not (Test-Path $intel.Path)) 'New Intel key not removed'
    Assert ((Get-Item $store).ValueCount -eq 0) 'Outstanding backup'

    New-Item -Path $intel.Path | Out-Null
    New-ItemProperty -Path $intel.Path -Name 1 -PropertyType Binary -Value ([byte[]](42)) | Out-Null
    try { Set-PortFix $intel $store $false; throw 'expected existing override refusal' }
    catch { if ($_.Exception.Message -notlike 'An existing Intel EDID*') { throw } }
    Assert ((Read-Binary (Get-Item $intel.Path) '1') -ceq 'Kg==') 'Existing override changed'
    'Windows DP audio registry checks passed.'
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force
}
