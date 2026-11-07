<#
Adds or removes the inbound firewall rule for the receiver.
The rule opens one TCP port to the home subnet only. The network stays Public,
so other apps' firewall rules and Network Discovery are unchanged. The rule also
covers Private, so it keeps working if the network is switched later.

Run from an Administrator PowerShell, in the project folder:
    powershell -ExecutionPolicy Bypass -File scripts\install_firewall.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install_firewall.ps1 -Uninstall

-DryRun needs no admin rights. It shows what would happen and changes nothing.
#>
param(
    [int]$Port = 8765,
    [string]$Subnet = "10.0.0.0/24",
    [switch]$Uninstall,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ruleName = "Balcony Station Receiver"

if ($DryRun) {
    if ($Uninstall) {
        Write-Output "Would remove firewall rule '$ruleName'."
    } else {
        Write-Output "Would create firewall rule '$ruleName': inbound TCP $Port, Public and Private profiles, from $Subnet only, Allow."
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
        -LocalPort $Port -Profile Public, Private -RemoteAddress $Subnet -Action Allow | Out-Null
    Write-Output "Created rule '$ruleName' for TCP $Port, from $Subnet only."
}
