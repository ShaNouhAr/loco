#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Installe loco nativement sous Windows (Windows 11, Windows Server 2022 et 2025), sans Linux.
    Relançable : met à jour le programme et applique les réglages ; les données sont conservées.

.DESCRIPTION
    Service Windows « loco » (compte de service virtuel NT SERVICE\loco) : nginx (portail HTTPS,
    caches APT, RPM, Flathub et Windows Update) et portail Python (waitress). Composants téléchargés depuis
    leurs sources officielles et vérifiés (SHA-256 de windows\components.json). Règles du pare-feu Windows
    limitées aux réseaux autorisés.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\windows\install.ps1
.EXAMPLE
    .\windows\install.ps1 -HostAddress 10.20.0.15 -Allow 10.20.0.0/16 -AdminAllow 10.20.99.0/24 -DataPath D:\loco
#>
[CmdletBinding()]
param(
    # Adresse annoncée aux postes (défaut : IPv4 de l'interface de la route par défaut).
    [string]$HostAddress,
    [int]$HttpsPort,
    [string]$PortalUrl,
    # Port HTTP d'amorçage des postes ; -NoHttp le désactive.
    [int]$HttpPort,
    [switch]$NoHttp,
    # Réseaux autorisés pour les postes (défaut : réseaux privés) et pour l'interface (défaut : comme -Allow).
    [string[]]$Allow,
    [string[]]$AdminAllow,
    # Proxy HTTP d'entreprise pour sortir sur Internet, ex. http://proxy.exemple.lan:3128 ; -NoProxy le retire.
    [string]$Proxy,
    [switch]$NoProxy,
    # Dossier des données et des caches (défaut : C:\ProgramData\loco\data).
    [string]$DataPath,
    [string]$BackupPath,
    [switch]$NoWU,
    [switch]$NoFlatpak,
    [switch]$NoRpm,
    [string]$WuCacheSize,
    [string]$AptCacheSize,
    [string]$RpmCacheSize,
    [string]$FlatpakCacheSize,
    # DNS publics du cache Windows Update (le DNS du réseau renvoie ce serveur pour ces domaines).
    [string[]]$Resolvers,
    [string]$MaxUpload,
    [int]$BackupKeep = -1,
    # Mot de passe du compte « admin » (sinon un mot de passe temporaire est généré à la première installation).
    [SecureString]$Password
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest

$Source = Split-Path -Parent $PSScriptRoot
$Program = Join-Path $env:ProgramFiles 'loco'
$Root = Join-Path $env:ProgramData 'loco'
$Etc = Join-Path $Root 'etc'
$ConfigPath = Join-Path $Etc 'loco.json'
$ServiceName = 'loco'
$ServiceAccount = "NT SERVICE\$ServiceName"
$PrivateNets = @('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7', 'fe80::/10')

function Say([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }
function Fail([string]$Text) { Write-Host "Erreur : $Text" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- vérifications
$os = Get-CimInstance Win32_OperatingSystem
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { Fail 'Windows 64 bits (x64) requis.' }
$build = [int]$os.BuildNumber
if ($os.ProductType -eq 1 -and $build -lt 22000) { Fail 'Windows 11 requis (ou Windows Server 2022 / 2025).' }
if ($os.ProductType -ne 1 -and $build -lt 20348) { Fail 'Windows Server 2022 ou 2025 requis.' }
foreach ($f in 'portal\locowin.py', 'conf\nginx-portal.conf', 'conf\nginx-apt-proxy.conf', 'windows\components.json', 'windows\loco.xml') {
    if (-not (Test-Path (Join-Path $Source $f))) { Fail "projet incomplet : $f introuvable dans $Source." }
}
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
. (Join-Path $PSScriptRoot 'migrate.ps1')
Move-LegacyInstallation

# ---------------------------------------------------------------- réglages
# Valeurs par défaut, puis celles de l'installation précédente, puis les paramètres.
$cfg = [ordered]@{
    host = ''; portal_url = ''; https_port = 8443; http_port = 8088; allow = $PrivateNets; admin_allow = @(); proxy = ''
    wu = $true; flatpak = $true; rpm = $true; wu_cache_size = ''; apt_cache_size = ''; rpm_cache_size = ''
    flatpak_cache_size = ''; resolvers = @('1.1.1.1', '9.9.9.9'); max_upload = '8g'; backup_keep = 3
    data_dir = (Join-Path $Root 'data'); backup_dir = (Join-Path $Root 'backups')
}
if (Test-Path $ConfigPath) {
    $previous = Get-Content -Raw -Encoding UTF8 $ConfigPath | ConvertFrom-Json
    foreach ($p in $previous.PSObject.Properties) { if ($cfg.Contains($p.Name)) { $cfg[$p.Name] = $p.Value } }
}
if ($PSBoundParameters.ContainsKey('HostAddress')) { $cfg.host = $HostAddress }
if ($PSBoundParameters.ContainsKey('HttpsPort')) { $cfg.https_port = $HttpsPort }
if ($PSBoundParameters.ContainsKey('PortalUrl')) { $cfg.portal_url = $PortalUrl.TrimEnd('/') }
if ($cfg.portal_url -and $cfg.portal_url -notmatch '^https://([A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(:[0-9]{1,5})?$') { Fail 'PortalUrl : adresse https://nom:port attendue.' }
if ($PSBoundParameters.ContainsKey('HttpPort')) { $cfg.http_port = $HttpPort }
if ($NoHttp) { $cfg.http_port = 0 }
if ($PSBoundParameters.ContainsKey('Allow')) { $cfg.allow = $Allow }
if ($PSBoundParameters.ContainsKey('AdminAllow')) { $cfg.admin_allow = $AdminAllow }
if ($PSBoundParameters.ContainsKey('Proxy')) { $cfg.proxy = $Proxy }
if ($NoProxy) { $cfg.proxy = '' }
if ($PSBoundParameters.ContainsKey('DataPath')) { $cfg.data_dir = $DataPath }
if ($PSBoundParameters.ContainsKey('BackupPath')) { $cfg.backup_dir = $BackupPath }
if ($PSBoundParameters.ContainsKey('NoWU')) { $cfg.wu = -not $NoWU }
if ($PSBoundParameters.ContainsKey('NoFlatpak')) { $cfg.flatpak = -not $NoFlatpak }
if ($PSBoundParameters.ContainsKey('NoRpm')) { $cfg.rpm = -not $NoRpm }
foreach ($n in 'WuCacheSize', 'AptCacheSize', 'RpmCacheSize', 'FlatpakCacheSize') {
    if ($PSBoundParameters.ContainsKey($n)) {
        $key = ($n -creplace '([a-z])([A-Z])', '$1_$2').ToLower()
        $cfg[$key] = (Get-Variable $n -ValueOnly)
    }
}
if ($PSBoundParameters.ContainsKey('Resolvers')) { $cfg.resolvers = $Resolvers }
if ($PSBoundParameters.ContainsKey('MaxUpload')) { $cfg.max_upload = $MaxUpload }
if ($BackupKeep -ge 0) { $cfg.backup_keep = $BackupKeep }

# Validation
foreach ($p in 'https_port', 'http_port') {
    if ([int]$cfg[$p] -lt 0 -or [int]$cfg[$p] -gt 65535) { Fail "port invalide : $($cfg[$p])." }
}
if ([int]$cfg.https_port -eq 0) { Fail 'le port HTTPS doit être supérieur à zéro.' }
$reservedPorts = @(3142, 8089)
if ($cfg.wu) { $reservedPorts += 80, 443 }
foreach ($p in 'https_port', 'http_port') {
    if ([int]$cfg[$p] -in $reservedPorts) { Fail "$p : port réservé au cache ou au portail interne." }
}
if ($cfg.http_port -and $cfg.http_port -eq $cfg.https_port) { Fail 'les ports HTTP et HTTPS doivent être différents.' }
if ($cfg.proxy -and $cfg.proxy -notmatch '^http://[^\s"''<>]+$') { Fail 'proxy : adresse http://hôte:port attendue.' }
foreach ($size in 'wu_cache_size', 'apt_cache_size', 'rpm_cache_size', 'flatpak_cache_size', 'max_upload') {
    if ($cfg[$size] -and $cfg[$size] -notmatch '^[0-9]+[kKmMgGtT]?$') { Fail "$size : taille invalide (ex. 50g)." }
}
foreach ($r in $cfg.resolvers) {
    $ip = $null
    if (-not [Net.IPAddress]::TryParse($r, [ref]$ip)) { Fail "résolveur invalide : $r." }
}
foreach ($list in 'allow', 'admin_allow') {
    foreach ($n in @($cfg[$list])) {
        $addr, $len = $n -split '/', 2
        $ip = $null
        if (-not [Net.IPAddress]::TryParse($addr, [ref]$ip) -or ($len -and $len -notmatch '^\d{1,3}$')) { Fail "réseau invalide : $n." }
    }
}
if (-not @($cfg.admin_allow).Count) { $cfg.admin_allow = @($cfg.allow) }
foreach ($d in 'data_dir', 'backup_dir') {
    $full = [IO.Path]::GetFullPath($cfg[$d])
    if ($full -notmatch '^[A-Za-z]:\\[^\s"'';{}]+$') { Fail "$d : dossier local sans espace attendu ($full) ; pas de racine de disque ni de chemin réseau." }
    $cfg[$d] = $full.TrimEnd('\')
}
foreach ($d in 'data_dir', 'backup_dir') {
    $path = $cfg[$d].TrimEnd('\') + '\'
    $reserved = @($Root, $Program, $env:WINDIR, $Etc, (Join-Path $Root 'downloads'),
                  (Join-Path $Root 'state'), (Join-Path $Root 'nginx'), (Join-Path $Root 'logs'))
    foreach ($protected in $reserved) {
        $base = [IO.Path]::GetFullPath($protected).TrimEnd('\') + '\'
        if ($base.StartsWith($path, [StringComparison]::OrdinalIgnoreCase) -or
            ($protected -ne $Root -and $path.StartsWith($base, [StringComparison]::OrdinalIgnoreCase))) {
            Fail "$d : choisissez un dossier de données distinct des dossiers du programme et du système."
        }
    }
}
$dataPrefix = $cfg.data_dir.TrimEnd('\') + '\'
$backupPrefix = $cfg.backup_dir.TrimEnd('\') + '\'
if ($dataPrefix.StartsWith($backupPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    $backupPrefix.StartsWith($dataPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    Fail 'Les données et les sauvegardes doivent avoir des dossiers distincts, sans imbrication.'
}

if (-not $cfg.host) {
    $route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Sort-Object RouteMetric | Select-Object -First 1
    if ($route) {
        $cfg.host = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.ifIndex | Select-Object -First 1).IPAddress
    }
    if (-not $cfg.host) { Fail 'adresse du serveur introuvable : utilisez -HostAddress.' }
}
if ($cfg.host -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]*$') { Fail 'HostAddress : adresse IPv4 ou nom DNS attendu.' }
# DNS du système : utilisés par le cache APT pour joindre les dépôts (y compris internes).
$dns = @()
$route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Sort-Object RouteMetric | Select-Object -First 1
if ($route) { $dns = @((Get-DnsClientServerAddress -AddressFamily IPv4 -InterfaceIndex $route.ifIndex).ServerAddresses) }

# Tailles des caches : calculées une fois d'après l'espace libre du volume des données.
New-Item -ItemType Directory -Force -Path $cfg.data_dir | Out-Null
$freeG = [math]::Floor((Get-PSDrive -Name ([IO.Path]::GetPathRoot($cfg.data_dir).Substring(0, 1))).Free / 1GB)
function Default-Size([int]$Divisor, [int]$Max, [int]$Min) {
    return "$([math]::Max($Min, [math]::Min($Max, [math]::Floor($freeG / $Divisor))))g"
}
if (-not $cfg.wu_cache_size) { $cfg.wu_cache_size = Default-Size 2 200 5 }
foreach ($k in 'apt_cache_size', 'rpm_cache_size', 'flatpak_cache_size') { if (-not $cfg[$k]) { $cfg[$k] = Default-Size 4 100 2 } }
if ($cfg.wu -and $freeG -lt 10) { Write-Warning "seulement $freeG Go libres pour les données : le cache Windows Update risque de remplir le disque." }

# ---------------------------------------------------------------- ports
$service = Get-Service $ServiceName -ErrorAction SilentlyContinue
$ourNginx = Join-Path $Program 'nginx\nginx.exe'
$ports = @([int]$cfg.https_port, 3142)
if ($cfg.http_port) { $ports += [int]$cfg.http_port }
if ($cfg.wu) { $ports += 80, 443 }
Say 'Vérification des ports'
foreach ($port in $ports) {
    foreach ($l in @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)) {
        $proc = Get-Process -Id $l.OwningProcess -ErrorAction SilentlyContinue
        $path = if ($proc) { try { $proc.Path } catch { '' } } else { '' }
        if ($path -ne $ourNginx) {
            $who = if ($proc) { "$($proc.ProcessName) (PID $($proc.Id))" } else { "PID $($l.OwningProcess)" }
            if ($l.OwningProcess -eq 4) { $who = 'Windows (HTTP.sys : IIS, WinRM ou autre service web)' }
            Fail "le port $port est déjà utilisé par $who. Libérez-le, ou utilisez -NoWU pour les ports 80 et 443."
        }
    }
}

# ---------------------------------------------------------------- composants
$components = Get-Content -Raw -Encoding UTF8 (Join-Path $Source 'windows\components.json') | ConvertFrom-Json
$Downloads = Join-Path $Root 'downloads'
New-Item -ItemType Directory -Force -Path $Downloads | Out-Null

function Get-Component($Item) {
    $file = Join-Path $Downloads ([IO.Path]::GetFileName(([Uri]$Item.url).AbsolutePath))
    if (-not (Test-Path $file) -or (Get-FileHash $file -Algorithm SHA256).Hash -ne $Item.sha256.ToUpper()) {
        $params = @{ Uri = $Item.url; OutFile = "$file.part"; UseBasicParsing = $true }
        if ($cfg.proxy) { $params.Proxy = $cfg.proxy }
        Invoke-WebRequest @params
        Move-Item -Force "$file.part" $file
    }
    if ((Get-FileHash $file -Algorithm SHA256).Hash -ne $Item.sha256.ToUpper()) {
        Remove-Item -Force $file
        Fail "empreinte incorrecte pour $($Item.url) : téléchargement refusé."
    }
    return $file
}

Say 'Téléchargement et vérification des composants'
$pythonZip = Get-Component $components.python
$nginxZip = Get-Component $components.nginx
$winsw = Get-Component $components.winsw
$wheels = @($components.wheels | ForEach-Object { Get-Component $_ })

# ---------------------------------------------------------------- arrêt pour mise à jour
if ($service -and $service.Status -ne 'Stopped') {
    Say 'Arrêt du service pour la mise à jour'
    Stop-Service $ServiceName -Force
    (Get-Service $ServiceName).WaitForStatus('Stopped', '00:01:00')
}

# ---------------------------------------------------------------- programme
Say "Installation du programme dans $Program"
New-Item -ItemType Directory -Force -Path $Program | Out-Null
$stage = Join-Path $Program '.new'
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Path $stage | Out-Null

Expand-Archive -Path $pythonZip -DestinationPath (Join-Path $stage 'python')
$site = Join-Path $stage 'python\Lib\site-packages'
New-Item -ItemType Directory -Force -Path $site | Out-Null
foreach ($w in $wheels) {
    $zip = Join-Path $env:TEMP ([IO.Path]::GetFileNameWithoutExtension($w) + '.zip')
    Copy-Item -Force $w $zip
    Expand-Archive -Path $zip -DestinationPath $site -Force
    Remove-Item -Force $zip
}
# Python embarqué : chemins fixés ici (PYTHONPATH est ignoré), sans site-packages de l'utilisateur.
$pth = Get-ChildItem (Join-Path $stage 'python') -Filter 'python3*._pth' | Select-Object -First 1
$zipName = (Get-ChildItem (Join-Path $stage 'python') -Filter 'python3*.zip' | Select-Object -First 1).Name
[IO.File]::WriteAllText($pth.FullName, "$zipName`r`n.`r`nLib\site-packages`r`n..\portal`r`n")

$nginxTmp = Join-Path $env:TEMP 'loco-nginx'
if (Test-Path $nginxTmp) { Remove-Item -Recurse -Force $nginxTmp }
Expand-Archive -Path $nginxZip -DestinationPath $nginxTmp
$nginxSrc = (Get-ChildItem $nginxTmp -Directory | Select-Object -First 1).FullName
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'nginx\conf') | Out-Null
Copy-Item (Join-Path $nginxSrc 'nginx.exe') (Join-Path $stage 'nginx')
Copy-Item (Join-Path $nginxSrc 'conf\mime.types') (Join-Path $stage 'nginx\conf')
Copy-Item (Join-Path $nginxSrc 'docs\LICENSE') (Join-Path $stage 'nginx\LICENSE') -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $nginxTmp

foreach ($d in 'portal', 'conf', 'windows') {
    Copy-Item -Recurse (Join-Path $Source $d) (Join-Path $stage $d)
}
Get-ChildItem (Join-Path $stage 'portal') -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'winsw') | Out-Null
Copy-Item $winsw (Join-Path $stage 'winsw\loco.exe')
$xml = (Get-Content -Raw -Encoding UTF8 (Join-Path $Source 'windows\loco.xml')).
    Replace('@PROGRAM@', $Program).Replace('@CONFIG@', $ConfigPath).Replace('@LOGS@', (Join-Path $Root 'logs'))
[IO.File]::WriteAllText((Join-Path $stage 'winsw\loco.xml'), $xml, (New-Object Text.UTF8Encoding($false)))
Copy-Item (Join-Path $Source 'windows\loco.cmd') (Join-Path $stage 'loco.cmd')
& (Join-Path $stage 'python\python.exe') -m compileall -q (Join-Path $stage 'portal') | Out-Null
if ($LASTEXITCODE -ne 0) { Fail 'le nouveau programme contient une erreur Python ; remplacement interrompu.' }

# Remplacement du programme.
foreach ($d in 'python', 'nginx', 'portal', 'conf', 'windows', 'winsw') {
    $target = Join-Path $Program $d
    if (Test-Path $target) { Remove-Item -Recurse -Force $target }
    Move-Item (Join-Path $stage $d) $target
}
Move-Item -Force (Join-Path $stage 'loco.cmd') (Join-Path $Program 'loco.cmd')
Remove-Item -Recurse -Force $stage

# ---------------------------------------------------------------- service et droits
$exe = Join-Path $Program 'winsw\loco.exe'
if (-not $service) {
    Say 'Création du service Windows loco'
    & $exe install | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail 'création du service impossible.' }
}
& sc.exe config $ServiceName obj= $ServiceAccount | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "compte de service $ServiceAccount impossible à appliquer." }

function Protect-Directory([string]$Path, [switch]$ReadOnly) {
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $inherit = 'ContainerInherit,ObjectInherit'
    foreach ($sid in 'S-1-5-18', 'S-1-5-32-544') {
        $who = New-Object Security.Principal.SecurityIdentifier($sid)
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($who, 'FullControl', $inherit, 'None', 'Allow')))
    }
    $svc = New-Object Security.Principal.NTAccount($ServiceAccount)
    $rights = if ($ReadOnly) { 'ReadAndExecute' } else { 'Modify' }
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($svc, $rights, $inherit, 'None', 'Allow')))
    Set-Acl -LiteralPath $Path -AclObject $acl
}
function Protect-Installation {
    # La configuration relue par l'installateur administrateur reste en lecture seule pour le service.
    Protect-Directory $Root -ReadOnly
    Protect-Directory $Etc -ReadOnly
    foreach ($d in $cfg.data_dir, $cfg.backup_dir, (Join-Path $Root 'state'),
                   (Join-Path $Root 'logs'), (Join-Path $Root 'nginx'), (Join-Path $Etc 'tls')) {
        Protect-Directory $d
    }
}
Say 'Dossiers et droits'
Protect-Installation

