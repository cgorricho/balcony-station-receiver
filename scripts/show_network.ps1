<#
Prints what is needed for the firewall rule and the router's DHCP reservation.
It only reads settings and needs no admin rights.

    powershell -ExecutionPolicy Bypass -File scripts\show_network.ps1
#>
param([int]$Port = 8765)

$ErrorActionPreference = "Stop"

# Use the physical adapter that carries the default route, WiFi or wired.
$physical = Get-NetAdapter -Physical | Where-Object { $_.Status -eq "Up" }
$route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" |
    Where-Object { $physical.ifIndex -contains $_.ifIndex } |
    Sort-Object RouteMetric |
    Select-Object -First 1
if (-not $route) {
    Write-Error "No connected network adapter found."
}

$adapter = $physical | Where-Object { $_.ifIndex -eq $route.ifIndex }
$ip = (Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 |
    Select-Object -First 1).IPAddress
$network = Get-NetConnectionProfile -InterfaceIndex $adapter.ifIndex
$isWifi = $adapter.PhysicalMediaType -match "802\.11"

Write-Output "Adapter:         $($adapter.Name) ($(if ($isWifi) { 'WiFi' } else { 'wired' }))"
Write-Output "Network:         $($network.Name)"
Write-Output "Network profile: $($network.NetworkCategory)"
Write-Output "MAC address:     $($adapter.MacAddress)"
Write-Output "IPv4 address:    $ip"
Write-Output ""
if ($ip -notlike "10.0.0.*") {
    Write-Output "WARNING: this address is outside the firewall rule's default subnet, 10.0.0.0/24."
    Write-Output "Run install_firewall.ps1 with -Subnet set to this network's subnet."
    Write-Output ""
}
if (-not $isWifi) {
    Write-Output "NOTE: the laptop is on a wired connection. The IP reservation belongs to this"
    Write-Output "adapter, so run this script again if you switch to WiFi."
    Write-Output ""
}
Write-Output "DHCP reservation (on your router's admin page):"
Write-Output "  1. Open the DHCP or address reservation section."
Write-Output "  2. Reserve $ip for MAC address $($adapter.MacAddress)."
Write-Output "  3. Save, then reconnect the laptop to the network."
Write-Output ""
Write-Output "Smoke test from a phone on the same WiFi:"
Write-Output "  http://${ip}:$Port/api/v1/health"
