$ErrorActionPreference = "Stop"

function Show-Dialog {
    param(
        [string]$Title,
        [string]$Message,
        [string]$Buttons = "OK",
        [string]$Icon = "Information"
    )
    Add-Type -AssemblyName PresentationFramework
    return [System.Windows.MessageBox]::Show($Message, $Title, $Buttons, $Icon)
}

function Find-Conda {
    $savedPathFile = Join-Path $PSScriptRoot ".pyfeat_conda_path.txt"
    if (Test-Path -LiteralPath $savedPathFile) {
        $savedPath = (Get-Content -LiteralPath $savedPathFile -TotalCount 1).Trim()
        if ($savedPath -and (Test-Path -LiteralPath $savedPath)) {
            return $savedPath
        }
    }
    if ($env:CONDA_EXE -and (Test-Path -LiteralPath $env:CONDA_EXE)) {
        return $env:CONDA_EXE
    }
    $fromPath = Get-Command conda.exe -ErrorAction SilentlyContinue
    if ($fromPath) {
        return $fromPath.Source
    }
    $candidates = @(
        (Join-Path $env:USERPROFILE "miniconda3\Scripts\conda.exe"),
        (Join-Path $env:USERPROFILE "miniconda3\condabin\conda.bat"),
        (Join-Path $env:USERPROFILE "anaconda3\Scripts\conda.exe"),
        (Join-Path $env:USERPROFILE "anaconda3\condabin\conda.bat")
    )
    if ($env:LOCALAPPDATA) {
        $candidates += Join-Path $env:LOCALAPPDATA "miniconda3\Scripts\conda.exe"
        $candidates += Join-Path $env:LOCALAPPDATA "miniconda3\condabin\conda.bat"
    }
    if ($env:PROGRAMDATA) {
        $candidates += Join-Path $env:PROGRAMDATA "miniconda3\Scripts\conda.exe"
        $candidates += Join-Path $env:PROGRAMDATA "miniconda3\condabin\conda.bat"
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

try {
    $appDir = $PSScriptRoot
    $assistant = Join-Path $appDir "setup_assistant.py"
    if (-not (Test-Path -LiteralPath $assistant)) {
        throw "Missing required file: setup_assistant.py"
    }

    $conda = Find-Conda
    $pythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $conda -and $pythonLauncher) {
        # A new Windows computer may have py.exe but no Python 3.11.  That is
        # not a usable runtime for this tool, so continue to the Miniconda
        # guidance instead of showing a technical launcher error.
        & $pythonLauncher.Source -3.11 -c "import sys; print(sys.executable)" *> $null
        $hasPython311 = ($LASTEXITCODE -eq 0)
        if ($hasPython311) {
            $details = (& $pythonLauncher.Source -3.11 $assistant 2>&1 | Out-String)
            $exitCode = $LASTEXITCODE
            if ($exitCode -ne 0) {
                throw "Python 3.11 returned exit code $exitCode.`n$details"
            }
            exit 0
        }
    }
    if (-not $conda) {
        $answer = Show-Dialog -Title "Conda was not found" -Message "Conda was not found in the usual folders. If you installed it in another folder, choose No and select conda.exe or conda.bat manually. Choose Yes only if Conda was not installed completely and you need the official Miniconda installer." -Buttons "YesNoCancel" -Icon "Information"
        if ($answer -eq "Yes") {
            Start-Process "https://pro.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"
            exit 0
        }
        if ($answer -eq "No") {
            $picker = New-Object Microsoft.Win32.OpenFileDialog
            $picker.Title = "Select conda.exe or conda.bat"
            $picker.Filter = "Conda launcher (conda.exe;conda.bat)|conda.exe;conda.bat|Executable files (*.exe)|*.exe|Batch files (*.bat)|*.bat"
            $picker.FileName = "conda.exe"
            if ($picker.ShowDialog()) {
                $conda = $picker.FileName
                Set-Content -LiteralPath (Join-Path $PSScriptRoot ".pyfeat_conda_path.txt") -Value $conda -Encoding ascii
            }
            else {
                exit 0
            }
        }
        else {
            exit 0
        }
    }

    if ($conda) {
        $details = (& $conda run -n base python $assistant 2>&1 | Out-String)
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "Conda returned exit code $exitCode.`n$details"
        }
        exit 0
    }

}
catch {
    $logPath = Join-Path $PSScriptRoot "setup_launcher_error.log"
    $_ | Out-String | Set-Content -LiteralPath $logPath -Encoding utf8
    Show-Dialog -Title "Setup assistant could not open" -Message ("The setup window did not open. Please send a screenshot of this message and the file setup_launcher_error.log.\n\n" + $_.Exception.Message) -Icon "Error" | Out-Null
    exit 1
}

