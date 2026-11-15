#Requires -Version 5.1
#Requires -RunAsAdministrator
# Machine Windows JETABLE : installation, requêtes réelles, sauvegarde, relance et désinstallation.
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Fixtures, [string]$Source = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$tests = $PSScriptRoot
$program = Join-Path $env:ProgramFiles 'loco'
$root = Join-Path $env:ProgramData 'loco'
if (Test-Path "$root\etc\loco.json") { throw 'Test réservé à une machine sans installation existante.' }
$env:LOCO_E2E_PASSWORD = 'Native-E2e-' + [guid]::NewGuid().ToString()
$password = ConvertTo-SecureString $env:LOCO_E2E_PASSWORD -AsPlainText -Force
function Install {
    & "$Source\windows\install.ps1" -HostAddress 127.0.0.1 -Password $password
    if ((Get-Service loco).Status -ne 'Running') { throw 'Service non démarré.' }
}
function Python {
    & "$program\python\python.exe" @args
    if ($LASTEXITCODE -ne 0) { throw "Commande Python en échec : $args" }
}
function Ready {
    for ($i = 0; $i -lt 45; $i++) {
        & curl.exe -fsk --max-time 3 https://127.0.0.1:8443/healthz 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep 1
    }
    throw 'Portail indisponible.'
}
Install
$env:LOCO_CONFIG = "$root\etc\loco.json"
Python -m unittest discover -s $tests -p test_windows_runtime.py -v
Python "$tests\https_redirect.py" https://127.0.0.1:8443
Python "$tests\windows_probe.py" $Fixtures
Python "$tests\windows_agent_probe.py"
$ca = (Get-FileHash "$root\etc\tls\ca.crt").Hash
$secret = (Get-FileHash "$root\etc\portal.secret").Hash
$configAcl = Get-Acl "$root\etc\loco.json"
$svcRules = @($configAcl.Access | Where-Object { $_.IdentityReference -like '*\loco' })
if (-not $svcRules.Count -or @($svcRules | Where-Object { $_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write }).Count) {
    throw 'La configuration de l''installateur doit rester en lecture seule pour le service.'
}
Python -m locowin backup create
$archive = (Get-ChildItem "$root\backups\*.tar.gz" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
$package = Get-ChildItem "$root\data\apt\repositories\debian\native-xz\pool\*.deb" | Select-Object -First 1
$digest = (Get-FileHash $package.FullName).Hash
Remove-Item $package.FullName
& "$program\loco.cmd" backup restore $archive
if ($LASTEXITCODE -ne 0) { throw 'Restauration en échec.' }
Ready
if ((Get-FileHash $package.FullName).Hash -ne $digest) { throw 'Paquet non restauré.' }
Install
if ((Get-FileHash "$root\etc\tls\ca.crt").Hash -ne $ca -or (Get-FileHash "$root\etc\portal.secret").Hash -ne $secret) {
    throw 'Une relance a changé les clés.'
}
& "$program\loco.cmd" restart
if ($LASTEXITCODE -ne 0) { throw 'Redémarrage en échec.' }
Ready
& "$Source\windows\remove.ps1" -Yes
if (Get-Service loco -ErrorAction SilentlyContinue) { throw 'Service non désinstallé.' }
if (-not (Test-Path $package.FullName)) { throw 'La désinstallation simple a effacé les données.' }
Install
if ((Get-FileHash "$root\etc\tls\ca.crt").Hash -ne $ca) { throw 'La réinstallation a changé la CA.' }
& "$Source\windows\remove.ps1" -Purge -Yes
if ((Test-Path $root) -or (Test-Path $program)) { throw 'Purge incomplète.' }
if (Get-NetFirewallRule -Group loco -ErrorAction SilentlyContinue) { throw 'Règles pare-feu restantes.' }
Remove-Item Env:\LOCO_E2E_PASSWORD
Write-Host 'Windows natif : installation, portail, APT/RPM, cache, agent du parc, sauvegarde, mise à jour, retrait et purge OK.'
