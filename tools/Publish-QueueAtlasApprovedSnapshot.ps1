# Decrypt locally and provide credentials only to a short-lived child process.
param([switch]$PublishApprovedSnapshot)
if (-not $PublishApprovedSnapshot) { throw 'Explicit approved-publication switch is required' }
$ErrorActionPreference = 'Stop'
$process = $null
$credential = $null
try {
    $directory = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'QueueAtlas\credentials'
    if ($directory.IndexOf('OneDrive',[StringComparison]::OrdinalIgnoreCase) -ge 0) { throw 'Unsafe credential directory' }
    $file = Join-Path $directory 'r2-connectivity.clixml'
    $credential = Import-Clixml -LiteralPath $file
    if ($credential.Version -ne 1 -or $credential.Bucket -ne 'queue-atlas-history' -or $credential.AccountId -notmatch '^[a-f0-9]{32}$') { throw 'Invalid credential metadata' }
    $variables = [ordered]@{
        QUEUE_ATLAS_R2_READ_ACCESS_KEY_ID=$credential.ReadAccessKeyId
        QUEUE_ATLAS_R2_READ_SECRET_ACCESS_KEY=$credential.ReadSecretAccessKey
        QUEUE_ATLAS_R2_PUBLISH_ACCESS_KEY_ID=$credential.PublishAccessKeyId
        QUEUE_ATLAS_R2_PUBLISH_SECRET_ACCESS_KEY=$credential.PublishSecretAccessKey
    }
    $root = Split-Path -Parent $PSScriptRoot
    $python = Join-Path $root 'venv\Scripts\python.exe'
    $report = Join-Path $directory ('r2-publication-' + [guid]::NewGuid().ToString('N'))
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $python
    $start.WorkingDirectory = $root
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.Arguments = '-B -m src.history_private_publish --publish-approved-snapshot --owner-verified-private-settings --report-directory "' + $report + '"'
    $start.EnvironmentVariables['QUEUE_ATLAS_HISTORY_ENABLED'] = '0'
    $start.EnvironmentVariables['QUEUE_ATLAS_R2_ACCOUNT_ID'] = $credential.AccountId
    $start.EnvironmentVariables['QUEUE_ATLAS_R2_BUCKET'] = $credential.Bucket
    foreach ($name in $variables.Keys) {
        $value = $variables[$name]
        if ($value -isnot [Security.SecureString]) { throw 'Credential is not DPAPI-protected' }
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($value)
        try { $start.EnvironmentVariables[$name] = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    }
    $process = [Diagnostics.Process]::Start($start)
    foreach ($name in $variables.Keys) { $start.EnvironmentVariables.Remove($name) }
    $process.WaitForExit()
    $code = $process.ExitCode
    if (Test-Path -LiteralPath $report) { Write-Host 'Private publication report saved outside OneDrive and Git.' } else { Write-Host 'No report was generated; diagnostic details remain suppressed.' }
    exit $code
} catch {
    Write-Error 'Private publication did not complete; credential values and error details are suppressed.' -ErrorAction Continue
    exit 1
} finally {
    if ($null -ne $credential) {
        foreach ($value in @($credential.ReadAccessKeyId,$credential.ReadSecretAccessKey,$credential.PublishAccessKeyId,$credential.PublishSecretAccessKey)) { if ($value -is [Security.SecureString]) { $value.Dispose() } }
    }
    if ($null -ne $process) { $process.Dispose() }
}
