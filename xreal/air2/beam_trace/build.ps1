param(
    [Parameter(Mandatory)][string]$Sdk,
    [Parameter(Mandatory)][string]$Jdk,
    [Parameter(Mandatory)][string]$Out
)

$ErrorActionPreference = 'Stop'
$jar = Join-Path $Sdk 'platforms/android-36/android.jar'
$tools = Join-Path $Sdk 'build-tools/36.0.0'
$javaTools = Join-Path $Jdk 'bin'
$env:JAVA_HOME = $Jdk
$outFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Out)
$work = Join-Path ([System.IO.Path]::GetTempPath()) ('xreal-audio-trace-build-' + [guid]::NewGuid())
$classes = Join-Path $work 'classes'
New-Item -ItemType Directory -Path $classes | Out-Null

function Run-Tool([string]$Tool, [string[]]$Arguments) {
    & $Tool @Arguments
    if ($LASTEXITCODE) { throw "Build tool failed with exit code ${LASTEXITCODE}: $Tool" }
}

$signingDir = Join-Path ([System.IO.Path]::GetTempPath()) 'xreal-audio-trace-signing'
New-Item -ItemType Directory -Force -Path $signingDir | Out-Null
$key = Join-Path $signingDir 'debug.keystore'
if (!(Test-Path -LiteralPath $key)) {
    Run-Tool (Join-Path $javaTools 'keytool.exe') @('-genkeypair', '-keystore', $key,
        '-storepass', 'android', '-keypass', 'android', '-alias', 'androiddebugkey',
        '-dname', 'CN=XREAL Audio Trace Debug', '-keyalg', 'RSA', '-validity', '3650')
}

Push-Location $work
try {
    Run-Tool (Join-Path $javaTools 'javac.exe') @('--release', '8',
        '-classpath', $jar, '-d', $classes, (Join-Path $PSScriptRoot 'TraceActivity.java'))
    Run-Tool (Join-Path $javaTools 'jar.exe') @('cf', 'trace.jar', '-C', $classes, '.')
    Run-Tool (Join-Path $tools 'd8.bat') @('--lib', $jar, '--min-api', '26', '--output', '.', 'trace.jar')
    Run-Tool (Join-Path $tools 'aapt.exe') @('package', '-f', '-M',
        (Join-Path $PSScriptRoot 'AndroidManifest.xml'), '-I', $jar, '-F', 'unsigned.apk')
    Run-Tool (Join-Path $tools 'aapt.exe') @('add', 'unsigned.apk', 'classes.dex')
    Run-Tool (Join-Path $tools 'zipalign.exe') @('-f', '4', 'unsigned.apk', 'aligned.apk')
    Run-Tool (Join-Path $tools 'apksigner.bat') @('sign', '--ks', $key, '--ks-pass',
        'pass:android', '--ks-key-alias', 'androiddebugkey', '--out', $outFile, 'aligned.apk')
    Run-Tool (Join-Path $tools 'apksigner.bat') @('verify', '--verbose', $outFile)
} finally {
    Pop-Location
}
