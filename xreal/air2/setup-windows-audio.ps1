#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess)]
param([switch]$Undo)

$ErrorActionPreference = 'Stop'

function DeviceProperty($id, $name) {
    (Get-PnpDeviceProperty -InstanceId $id -KeyName "DEVPKEY_Device_$name" -WhatIf:$false).Data
}

function IntelAudioBlock {
    [byte[]]$block = New-Object byte[] 128
    ([byte[]](2,3,20,65,35,9,4,1,131,1,0,0,101,3,12,0,16,0,65,4)).CopyTo($block, 0)
    $block[127] = 0x28
    return ,$block
}

function PciSlot($id) {
    foreach ($path in (DeviceProperty $id 'LocationPaths')) {
        # The graphics and HD Audio PCI functions share the bridge path and device number.
        if ($path -match '^(PCIROOT\(.+\).*#PCI\([0-9a-f]{2})[0-9a-f]{2}\)$') { return $Matches[1] }
    }
}

function Get-PortTarget([switch]$ForUndo) {
    $monitors = @(Get-PnpDevice -Class Monitor -PresentOnly | Where-Object InstanceId -like 'DISPLAY\MRG3135\*')
    if ($monitors.Count -ne 1) { throw "Connect one Air 2 Pro directly to the port to configure. Detected $($monitors.Count)." }
    $id = $monitors[0].InstanceId
    $gpu = DeviceProperty $id 'Parent'
    if ($gpu -like 'PCI\VEN_8086&*') {
        $parent = "HKLM:\SYSTEM\CurrentControlSet\Enum\$id\Device Parameters"
        $audio = IntelAudioBlock
        $intel = @{ Device=$id; Path="$parent\EDID_OVERRIDE"; Names=@('1'); Bytes=$audio; Port='Intel' }
        if ($ForUndo) { return $intel }
        [byte[]]$edid = Get-ItemPropertyValue -LiteralPath $parent -Name EDID
        [byte[]]$quiet = New-Object byte[] 128
        ([byte[]](2,3,12,1,101,3,12,0,16,0,65,4)).CopyTo($quiet, 0)
        $quiet[127] = 0x25
        if ($edid.Length -ne 256 -or [BitConverter]::ToString($edid,8,4) -ne '36-47-35-31' -or $edid[126] -ne 1) {
            throw 'Unrecognized Air 2 Pro EDID.'
        }
        $extension = [Convert]::ToBase64String($edid,128,128)
        if ($extension -notin @([Convert]::ToBase64String($audio), [Convert]::ToBase64String($quiet))) {
            throw 'This EDID extension does not match the tested kit DP image.'
        }
        return $intel
    }
    if ($gpu -notlike 'PCI\VEN_10DE&*') { throw "Unsupported graphics adapter: $gpu" }
    $slot = PciSlot $gpu
    if (-not $slot) { throw 'Cannot identify the NVIDIA GPU PCI slot.' }
    $devices = @(Get-PnpDevice -Class MEDIA -PresentOnly | Where-Object {
        $_.InstanceId -like 'HDAUDIO\FUNC_01&VEN_10DE&*' -and
        (PciSlot (DeviceProperty $_.InstanceId 'Parent')) -eq $slot
    })
    if ($devices.Count -ne 1) { throw "Expected one NVIDIA audio device in the GPU's PCI slot. Detected $($devices.Count)." }
    $id = $devices[0].InstanceId
    $driver = DeviceProperty $id 'Driver'
    if ($driver -notmatch '^\{4d36e96c-e325-11ce-bfc1-08002be10318\}\\\d{4}$') { throw 'Unexpected audio driver key.' }
    return @{ Device=$id; Path="HKLM:\SYSTEM\CurrentControlSet\Control\Class\$driver\PowerSettings"
        Names=@('PerformanceIdleTime','ConservationIdleTime'); Bytes=[byte[]](0,0,0,0); Port='NVIDIA' }
}

# A missing value is null. Base64 preserves the exact bytes (including an empty REG_BINARY).
function Read-Binary($key, $name) {
    if ($key -and $name -in $key.GetValueNames()) {
        if ($key.GetValueKind($name) -ne 'Binary') { throw "Unexpected registry type for $name in $($key.Name)." }
        return [Convert]::ToBase64String($key.GetValue($name))
    }
    return $null
}

function Restart-Target($id) {
    & "$env:SystemRoot\System32\pnputil.exe" /restart-device $id | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Device restart returned $LASTEXITCODE. Reconnect the glasses and rerun the same command." }
}

