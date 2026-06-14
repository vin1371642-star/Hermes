param([Parameter(Mandatory)][ValidateSet("marketing","code","work","personal","cron")][string]$Profile)

$hermesHome = "$env:LOCALAPPDATA\hermes"
$configPath = "$hermesHome\config.yaml"
$patchPath  = "$hermesHome\profiles\$Profile.yaml"
$hermesDir  = "C:\Hermes"

Write-Host "Switching to profile: $Profile" -ForegroundColor Cyan

$script = @"
import sys
sys.path.insert(0, r'$hermesDir')
from ruamel.yaml import YAML
yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 120

with open(r'$configPath', 'r', encoding='utf-8') as f:
    cfg = yaml.load(f)

with open(r'$patchPath', 'r', encoding='utf-8') as f:
    patch = yaml.load(f)

def deep_merge(base, override):
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        else:
            base[k] = v

deep_merge(cfg, patch)

with open(r'$configPath', 'w', encoding='utf-8') as f:
    yaml.dump(cfg, f)

active = cfg.get('model', {}).get('model', '?')
print(f'Profile applied. Active model: {active}')
"@

& "$hermesDir\.venv\Scripts\python.exe" -c $script
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: failed to patch config" -ForegroundColor Red; exit 1 }

Write-Host "Restarting Hermes gateway..." -ForegroundColor Yellow
& "$hermesDir\.venv\Scripts\python.exe" -m hermes_cli.main gateway stop 2>&1 | Out-Null
Start-Sleep -Seconds 2
& "$hermesDir\.venv\Scripts\python.exe" -m hermes_cli.main gateway start 2>&1 | Out-Null
Start-Sleep -Seconds 6

Write-Host "Profile '$Profile' active. Gateway restarted." -ForegroundColor Green
