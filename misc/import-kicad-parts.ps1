# --- Parameters (must be first) --------------------------------------------
param(
    [string]$RepoRoot     = "C:\Users\developer\Documents\GitHub\Hardware",
    [string]$Downloads    = "C:\Users\developer\Documents\GitHub\Hardware\downloads",
    [string]$Libs         = "C:\Users\developer\Documents\GitHub\Hardware\libs",
    [string]$SymbolLib    = "C:\Users\developer\Documents\GitHub\Hardware\libs\MySymbols.kicad_sym",
    [string]$FootprintLib = "C:\Users\developer\Documents\GitHub\Hardware\libs\MyFootprints.pretty",
    [string]$ModelLib     = "C:\Users\developer\Documents\GitHub\Hardware\libs\My3DModels",
    [string]$MiscDir      = "C:\Users\developer\Documents\GitHub\Hardware\misc",
    [string]$PythonExe    = "python"
)

# --- Optional: self-elevate if not admin -----------------------------------
# Comment this block out if you run via Scheduled Task (already elevated).
$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    # Prefer pwsh.exe (PowerShell 7), else fall back to Windows PowerShell
    $cmdPwsh = Get-Command pwsh.exe -ErrorAction SilentlyContinue
    if ($cmdPwsh) {
        $elevateExe = $cmdPwsh.Source
    } else {
        $elevateExe = (Get-Command powershell.exe -ErrorAction Stop).Source
    }

    Start-Process -FilePath $elevateExe `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -NoExit -File `"$PSCommandPath`"" `
        -Verb RunAs
    exit
}

# --- Logging / error behavior ----------------------------------------------
$Log = Join-Path $RepoRoot "tools\importer.log"
$ErrorActionPreference = 'Stop'
Start-Transcript -Path $Log -Append | Out-Null

# --- Helpers ---------------------------------------------------------------

function Initialize-Folders {
    foreach ($p in @($RepoRoot, $Downloads, $Libs, $FootprintLib, $ModelLib, $MiscDir)) {
        if (-not (Test-Path -LiteralPath $p)) { New-Item -ItemType Directory -Force -Path $p | Out-Null }
    }
    # Ensure symbol lib exists (header created by Python helper if not)
    if (-not (Test-Path -LiteralPath $SymbolLib)) {
        Set-Content -Path $SymbolLib -Value "(kicad_symbol_lib (version 20211014) (generator ""import-kicad-parts.ps1""))`n)`n" -Encoding UTF8
    }
}

function Wait-FileReady {
    param([string]$Path, [int]$Tries = 20, [int]$DelayMs = 500)
    $prevSize = -1
    for ($i=0; $i -lt $Tries; $i++) {
        if (Test-Path -LiteralPath $Path) {
            try {
                $fi = Get-Item -LiteralPath $Path
                if ($fi.Length -eq $prevSize) { return $true }
                $prevSize = $fi.Length
            } catch { }
        }
        Start-Sleep -Milliseconds $DelayMs
    }
    return (Test-Path -LiteralPath $Path)
}

function Expand-ZipToFolder {
    param([string]$ZipPath)
    $base   = [System.IO.Path]::GetFileNameWithoutExtension($ZipPath)
    $target = Join-Path $Downloads $base
    if (-not (Test-Path -LiteralPath $target)) { New-Item -ItemType Directory -Path $target | Out-Null }
    try {
        Expand-Archive -Path $ZipPath -DestinationPath $target -Force
    } catch {
        Write-Warning ("Failed to expand {0}: {1}" -f $ZipPath, $_)
        return $null
    }
    return $target
}

function Merge-Symbols {
    param([string[]]$SourceSymPaths)
    if ($SourceSymPaths.Count -gt 0) {
        $mergeScript = Join-Path $RepoRoot "tools\merge_symbols.py"
        if (-not (Test-Path -LiteralPath $mergeScript)) {
            Write-Error "merge_symbols.py not found at $mergeScript"
            return
        }
        $pyArgs = @($mergeScript, $SymbolLib) + $SourceSymPaths
        & $PythonExe $pyArgs
    }
}