function Set-PortFix($target, $store, [bool]$restore) {
    $path = $target.Path
    $id = $target.Device
    $name = $id.Replace('\','#')
    $desired = [Convert]::ToBase64String($target.Bytes)
    $key = if (Test-Path -LiteralPath $path) { Get-Item -LiteralPath $path }
    $backupKey = if (Test-Path -LiteralPath $store) { Get-Item -LiteralPath $store }
    $saved = if ($backupKey) { $backupKey.GetValue($name) }
    if ($restore -and -not $saved) { Write-Output "No saved changes for this $($target.Port) device."; return }

    $current = @{}
    foreach ($field in $target.Names) { $current[$field] = Read-Binary $key $field }
    if ($saved) {
        $record = $saved | ConvertFrom-Json
        if ($record.Path -ne $path) { throw 'The driver key has changed since setup. Saved originals were retained.' }
        foreach ($field in $target.Names) {
            if ($field -notin $record.Original.PSObject.Properties.Name -or
                ($current[$field] -cne $desired -and $current[$field] -cne $record.Original.$field)) {
                throw "Setting $field changed outside this setup. Saved originals were retained."
            }
        }
        if (-not $restore -and $record.Applied -and
            @($target.Names | Where-Object { $current[$_] -cne $desired }).Count -eq 0) {
            Write-Output "Already applied to this $($target.Port) device. Original settings retained."; return
        }
    } else {
        if (@($target.Names | Where-Object { $current[$_] -cne $desired }).Count -eq 0) {
            Write-Output "The $($target.Port) settings already match. No changes to undo."; return
        }
        if ($target.Port -eq 'Intel' -and $null -ne $current['1']) { throw 'An existing Intel EDID block 1 override differs. Leaving it intact.' }
        if (-not $backupKey) { New-Item -Path $store -Force | Out-Null }
        $record = [pscustomobject]@{ Path=$path; KeyExisted=($null -ne $key); Original=[pscustomobject]$current; Applied=$false }
        # Save before the first edit so undo also works after an interrupted setup.
        New-ItemProperty -LiteralPath $store -Name $name -PropertyType String -Value ($record | ConvertTo-Json -Compress) | Out-Null
    }

    foreach ($field in $target.Names) {
        $value = if ($restore) { $record.Original.$field } else { $desired }
        if ($current[$field] -ceq $value) { continue }
        if ($null -eq $value) {
            Remove-ItemProperty -LiteralPath $path -Name $field
        } else {
            if (-not (Test-Path -LiteralPath $path)) { New-Item -Path $path | Out-Null }
            New-ItemProperty -LiteralPath $path -Name $field -PropertyType Binary -Value ([Convert]::FromBase64String($value)) -Force | Out-Null
        }
    }
    if ($restore -and -not $record.KeyExisted) {
        $key = Get-Item -LiteralPath $path -ErrorAction SilentlyContinue
        if ($key -and $key.ValueCount -eq 0 -and $key.SubKeyCount -eq 0) { Remove-Item -LiteralPath $path }
    }
    Restart-Target $id
    if ($restore) {
        Remove-ItemProperty -LiteralPath $store -Name $name
        Write-Output "Restored original $($target.Port) settings."
    } else {
        $record.Applied = $true
        Set-ItemProperty -LiteralPath $store -Name $name -Value ($record | ConvertTo-Json -Compress)
        Write-Output "Applied $($target.Port) settings. Original values saved under $store. Cold-reconnect the glasses."
    }
}

if ($MyInvocation.InvocationName -eq '.') { return }
if (-not [Environment]::Is64BitProcess) { throw 'Run 64-bit Windows PowerShell.' }
$target = Get-PortTarget -ForUndo:$Undo
$action = if ($Undo) { 'Restore original settings and restart device' } else { 'Apply DP audio settings and restart device if needed' }
if (-not $PSCmdlet.ShouldProcess("$($target.Port) $($target.Device) [$($target.Path)]", $action)) { return }
$user = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $user.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Open PowerShell as administrator and rerun the command.' }
$lock = [Threading.Mutex]::new($false, 'Global\XrealAir2ProDpAudioSetup')
try {
    try { $acquired = $lock.WaitOne(0) } catch [Threading.AbandonedMutexException] { $acquired = $true }
    if (-not $acquired) { throw 'Another DP audio setup is running.' }
    Set-PortFix $target 'HKLM:\SOFTWARE\XREAL-Air2Pro-DP-Audio' $Undo.IsPresent
} finally {
    if ($acquired) { $lock.ReleaseMutex() }
    $lock.Dispose()
}
