"""Scripts prêts à l'emploi semés une fois dans la bibliothèque. L'administrateur peut ensuite les modifier ou les
supprimer : chacun n'est inséré qu'au premier démarrage qui le connaît (drapeau dans settings), jamais ré-imposé."""

# Installe/maj le dernier pilote NVIDIA (GeForce Game Ready, DCH) via l'API publique de NVIDIA, puis en silencieux.
# Écrit sans accent dans les sorties pour rester lisible quelle que soit la page de code du poste.
NVIDIA_WINDOWS = r'''# loco - Pilote NVIDIA : installe ou met a jour le dernier Game Ready (DCH) sur ce poste.
# Detecte le GPU, interroge l'API NVIDIA, compare a la version installee, telecharge et installe en silencieux.
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$gpu = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
       Where-Object { $_.Name -match 'NVIDIA' } | Select-Object -First 1
if (-not $gpu) { Write-Output 'Aucun GPU NVIDIA sur ce poste : rien a faire.'; exit 0 }
Write-Output "GPU : $($gpu.Name)"

# Version installee au format NVIDIA (ex. 31.0.15.5186 -> 551.86)
$installed = '0.0'
if ($gpu.DriverVersion -match '(\d+)\.(\d+)\.(\d+)\.(\d+)$') {
    $d = ($Matches[3] + $Matches[4]) -replace '\D', ''
    if ($d.Length -ge 5) { $d = $d.Substring($d.Length - 5); $installed = $d.Substring(0, 3) + '.' + $d.Substring(3, 2) }
}
Write-Output "Pilote installe : $installed"

$notebook = [bool](Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue)
$osId = if ([Environment]::OSVersion.Version.Build -ge 22000) { 135 } else { 57 }   # 135 = Windows 11, 57 = Windows 10 64-bit
$model = ($gpu.Name -replace '.*NVIDIA ', '').Trim()

function Lookup($t, $p) {
    $u = "https://www.nvidia.com/Download/API/lookupValueSearch.aspx?TypeID=$t&ParentID=$p"
    (Invoke-RestMethod -Uri $u -UseBasicParsing -TimeoutSec 60).LookupValueSearch.LookupValues.LookupValue
}

$gf = Lookup 1 0 | Where-Object { $_.Name -match 'GeForce' } | Select-Object -First 1
if (-not $gf) { Write-Output 'API NVIDIA : type GeForce introuvable.'; exit 1 }

$psid = $null; $pfid = $null; $fbSid = $null; $fbFid = $null
foreach ($s in (Lookup 2 $gf.Value)) {
    if (($s.Name -match 'Notebook') -ne $notebook) { continue }
    $prods = Lookup 3 $s.Value
    if (-not $fbSid -and $prods) { $fbSid = $s.Value; $fbFid = ($prods | Select-Object -First 1).Value }
    foreach ($pr in $prods) {
        $pn = ($pr.Name -replace 'NVIDIA ', '').Trim()
        if ($model -like "*$pn*" -or $pn -like "*$model*") { $psid = $s.Value; $pfid = $pr.Value; break }
    }
    if ($pfid) { break }
}
if (-not $pfid) { $psid = $fbSid; $pfid = $fbFid }   # GPU non identifie finement : le paquet bureau/portable NVIDIA est universel
if (-not $pfid) { Write-Output 'API NVIDIA : serie ou produit introuvable.'; exit 1 }

$api = "https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/services/AjaxDriverService.php" +
       "?func=DriverManualLookup&psid=$psid&pfid=$pfid&osID=$osId&languageCode=1033&isWHQL=1&dch=1&sort1=0&numberOfResults=1"
$r = Invoke-RestMethod -Uri $api -UseBasicParsing -TimeoutSec 60
if (-not $r.IDS) { Write-Output 'API NVIDIA : aucun pilote DCH pour ce GPU et cette version de Windows.'; exit 1 }
$latest = [string]$r.IDS[0].downloadInfo.Version
$dl = [string]$r.IDS[0].downloadInfo.DownloadURL
Write-Output "Dernier pilote NVIDIA : $latest"

if ([version]$installed -ge [version]$latest) { Write-Output 'Deja a jour.'; exit 0 }

$tmp = Join-Path $env:TEMP "nvidia-$latest.exe"
Write-Output "Telechargement : $dl"
Invoke-WebRequest -Uri $dl -OutFile $tmp -UseBasicParsing -TimeoutSec 1800
Write-Output 'Installation silencieuse (-s -noreboot -clean)...'
$proc = Start-Process -FilePath $tmp -ArgumentList '-s -noreboot -clean' -Wait -PassThru
Remove-Item $tmp -Force -ErrorAction SilentlyContinue
if ($proc.ExitCode -le 1) { Write-Output "Pilote NVIDIA $latest installe (code $($proc.ExitCode))." }
else { Write-Output "Installeur NVIDIA : echec, code $($proc.ExitCode)."; exit $proc.ExitCode }
'''

BUILTINS = [
    {
        "flag": "nvidia_driver",
        "name": "Pilote NVIDIA — dernière version",
        "platform": "windows",
        "description": "Installe ou met à jour le dernier pilote NVIDIA Game Ready (DCH) du poste, directement depuis NVIDIA.",
        "content": NVIDIA_WINDOWS,
    },
]


def seed(conn, now):
    """Insère les scripts intégrés absents, une seule fois chacun (drapeau en base) : une suppression par l'admin tient."""
    for item in BUILTINS:
        key = "seed_script_" + item["flag"]
        if conn.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone():
            continue
        conn.execute("INSERT INTO scripts (name, description, platform, content, created_at, updated_at, updated_by) "
                     "VALUES (?,?,?,?,?,?,'loco')",
                     (item["name"], item["description"], item["platform"], item["content"], now, now))
        conn.execute("INSERT INTO settings (key, value) VALUES (?, '1')", (key,))