function Move-Files {
    param([string]$PartDir, [string]$PartName)

    # Robust file selection for Windows PowerShell 5: gather all files, then filter by extension
    $allFiles = Get-ChildItem -LiteralPath $PartDir -Recurse -File -ErrorAction SilentlyContinue

    $symFiles   = $allFiles | Where-Object { $_.Extension -eq ".kicad_sym" }
    $modFiles   = $allFiles | Where-Object { $_.Extension -eq ".kicad_mod" }
    $modelFiles = $allFiles | Where-Object { $_.Extension -in @(".step",".wrl") }

    # 1) Merge symbols (no renaming)
    if ($symFiles.Count -gt 0) {
        Write-Host ("  -> Merging symbols: {0}" -f $symFiles.Count)
        & ${function:Merge-Symbols} -SourceSymPaths ($symFiles | ForEach-Object { $_.FullName })
    }

    # 2) Footprints
    foreach ($m in $modFiles) {
        Write-Host ("  -> Moving footprint: {0}" -f $m.Name)
        Copy-Item -LiteralPath $m.FullName -Destination (Join-Path $FootprintLib $m.Name) -Force
    }

    # 3) 3D models
    foreach ($mdl in $modelFiles) {
        Write-Host ("  -> Moving 3D model: {0}" -f $mdl.Name)
        Copy-Item -LiteralPath $mdl.FullName -Destination (Join-Path $ModelLib $mdl.Name) -Force
    }

    # 4) Unknown / junk => Hardware\misc  (exclude already moved types)
    $allowedExt = @(".kicad_sym",".kicad_mod",".step",".wrl",".zip")
    $junkFiles  = $allFiles | Where-Object { $allowedExt -notcontains $_.Extension }
    foreach ($f in $junkFiles) {
        Write-Host ("  -> Moving misc: {0}" -f $f.Name)
        $dest = Join-Path $MiscDir $f.Name
        try { Move-Item -LiteralPath $f.FullName -Destination $dest -Force }
        catch { Write-Warning ("Failed to move misc file {0}: {1}" -f $f.FullName, $_) }
    }
}

function Remove-PartArtifacts {
    param([string]$ZipPath, [string]$PartDir)
    # Delete extracted folder
    if (Test-Path -LiteralPath $PartDir) {
        try { Remove-Item -LiteralPath $PartDir -Recurse -Force -ErrorAction Stop }
        catch { Write-Warning ("Failed to remove {0}: {1}" -f $PartDir, $_) }
    }
    # Delete zip
    if (Test-Path -LiteralPath $ZipPath) {
        try { Remove-Item -LiteralPath $ZipPath -Force -ErrorAction Stop }
        catch { Write-Warning ("Failed to remove {0}: {1}" -f $ZipPath, $_) }
    }
}

function Invoke-GitCommit {
    param([string]$Message)
    try {
        & git -C $RepoRoot add -A
        & git -C $RepoRoot commit -m $Message
        & git -C $RepoRoot push
        Write-Host "  -> Git committed and pushed"
    } catch {
        Write-Warning ("Git operation failed: {0}" -f $_)
    }
}

function Invoke-ZipProcessing {
    param([string]$ZipPath)

    $base = [System.IO.Path]::GetFileNameWithoutExtension($ZipPath)
    Write-Host ("Processing: {0}" -f $base)

    if (-not (Wait-FileReady -Path $ZipPath)) {
        Write-Warning ("Zip not ready: {0}" -f $ZipPath)
        return
    }

    $partDir = Expand-ZipToFolder -ZipPath $ZipPath
    if (-not $partDir) { return }

    & ${function:Move-Files} -PartDir $partDir -PartName $base
    & ${function:Remove-PartArtifacts} -ZipPath $ZipPath -PartDir $partDir
    & ${function:Invoke-GitCommit} -Message ("Add {0} from downloads" -f $base)
}

# --- Main ------------------------------------------------------------------

Initialize-Folders

Write-Host ("Importer watching: {0}" -f $Downloads)
$fsw = New-Object System.IO.FileSystemWatcher $Downloads
$fsw.Filter = "*.zip"
$fsw.IncludeSubdirectories = $false
$fsw.EnableRaisingEvents = $true

# Handle Created and Changed (some apps write ZIPs via temp -> rename)
$created = Register-ObjectEvent -InputObject $fsw -EventName Created -SourceIdentifier "ZipCreated" -Action {
    param($evSender, $evArgs)
    & ${function:Invoke-ZipProcessing} -ZipPath $evArgs.FullPath
} | Out-Null

$changed = Register-ObjectEvent -InputObject $fsw -EventName Changed -SourceIdentifier "ZipChanged" -Action {
    param($evSender, $evArgs)
    & ${function:Invoke-ZipProcessing} -ZipPath $evArgs.FullPath
} | Out-Null

# Optional: process any zip already in the folder at startup
Get-ChildItem -LiteralPath $Downloads -Filter *.zip -ErrorAction SilentlyContinue | ForEach-Object {
    & ${function:Invoke-ZipProcessing} -ZipPath $_.FullName
}

Write-Host "Press Ctrl+C to stop. (Use 'Get-EventSubscriber' / 'Unregister-Event' to clean up if needed)"
while ($true) { Start-Sleep -Seconds 1 }