$state = [ordered]@{
    host = $cfg.host; https_port = [int]$cfg.https_port; http_port = [int]$cfg.http_port; app_port = 8089; acng_port = 3142
    allow = @($cfg.allow); admin_allow = @($cfg.admin_allow); proxy = $cfg.proxy
    wu = [bool]$cfg.wu; flatpak = [bool]$cfg.flatpak; rpm = [bool]$cfg.rpm
    wu_cache_size = $cfg.wu_cache_size; apt_cache_size = $cfg.apt_cache_size; rpm_cache_size = $cfg.rpm_cache_size
    flatpak_cache_size = $cfg.flatpak_cache_size; resolvers = @($cfg.resolvers); dns = @($dns)
    max_upload = $cfg.max_upload; backup_keep = [int]$cfg.backup_keep
    program_dir = $Program; data_dir = $cfg.data_dir; backup_dir = $cfg.backup_dir
    state_dir = (Join-Path $Root 'state'); etc_dir = $Etc; log_dir = (Join-Path $Root 'logs'); nginx_dir = (Join-Path $Root 'nginx')
}
New-Item -ItemType Directory -Force -Path $Etc | Out-Null
[IO.File]::WriteAllText($ConfigPath, ($state | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding($false)))

# ---------------------------------------------------------------- configuration
Say 'Configuration (comptes, clé de signature, dépôts, certificats, nginx)'
$env:LOCO_CONFIG = $ConfigPath
if ($Password) {
    $env:LOCO_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password))
}
$python = Join-Path $Program 'python\python.exe'
$result = $null
$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
$consoleEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
& $python -m locowin configure 2>&1 | ForEach-Object {
    $line = "$_"
    if ($line.StartsWith('LOCALREPO-RESULT ')) { $result = $line.Substring(17) | ConvertFrom-Json } else { Write-Host "    $line" }
}
$code = $LASTEXITCODE
[Console]::OutputEncoding = $consoleEncoding
$ErrorActionPreference = 'Stop'
Remove-Item Env:\LOCO_PASSWORD -ErrorAction SilentlyContinue
if ($code -ne 0 -or -not $result) { Fail 'configuration interrompue (voir ci-dessus).' }
# Fichiers de configuration et d'état (petits) : droits du dossier réappliqués, au cas où.
# Le parent reste protégé pendant la remise à zéro des anciennes ACL des enfants.
Protect-Directory $Root -ReadOnly
& icacls.exe "$Root\*" /reset /T /C /Q | Out-Null
Protect-Installation

