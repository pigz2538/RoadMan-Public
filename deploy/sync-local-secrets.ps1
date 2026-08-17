param(
    [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
)

$ErrorActionPreference = 'Stop'
$envPath = Join-Path $Root '.env'
$entries = [ordered]@{
    VITE_AMAP_JSAPI_KEY = (Join-Path $Root 'Skills\amap-jsapi\apikey.txt')
    VITE_AMAP_SECURITY_JS_CODE = (Join-Path $Root 'Skills\amap-jsapi\secretkey.txt')
    AMAP_WEBSERVICE_KEY = (Join-Path $Root 'Skills\amap-lbs\apipkey.txt')
    OPENTRIPMAP_API_KEY = (Join-Path $Root 'Skills\opentripmap\apikey.txt')
}

$lines = if (Test-Path -LiteralPath $envPath) {
    @(Get-Content -LiteralPath $envPath -Encoding UTF8)
} else {
    @('# Local secrets are ignored by git.')
}

foreach ($name in $entries.Keys) {
    $secretPath = $entries[$name]
    if (-not (Test-Path -LiteralPath $secretPath)) { continue }
    $value = (Get-Content -LiteralPath $secretPath -Raw -Encoding UTF8).Trim()
    if ([string]::IsNullOrWhiteSpace($value)) { continue }
    $escapedName = [regex]::Escape($name)
    $replacement = "$name=$value"
    $found = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match "^$escapedName=") {
            $lines[$index] = $replacement
            $found = $true
        }
    }
    if (-not $found) { $lines += $replacement }
}

Set-Content -LiteralPath $envPath -Value $lines -Encoding UTF8
Write-Output 'Local provider keys synchronized into ignored .env (values omitted).'
