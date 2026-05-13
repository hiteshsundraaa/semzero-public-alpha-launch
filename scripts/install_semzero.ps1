param(
  [string]$Extras = "",
  [string]$PythonBin = "python",
  [string]$VenvDir = ".venv"
)
& $PythonBin -m venv $VenvDir
& "$VenvDir\Scripts\python.exe" -m pip install --upgrade pip
if ($Extras -ne "") {
  & "$VenvDir\Scripts\python.exe" -m pip install -e ".[$Extras]"
} else {
  & "$VenvDir\Scripts\python.exe" -m pip install -e .
}
Write-Host "`nSemZero installed. Activate with:`n  $VenvDir\Scripts\Activate.ps1`nThen run:`n  semzero commands`n  semzero shadow --help"
