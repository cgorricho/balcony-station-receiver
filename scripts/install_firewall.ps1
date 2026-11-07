<#
Adds or removes the inbound firewall rule for the receiver.
The rule covers TCP on the Private profile, from the local subnet only.

Run from an Administrator PowerShell, in the project folder:
    powershell -ExecutionPolicy Bypass -File scripts\install_firewall.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install_firewall.ps1 -Uninstall

-DryRun needs no admin rights. It shows what would happen and changes nothing.
#>
param(
    [int]$Port = 8765,
    [switch]$Uninstall,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ruleName = "Balcony Station Receiver"

if ($DryRun) {
    if ($Uninstall) {
        Write-Output "Would remove firewall rule '$ruleName'."
    } else {
        Write-Output "Would create firewall rule '$ruleName': inbound TCP $Port, Private profile, LocalSubnet only, Allow."
    }
    exit 0
}

$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "This script needs an Administrator PowerShell."
}

$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    $existing | Remove-NetFirewallRule
    Write-Output "Removed the existing rule."
}

if (-not $Uninstall) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP `
        -LocalPort $Port -Profile Private -RemoteAddress LocalSubnet -Action Allow | Out-Null
    Write-Output "Created rule '$ruleName' for TCP $Port (Private profile, local subnet only)."
}
