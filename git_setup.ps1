Set-Location $PSScriptRoot

Write-Host "=== Limpiando git anterior ===" -ForegroundColor Cyan
if (Test-Path ".git") { Remove-Item -Recurse -Force ".git" }

Write-Host "=== Inicializando repositorio ===" -ForegroundColor Cyan
git init -b main
git config user.name "Carlos Orlando"
git config user.email "menesescoronacarlosorlando@gmail.com"

Write-Host "=== Commit 1 ===" -ForegroundColor Green
git add .gitignore .env.example
git commit -m "chore: init repo, gitignore y env.example"

Write-Host "=== Commit 2 ===" -ForegroundColor Green
git add README.md
git commit -m "docs: README con descripcion del proyecto y estructura"

Write-Host "=== Commit 3 ===" -ForegroundColor Green
git add requirements.txt pyproject.toml
git commit -m "feat: requirements.txt y pyproject.toml con dependencias del proyecto"

Write-Host "=== Commit 4 ===" -ForegroundColor Green
git add src/__init__.py src/config.py src/utils.py
git commit -m "feat(src): paquete base, config.py y utils.py"

Write-Host "=== Commit 5 ===" -ForegroundColor Green
git add src/ingestion.py
git commit -m "feat(src): modulo de ingestion de datos desde API Binance"

Write-Host "=== Commit 6 ===" -ForegroundColor Green
git add src/transform.py
git commit -m "feat(src): modulo ETL de transformacion y calculo de indicadores"

Write-Host "=== Commit 7 ===" -ForegroundColor Green
git add src/validation.py
git commit -m "feat(src): modulo de validacion de integridad de datos"

Write-Host "=== Commit 8 ===" -ForegroundColor Green
git add src/database.py
git commit -m "feat(src): modulo de base de datos PostgreSQL con SQLAlchemy"

Write-Host "=== Commit 9 ===" -ForegroundColor Green
git add src/export_data.py
git commit -m "feat(src): modulo de exportacion CSV para Power BI dashboard"

Write-Host "=== Commit 10 ===" -ForegroundColor Green
git add main.py
git commit -m "feat: main.py orquestador del pipeline completo con CLI args"

Write-Host "=== Commit 11 ===" -ForegroundColor Green
git add sql/
git commit -m "feat(sql): esquema PostgreSQL, tablas, vistas y queries analiticas"

Write-Host "=== Commit 12 ===" -ForegroundColor Green
git add research/quant_eda.py research/s2_hypothesis_strategy_lab.py 2>$null
git add 02_quant_eda.py s2_hypothesis_strategy_lab.py 2>$null
git commit -m "feat(research): EDA cuantitativo y laboratorio de hipotesis H1-H4"

Write-Host "=== Commit 13 ===" -ForegroundColor Green
git add research/q3_backtesting_framework.py research/q4_quant_research_lab.py 2>$null
git add research/q5_pattern_discovery.py research/q6_backtest_engine.py 2>$null
git add q3_backtesting_framework.py q4_quant_research_lab.py 2>$null
git add q5_pattern_discovery.py q6_backtest_engine.py 2>$null
git add exposure_benchmark.py 2>$null
git commit -m "feat(research): backtesting framework vectorizado y engine walk-forward"

Write-Host "=== Commit 14 ===" -ForegroundColor Green
git add research/s3_overfit_audit.py research/s4_institutional_validation.py 2>$null
git add s3_overfit_audit.py 2>$null
git commit -m "feat(research): auditoria de overfitting y validacion institucional OOS"

Write-Host "=== Commit 15 ===" -ForegroundColor Green
git add tests/
git commit -m "test: suite de tests unitarios e integracion del pipeline"

Write-Host "=== Commit 16 ===" -ForegroundColor Green
git add notebooks/
git commit -m "feat(notebooks): notebooks Jupyter de analisis y experimentacion"

Write-Host "=== Commit 17 ===" -ForegroundColor Green
git add dashboard/ crypto_dark_theme.json
git commit -m "feat(dashboard): estructura Power BI y tema oscuro personalizado"

Write-Host "=== Commit 18 ===" -ForegroundColor Green
git add docs/
git commit -m "docs: arquitectura, metodologia, hallazgos y roadmap"

Write-Host "=== Commit 19 ===" -ForegroundColor Green
git add cleanup.ps1 newtest.txt 2>$null
git add --all
git commit -m "chore: scripts auxiliares y archivos de utilidad del proyecto"

Write-Host "`n=== LISTO ===" -ForegroundColor Cyan
git log --oneline
Write-Host "`nTotal commits: $(git rev-list --count HEAD)" -ForegroundColor Yellow
Write-Host "`nPara subir a GitHub ejecuta:" -ForegroundColor Cyan
Write-Host "  git remote add origin https://github.com/TU_USUARIO/binance-crypto-pipeline.git"
Write-Host "  git push -u origin main"
