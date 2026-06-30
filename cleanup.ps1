# cleanup.ps1
# Ejecutar UNA VEZ para eliminar los artefactos de Phase 1 y dejar el proyecto
# en la estructura Junior limpia.
#
# Uso:
#   - Click derecho sobre el archivo -> "Ejecutar con PowerShell"
#   - O abre PowerShell en esta carpeta y escribe: .\cleanup.ps1

$ErrorActionPreference = "Continue"
$proj = $PSScriptRoot

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Crypto Pipeline - Limpieza Phase 1" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# Artefactos de Phase 1 (Senior DE) a eliminar
$toRemove = @(
    "src\crypto_pipeline",              # Package anidado (reemplazado por archivos planos en src/)
    "src\__init__.py",                  # Init del package Phase 1
    "database",                         # SQL star-schema (reemplazado por sql/)
    "docker-compose.yml",               # Docker Phase 1
    "pyproject.toml",                   # Build system Phase 1
    "requirements-dev.txt",             # Dev deps Phase 1
    "__pycache__",                      # Cache Python
    ".pytest_cache",                    # Cache pytest
    "binance_crypto_datalake_pipeline", # Carpeta duplicada vieja
    "CODE_REVIEW.md",                   # Review doc Phase 1
    "config"                            # Config dir Phase 1 (reemplazado por src/config.py)
)

foreach ($item in $toRemove) {
    $path = Join-Path $proj $item
    if (Test-Path $path) {
        Remove-Item $path -Recurse -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path $path)) {
            Write-Host "  [OK] Eliminado: $item" -ForegroundColor Green
        } else {
            Write-Host "  [!!] No se pudo eliminar: $item" -ForegroundColor Red
        }
    } else {
        Write-Host "  [--] Ya no existe: $item" -ForegroundColor DarkGray
    }
}

# Limpiar carpetas __pycache__ en todo el árbol del proyecto
Write-Host ""
Write-Host "  Limpiando __pycache__ en subdirectorios..." -ForegroundColor Yellow
Get-ChildItem -Path $proj -Filter "__pycache__" -Recurse -Directory -ErrorAction SilentlyContinue |
    ForEach-Object {
        Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "    Eliminado: $($_.FullName.Replace($proj, '.'))" -ForegroundColor DarkGray
    }

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Listo. Proyecto en estructura Junior." -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