# ---------------------------------------------------------------- pare-feu
Say 'Pare-feu Windows'
Get-NetFirewallRule -Group 'loco' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
$nginxExe = Join-Path $Program 'nginx\nginx.exe'
function Add-Rule([string]$Name, [int[]]$Ports, [string[]]$Networks) {
    New-NetFirewallRule -Group 'loco' -DisplayName "loco : $Name" -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $Ports -RemoteAddress $Networks -Program $nginxExe -Profile Any | Out-Null
}
$clients = @($cfg.allow)
$admins = @(@($cfg.allow) + @($cfg.admin_allow) | Select-Object -Unique)
Add-Rule 'interface et postes (HTTPS)' @([int]$cfg.https_port) $admins
Add-Rule 'cache APT' @(3142) $clients
if ($cfg.http_port) { Add-Rule 'amorçage des postes (HTTP)' @([int]$cfg.http_port) $clients }
if ($cfg.wu) { Add-Rule 'cache Windows Update' @(80, 443) $clients }

# ---------------------------------------------------------------- démarrage
Say 'Démarrage du service'
Start-Service $ServiceName
$ok = $false
for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
    Start-Sleep -Seconds 1
    & curl.exe -fsk --max-time 3 "https://127.0.0.1:$($cfg.https_port)/healthz" 2>$null | Out-Null
    $ok = $LASTEXITCODE -eq 0
}
if (-not $ok) { Fail "le portail ne répond pas (journaux : $(Join-Path $Root 'logs'))." }

