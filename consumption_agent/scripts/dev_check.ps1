param(
    [string]$Python = "",
    [switch]$SkipTests,
    [switch]$SkipRuff,
    [switch]$SkipMypy
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RepoRoot = (Resolve-Path (Join-Path $ProjectRoot "..")).Path

function Resolve-Python {
    if ($Python) {
        return $Python
    }

    $candidates = @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $RepoRoot ".venv\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    throw "Python was not found. Create a venv or pass -Python <path>."
}

function Run-Step([string]$Name, [scriptblock]$Body) {
    Write-Host "==> $Name"
    & $Body
}

$PythonExe = Resolve-Python
Write-Host "Project: $ProjectRoot"
Write-Host "Python:  $PythonExe"

Push-Location $ProjectRoot
try {
    Run-Step "Python version" {
        & $PythonExe --version
    }

    Run-Step "compileall" {
        & $PythonExe -m compileall -q .
    }

    Run-Step "conflict marker scan" {
        $matches = git grep -n -E "^(<<<<<<<|=======|>>>>>>>)" -- . 2>$null
        if ($LASTEXITCODE -eq 0) {
            $matches
            throw "Conflict markers found."
        }
        if ($LASTEXITCODE -ne 1) {
            throw "git grep failed while scanning conflict markers."
        }
    }

    Run-Step "secret pattern scan" {
        $patterns = @(
            "ghp_[A-Za-z0-9_]{30,}",
            "github_pat_[A-Za-z0-9_]{50,}",
            "[0-9]{8,10}:[A-Za-z0-9_-]{30,}"
        )
        foreach ($pattern in $patterns) {
            $matches = git grep -n -I -E $pattern -- . 2>$null
            if ($LASTEXITCODE -eq 0) {
                $matches
                throw "Potential secret detected by pattern: $pattern"
            }
            if ($LASTEXITCODE -ne 1) {
                throw "git grep failed while scanning secrets."
            }
        }
    }

    if (-not $SkipRuff) {
        Run-Step "ruff check" {
            & $PythonExe -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('ruff') else 2)"
            if ($LASTEXITCODE -eq 2) {
                throw "ruff is not installed. Run: pip install -e .[dev]"
            }
            & $PythonExe -m ruff check .
        }
    }

    if (-not $SkipMypy) {
        Run-Step "mypy" {
            & $PythonExe -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('mypy') else 2)"
            if ($LASTEXITCODE -eq 2) {
                throw "mypy is not installed. Run: pip install -e .[dev]"
            }
            & $PythonExe -m mypy consumption
        }
    }

    if (-not $SkipTests) {
        Run-Step "pytest" {
            & $PythonExe -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pytest') else 2)"
            if ($LASTEXITCODE -eq 2) {
                throw "pytest is not installed. Run: pip install -e .[dev]"
            }
            & $PythonExe -m pytest
        }
    }

    Write-Host "All requested checks passed."
}
finally {
    Pop-Location
}
