[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version
)

$ErrorActionPreference = 'Stop'
$tag = "v$Version"
$ghInstallPath = Join-Path $env:ProgramFiles 'GitHub CLI'
if (Test-Path (Join-Path $ghInstallPath 'gh.exe')) {
    $env:Path = "$ghInstallPath;$env:Path"
}

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is required but was not found on PATH."
    }
}

Require-Command git
Require-Command gh
gh auth status | Out-Host

$config = Get-Content (Join-Path $PSScriptRoot '..\x50-simulator\config.yaml') -Raw
$versionPattern = '(?m)^version:\s*"' + [regex]::Escape($Version) + '"\s*$'
if ($config -notmatch $versionPattern) {
    throw "x50-simulator/config.yaml version does not equal $Version."
}
if (git status --porcelain) {
    throw 'Worktree is not clean. Commit or stash changes before releasing.'
}
if (git tag --list $tag) {
    throw "Tag $tag already exists."
}

Push-Location (Join-Path $PSScriptRoot '..\x50-simulator')
try {
    python -m unittest discover -s tests -v
    python -m compileall -q app tests
} finally {
    Pop-Location
}

git push origin main
git tag -a $tag -m "X50 Navigation Simulator $Version"
git push origin $tag
gh release create $tag --title "X50 Navigation Simulator $Version" --generate-notes

Write-Host "Published $tag" -ForegroundColor Green