# ---------------------------------------------------------------- résumé
Write-Host ''
Say 'loco est installé'
Write-Host "    Interface          : $($result.admin_url)"
if ($result.issued) {
    Write-Host "    Compte initial     : admin / $($result.issued)   (à changer à la première connexion ; affiché une seule fois)"
}
Write-Host "    Accès perdu        : & '$Program\loco.cmd' user passwd admin"
Write-Host "    Autorité HTTPS     : $Etc\tls\ca.crt (à déployer sur les postes, par GPO par exemple)"
Write-Host "                         empreinte $($result.ca_fingerprint)"
Write-Host "    Postes Linux       : curl -fsSL $($result.admin_url)/linux/setup.sh | sudo sh"
if ($result.http_url) { Write-Host "                         sans l'autorité : curl -fsSL $($result.http_url)/linux/setup.sh | sudo sh -s -- --trust-ca" }
Write-Host "    Réseaux autorisés  : postes $(@($cfg.allow) -join ' ') | interface $(@($cfg.admin_allow) -join ' ')"
if ($cfg.proxy) { Write-Host "    Proxy sortant      : $($cfg.proxy)" }
if ($cfg.wu) { Write-Host "    Cache Windows Upd. : $($cfg.wu_cache_size) max, DNS à configurer depuis le portail" }
Write-Host "    Cache APT          : $($cfg.apt_cache_size) max (port 3142)"
if ($cfg.rpm) { Write-Host "    Famille Red Hat    : curl -fsSL $($result.admin_url)/rpm/setup.sh | sudo sh" }
if ($cfg.flatpak) { Write-Host "    Flatpak (Flathub)  : curl -fsSL $($result.admin_url)/flatpak/setup.sh | sudo sh" }
if ([int]$cfg.backup_keep -gt 0) { Write-Host "    Sauvegardes        : quotidiennes dans $($cfg.backup_dir) ($($cfg.backup_keep) conservées)" }
Write-Host "    Données            : $($cfg.data_dir)"
Write-Host "    Désinstaller       : powershell -ExecutionPolicy Bypass -File `"$Program\windows\remove.ps1`""
