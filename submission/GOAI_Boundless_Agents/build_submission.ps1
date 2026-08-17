$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$markdown = Get-ChildItem -LiteralPath $root -Filter 'RoadMan_*.md' | Select-Object -First 1 -ExpandProperty FullName
$base = [IO.Path]::GetFileNameWithoutExtension($markdown)
$html = Join-Path $root ($base + '.html')
$docx = Join-Path $root ($base + '.docx')
$reference = Join-Path $root 'reference.docx'

python (Join-Path $root 'tools\create_reference_docx.py')
pandoc $markdown --from markdown+fenced_divs --to html5 --standalone --embed-resources --resource-path $root --css (Join-Path $root 'submission.css') -o $html
pandoc $markdown --from markdown+fenced_divs --to docx --resource-path $root --reference-doc $reference --toc --toc-depth=2 -o $docx
node (Join-Path $root 'tools\print_pdf.mjs')

Write-Host "Built:"
Get-Item $html,$docx,(Join-Path $root ($base + '.pdf')) | Select-Object FullName,Length,LastWriteTime
