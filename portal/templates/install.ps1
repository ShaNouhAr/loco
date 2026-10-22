# loco : installation depuis le catalogue {{ base }}
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
{% for s in missing %}
Write-Warning {{ ps_t('Absent du catalogue (ou pas encore pret) : %(name)s', name=s) }}
{% endfor %}
$apps = @(
{% for a in apps %}    @{ Name = {{ q(a.name) }}; Url = {{ q(base ~ '/win/files/' ~ a.slug ~ '/' ~ quote(a.filename)) }}; File = {{ q(a.filename) }}; Sha = {{ q(a.sha256) }}; Kind = {{ q(a.kind) }}; Args = {{ q(a.args) }} }{% if not loop.last %},{% endif %}
{% endfor %})

# Délai maximal par installeur (secondes) : un installeur qui attend un clic est arrêté.
$timeout = 1800
if ($env:LOCO_TIMEOUT) { $timeout = [int]$env:LOCO_TIMEOUT }

function Start-Installer($file, $arguments) {
    if ($arguments) { $p = Start-Process $file -ArgumentList $arguments -PassThru }
    else { $p = Start-Process $file -PassThru }
    $null = $p.Handle  # nécessaire pour lire le code de sortie ensuite
    if (-not $p.WaitForExit($timeout * 1000)) {
        try { $p.Kill() } catch {}
        throw {{ ps_t("pas termine apres %(timeout)s s : l'installeur attend sans doute une action (verifiez les arguments silencieux)", timeout='$timeout') }}
    }
    return $p
}

$dir = Join-Path $env:TEMP ('loco-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $dir | Out-Null
$failed = 0

foreach ($a in $apps) {
    Write-Host "==> $($a.Name)" -ForegroundColor Cyan
    try {
        $dest = Join-Path $dir $a.File
        Invoke-WebRequest -UseBasicParsing -Uri $a.Url -OutFile $dest
        if ((Get-FileHash $dest -Algorithm SHA256).Hash -ne $a.Sha) { throw {{ ps_t('somme SHA256 invalide') }} }
        $p = $null
        switch ($a.Kind) {
            'msi' { $p = Start-Installer msiexec.exe "/i `"$dest`" $($a.Args)" }
            'msu' { $p = Start-Installer wusa.exe "`"$dest`" $($a.Args)" }
            'ps1' { $p = Start-Installer powershell.exe "-NoProfile -ExecutionPolicy Bypass -File `"$dest`" $($a.Args)" }
            'exe' { $p = Start-Installer $dest $a.Args }
            default {
                $target = Join-Path $env:PUBLIC 'Downloads'
                Copy-Item $dest $target -Force
                Write-Host {{ ps_t('    Fichier copie dans %(path)s', path='$target') }} -ForegroundColor Green
            }
        }
        if ($p) {
            $code = $p.ExitCode
            if ($code -eq 3010 -or $code -eq 1641) { Write-Host {{ ps_t('    OK (redemarrage necessaire)') }} -ForegroundColor Yellow }
            elseif ($code -eq 2359302 -or $code -eq 1638) { Write-Host {{ ps_t('    Deja installe') }} -ForegroundColor Green }
            elseif ($code -eq -2145124329) { Write-Host {{ ps_t('    Non applicable a ce systeme') }} -ForegroundColor Yellow }
            elseif ($code -ne 0) { throw {{ ps_t('code de sortie %(code)s', code='$code') }} }
            else { Write-Host '    OK' -ForegroundColor Green }
        }
    } catch {
        Write-Warning "$($a.Name) : $_"
        $failed++
    }
}

Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
if ($failed) { Write-Warning {{ ps_t('%(n)s installation(s) en echec.', n='$failed') }} } else { Write-Host {{ ps_t('Termine.') }} -ForegroundColor Green }
