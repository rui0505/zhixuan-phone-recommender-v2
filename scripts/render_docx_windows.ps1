param([Parameter(Mandatory=$true)][string]$Path, [Parameter(Mandatory=$true)][string]$OutputDir)
$ErrorActionPreference = 'Stop'
$taskInput = (Resolve-Path -LiteralPath $Path).Path
$taskOutput = [IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Path $taskOutput -Force | Out-Null
$taskPdf = Join-Path $taskOutput 'report.pdf'
$taskWord = $null
$taskDocument = $null
try {
    $taskWord = New-Object -ComObject Word.Application
    $taskWord.Visible = $false
    $taskWord.DisplayAlerts = 0
    $taskDocument = $taskWord.Documents.Open($taskInput, $false, $true)
    $taskDocument.ExportAsFixedFormat($taskPdf, 17)
    Write-Output "Read-only Word COM export: $taskPdf"
} finally {
    if ($null -ne $taskDocument) { $taskDocument.Close(0) }
    if ($null -ne $taskWord) { $taskWord.Quit() }
}
