# loco : agent du parc (loco-client {{ version }}) pour Windows 10/11 et Windows Server 2016 et plus.
#   irm '{{ server }}/agent/windows.ps1?key=...' | iex      (PowerShell administrateur)
# Telecharge l'installeur loco-client.msi depuis loco, verifie son empreinte SHA-256, puis l'installe :
# service Windows "loco-client" (SYSTEM), visible dans Programmes et fonctionnalites. Autorite HTTPS de loco
# epinglee par son empreinte, sans modifier les certificats du poste. Retrait : depuis le portail.
# Deploiement en masse : le MSI par GPO, avec les valeurs Server, Key et CaSha256 dans
# HKLM\SOFTWARE\Policies\loco\Client (voir Mon parc).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# Langue du poste : francais si Windows est en francais, espagnol si en espagnol, sinon anglais.
$LocoLang = (Get-UICulture).TwoLetterISOLanguageName
function T($fr, $en, $es) { switch ($LocoLang) { 'fr' { $fr } 'es' { $es } default { $en } } }
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning {{ ps_t("Relancez PowerShell en tant qu'administrateur.") }}
    return
}
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$msi = Join-Path $env:TEMP 'loco-client.msi'
Invoke-WebRequest -UseBasicParsing -Uri '{{ msi_url }}' -OutFile $msi
if ((Get-FileHash $msi -Algorithm SHA256).Hash -ne '{{ msi_sha256 }}') {
    Remove-Item $msi -Force
    throw {{ ps_t('Empreinte SHA-256 du MSI incorrecte : installation annulee.') }}
}
$log = Join-Path $env:TEMP 'loco-client-install.log'
$arguments = '/i "{0}" SERVER="{{ server }}" KEY="{{ key }}" CA_SHA256="{{ ca_sha256 }}" /qn /norestart /l*v "{1}"' -f $msi, $log
$p = Start-Process msiexec.exe -ArgumentList $arguments -Wait -PassThru
Remove-Item $msi -Force -ErrorAction SilentlyContinue
if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010) { throw {{ ps_t('Installation du MSI en echec (code %(code)s, journal %(log)s).', code='$($p.ExitCode)', log='$log') }} }
$service = Get-Service loco-client -ErrorAction SilentlyContinue
if (-not $service -or $service.Status -ne 'Running') { throw {{ ps_t('Le service loco-client ne tourne pas.') }} }
Write-Host {{ ps_t('Agent loco installe (service loco-client) : le poste apparait dans Mon parc sous deux minutes (%(url)s/parc).', url=server) }} -ForegroundColor Green
