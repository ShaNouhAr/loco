# loco : redirige les domaines Windows Update vers le cache {{ host }}.
# A executer en administrateur sur le serveur DNS Active Directory.
#   .\loco-dns.ps1            -> cree les zones
#   .\loco-dns.ps1 -Remove    -> supprime les zones (retour arriere)
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
# Langue du poste : francais si Windows est en francais, espagnol si en espagnol, sinon anglais.
$LocoLang = (Get-UICulture).TwoLetterISOLanguageName
function T($fr, $en, $es) { switch ($LocoLang) { 'fr' { $fr } 'es' { $es } default { $en } } }
$ip = '{{ host }}'
$domains = @({% for d in domains %}'{{ d }}'{% if not loop.last %}, {% endif %}{% endfor %})

foreach ($d in $domains) {
    $zone = Get-DnsServerZone -Name $d -ErrorAction SilentlyContinue
    if ($Remove) {
        if ($zone) { Remove-DnsServerZone -Name $d -Force; Write-Host {{ ps_t('Zone supprimee : %(zone)s', zone='$d') }} }
        continue
    }
    if (-not $zone) { Add-DnsServerPrimaryZone -Name $d -ReplicationScope Forest }
    foreach ($name in '@', '*') {
        $existing = Get-DnsServerResourceRecord -ZoneName $d -Name $name -RRType A -ErrorAction SilentlyContinue
        if ($existing) { $existing | Remove-DnsServerResourceRecord -ZoneName $d -Force }
        Add-DnsServerResourceRecordA -ZoneName $d -Name $name -IPv4Address $ip
    }
    Write-Host "Zone $d -> $ip"
}
Clear-DnsServerCache -Force
