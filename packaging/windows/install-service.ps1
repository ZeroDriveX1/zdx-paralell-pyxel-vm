param([string]$Python = "python", [string]$ConfigRoot = "C:\ProgramData\ZeroDriveX")
$command = "$Python -m zdx_cli serve --port 8765 --coordinator-key `"$ConfigRoot\coordinator.pem`" --tls-ca `"$ConfigRoot\tls\ca.pem`" --tls-cert `"$ConfigRoot\tls\coordinator.pem`" --tls-key `"$ConfigRoot\tls\coordinator-key.pem`""
Write-Host "Create the service with an approved service wrapper using: $command"
Write-Host "This script intentionally does not install an unbundled third-party wrapper."
