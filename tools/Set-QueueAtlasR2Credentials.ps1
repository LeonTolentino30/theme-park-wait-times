# Run in your own local terminal. Never enter key values into chat or source code.
$ErrorActionPreference = 'Stop'
$readId = $readSecret = $publishId = $publishSecret = $null
try {
    $base = [Environment]::GetFolderPath('LocalApplicationData')
    if (-not $base -or $base.IndexOf('OneDrive',[StringComparison]::OrdinalIgnoreCase) -ge 0) { throw 'Unsafe credential location' }
    $directory = Join-Path $base 'QueueAtlas\credentials'
    $file = Join-Path $directory 'r2-connectivity.clixml'
    if (Test-Path -LiteralPath $file) { throw 'Credential file already exists; refusing overwrite' }
    [void][IO.Directory]::CreateDirectory($directory)
    if ((Get-Item -LiteralPath $directory).LinkType -in @('SymbolicLink','Junction')) { throw 'Unsafe credential directory' }
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $system = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $security = [Security.AccessControl.DirectorySecurity]::new()
    $security.SetAccessRuleProtection($true,$false)
    foreach ($sid in @($owner,$system)) {
        $security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,[Security.AccessControl.FileSystemRights]::FullControl,[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow))
    }
    Set-Acl -LiteralPath $directory -AclObject $security
    $acl = Get-Acl -LiteralPath $directory
    $other = @($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | Where-Object { $_.IdentityReference.Value -notin @($owner.Value,$system.Value) })
    if (-not $acl.AreAccessRulesProtected -or $other.Count -ne 0) { throw 'Private ACL verification failed' }
    Write-Host 'Use S3 Access Key ID / Secret Access Key pairs, not Cloudflare bearer tokens.'
    $account = Read-Host 'R2 Account ID (non-secret 32-character ID)'
    if ($account -notmatch '^[a-f0-9]{32}$') { throw 'Invalid account ID' }
    $readId = Read-Host 'queue-atlas-render-readonly: S3 Access Key ID (hidden)' -AsSecureString
    $readSecret = Read-Host 'queue-atlas-render-readonly: S3 Secret Access Key (hidden)' -AsSecureString
    $publishId = Read-Host 'queue-atlas-local-publisher: S3 Access Key ID (hidden)' -AsSecureString
    $publishSecret = Read-Host 'queue-atlas-local-publisher: S3 Secret Access Key (hidden)' -AsSecureString
    foreach ($value in @($readId,$readSecret,$publishId,$publishSecret)) {
        if ($value.Length -lt 8 -or $value.Length -gt 256) { throw 'Invalid credential length' }
    }
    [ordered]@{ Version=1; AccountId=$account; Bucket='queue-atlas-history'; ReadAccessKeyId=$readId; ReadSecretAccessKey=$readSecret; PublishAccessKeyId=$publishId; PublishSecretAccessKey=$publishSecret } | Export-Clixml -LiteralPath $file -NoClobber -Depth 3
    Write-Host 'Encrypted credentials saved outside OneDrive and Git. No network request was made.'
    Write-Host 'Tell Codex that secure setup is complete; do not send any credential values.'
} catch {
    Write-Error 'Secure setup did not complete. No credential values will be displayed. Existing files are never overwritten.' -ErrorAction Continue
    exit 1
} finally {
    foreach ($value in @($readId,$readSecret,$publishId,$publishSecret)) { if ($null -ne $value) { $value.Dispose() } }
}
