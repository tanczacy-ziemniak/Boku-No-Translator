param(
    [switch]$SkipDependencyInstall,
    [switch]$InstallCpuPaddle,
    [switch]$CpuOnly,
    [switch]$ForceBlackwellPaddle,
    [ValidateSet("auto", "cpu", "standard", "blackwell", "cuda118", "cuda126", "cuda129")]
    [string]$PaddleGpuRuntime = "auto",
    [string]$PaddleVersion = "3.3.1",
    [string]$Python311InstallerUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe",
    [ValidateSet("auto", "wheel", "source", "skip")]
    [string]$LlamaCudaInstallMode = "auto",
    [string]$LlamaCppPythonVersion = "0.3.32",
    [switch]$RequireLlamaCuda,
    [string]$LlamaCudaWheelIndex = "",
    [string]$LlamaPipTempDir = "",
    [string]$LlamaCudaToolkitRoot = "",
    [string]$DefaultLlamaCudaWheelIndex = "https://abetlen.github.io/llama-cpp-python/whl/cu124",
    [string]$BlackwellLlamaCudaWheelIndex = "",
    [string]$PaddleCpuIndexUrl = "https://www.paddlepaddle.org.cn/packages/stable/cpu/",
    [string]$PaddleCuda118IndexUrl = "https://www.paddlepaddle.org.cn/packages/stable/cu118/",
    [string]$PaddleCuda126IndexUrl = "https://www.paddlepaddle.org.cn/packages/stable/cu126/",
    [string]$PaddleCuda129IndexUrl = "https://www.paddlepaddle.org.cn/packages/stable/cu129/",
    [string]$CudaToolkitWingetId = "Nvidia.CUDA",
    [string]$VsBuildToolsWingetId = "Microsoft.VisualStudio.2022.BuildTools"
)

$ErrorActionPreference = "Stop"

$AppName = "boku-no-translator"
$DisplayName = "Boku No Translator"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $AppDir
$VenvDir = Join-Path $RootDir ".venv"
$Python = Join-Path $VenvDir "Scripts\python.exe"
$SitePackages = Join-Path $VenvDir "Lib\site-packages"
$NvidiaPackageDir = Join-Path $SitePackages "nvidia"
$LlamaLibDir = Join-Path $SitePackages "llama_cpp\lib"
$SitePackagesBinDir = Join-Path $SitePackages "bin"
$NvidiaRuntimeDirs = @(
    "cublas",
    "cuda_nvrtc",
    "cuda_runtime",
    "cudnn",
    "cufft",
    "curand",
    "cusolver",
    "cusparse",
    "nvjitlink"
)
$NvidiaRuntimePackages = @(
    "nvidia-cublas-cu12",
    "nvidia-cuda-nvrtc-cu12",
    "nvidia-cuda-runtime-cu12",
    "nvidia-cudnn-cu12",
    "nvidia-cufft-cu12",
    "nvidia-curand-cu12",
    "nvidia-cusolver-cu12",
    "nvidia-cusparse-cu12",
    "nvidia-nvjitlink-cu12"
)

function Find-Python311 {
    $Candidates = @(
        @{ Command = "py"; Args = @("-3.11") },
        @{ Command = "python"; Args = @() },
        @{ Command = "python3"; Args = @() },
        @{ Command = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"; Args = @() },
        @{ Command = Join-Path $env:ProgramFiles "Python311\python.exe"; Args = @() },
        @{ Command = Join-Path ${env:ProgramFiles(x86)} "Python311\python.exe"; Args = @() }
    )

    foreach ($Candidate in $Candidates) {
        try {
            $Command = $Candidate.Command
            if ($Command -like "*\*" -and -not (Test-Path $Command)) {
                continue
            }
            $Args = $Candidate.Args
            $Version = & $Command @Args -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and ($Version | Select-Object -First 1) -eq "3.11") {
                return $Candidate
            }
        } catch {
        }
    }
    return $null
}

function Install-Python311FromOfficialInstaller {
    $DownloadDir = Join-Path $RootDir ".build"
    New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
    $InstallerPath = Join-Path $DownloadDir "python-3.11-amd64.exe"

    Write-Host "Downloading Python 3.11 installer from python.org..."
    Write-Host "  $Python311InstallerUrl"
    Invoke-WebRequest -Uri $Python311InstallerUrl -OutFile $InstallerPath

    try {
        $Signature = Get-AuthenticodeSignature -LiteralPath $InstallerPath
        if ($Signature.Status -ne "Valid") {
            Write-Warning "Python installer signature status is '$($Signature.Status)'. Continuing, but verify the installer if this is unexpected."
        } elseif ($Signature.SignerCertificate.Subject -notlike "*Python Software Foundation*") {
            Write-Warning "Python installer is signed, but signer was not recognized as Python Software Foundation: $($Signature.SignerCertificate.Subject)"
        }
    } catch {
        Write-Warning "Could not verify Python installer signature: $($_.Exception.Message)"
    }

    Write-Host "Installing Python 3.11 silently for the current user..."
    $Process = Start-Process -FilePath $InstallerPath -ArgumentList @(
        "/quiet",
        "InstallAllUsers=0",
        "PrependPath=1",
        "Include_launcher=1",
        "Include_pip=1",
        "Include_test=0",
        "Shortcuts=0"
    ) -Wait -PassThru
    if ($Process.ExitCode -ne 0) {
        throw "Python 3.11 installer failed with exit code $($Process.ExitCode)."
    }
}

function Ensure-Python311 {
    $Found = Find-Python311
    if ($Found) {
        return $Found
    }

    $Winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($Winget) {
        Write-Host "Python 3.11 was not found. Installing Python 3.11 with winget..."
        & winget install --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements

        $Found = Find-Python311
        if ($Found) {
            return $Found
        }

        Write-Warning "winget completed, but Python 3.11 was not found. Falling back to the official python.org installer."
    } else {
        Write-Host "Python 3.11 was not found and winget is unavailable. Falling back to the official python.org installer."
    }

    Install-Python311FromOfficialInstaller

    $Found = Find-Python311
    if (-not $Found) {
        throw "Python 3.11 install completed, but Python 3.11 is still not available. Open a new terminal and rerun build_app.ps1."
    }
    return $Found
}

function Test-NvidiaGpu {
    $Candidates = @("nvidia-smi")
    if ($env:SystemRoot) {
        $Candidates += (Join-Path $env:SystemRoot "System32\nvidia-smi.exe")
    }
    foreach ($Candidate in $Candidates) {
        try {
            & $Candidate --query-gpu=index --format=csv,noheader,nounits 1>$null 2>$null
            if ($LASTEXITCODE -eq 0) {
                return $true
            }
        } catch {
        }
    }
    return $false
}

function ConvertTo-ComputeCapability {
    param([string]$Text)
    if (-not $Text) {
        return $null
    }
    $Value = 0.0
    $Style = [System.Globalization.NumberStyles]::Float
    $Culture = [System.Globalization.CultureInfo]::InvariantCulture
    if ([double]::TryParse($Text.Trim(), $Style, $Culture, [ref]$Value)) {
        return $Value
    }
    return $null
}

function Get-ComputeCapabilityFromGpuName {
    param([string]$Name)
    if (-not $Name) {
        return $null
    }

    if ($Name -match "RTX\s+50|Blackwell") { return 12.0 }
    if ($Name -match "H100|H200|H800|H20|GH200") { return 9.0 }
    if ($Name -match "RTX\s+40|L4|L40|L40S|Ada") { return 8.9 }
    if ($Name -match "RTX\s+30|A100|A800|A40|A30|A10|A16|A2|RTX\s+A|Ampere") { return 8.6 }
    if ($Name -match "RTX\s+20|GTX\s+16|T4|T1000|T2000|Turing|Quadro\s+RTX|Titan\s+RTX") { return 7.5 }
    if ($Name -match "V100|Titan\s+V|Volta|GV100") { return 7.0 }
    if ($Name -match "P100|GP100") { return 6.0 }
    if ($Name -match "GTX\s+10|GT\s+1030|Titan\s+Xp|Titan\s+X|Tesla\s+P|Quadro\s+P|Pascal") { return 6.1 }
    return $null
}

function Get-ConsumerGpuRuntimeModeFromName {
    param([string]$Name)
    if (-not $Name) {
        return $null
    }

    if ($Name -match "RTX\s+50|Blackwell") {
        return "cuda129"
    }
    if ($Name -match "RTX\s+40|RTX\s+30|RTX\s+20|Quadro\s+RTX|Titan\s+RTX") {
        return "cuda126"
    }
    if ($Name -match "GTX\s+16|GTX\s+10|GTX\s+9|GTX\s+8|GTX\s+7|GTX\s+6|GT\s+\d|Titan\s+Xp|Titan\s+X") {
        return "cuda118"
    }
    return $null
}

function Get-NvidiaGpuInfos {
    $Candidates = @("nvidia-smi")
    if ($env:SystemRoot) {
        $Candidates += (Join-Path $env:SystemRoot "System32\nvidia-smi.exe")
    }
    foreach ($Candidate in $Candidates) {
        try {
            $Output = & $Candidate --query-gpu=index,name,compute_cap --format=csv,noheader,nounits 2>$null
            if ($LASTEXITCODE -eq 0) {
                $Infos = @()
                foreach ($Line in $Output) {
                    $Parts = $Line -split ","
                    if ($Parts.Count -lt 3) {
                        continue
                    }
                    $Name = $Parts[1].Trim()
                    $ComputeCapability = ConvertTo-ComputeCapability $Parts[2]
                    if ($null -eq $ComputeCapability) {
                        $ComputeCapability = Get-ComputeCapabilityFromGpuName $Name
                    }
                    $Info = [PSCustomObject]@{
                        Index = [int]($Parts[0].Trim())
                        Name = $Name
                        ComputeCapability = $ComputeCapability
                    }
                    $Infos += $Info
                }
                if ($Infos.Count -gt 0) {
                    return $Infos
                }
            }

            $FallbackOutput = & $Candidate --query-gpu=index,name --format=csv,noheader,nounits 2>$null
            if ($LASTEXITCODE -eq 0) {
                $Infos = @()
                foreach ($Line in $FallbackOutput) {
                    $Parts = $Line -split ","
                    if ($Parts.Count -lt 2) {
                        continue
                    }
                    $Name = $Parts[1].Trim()
                    $Info = [PSCustomObject]@{
                        Index = [int]($Parts[0].Trim())
                        Name = $Name
                        ComputeCapability = Get-ComputeCapabilityFromGpuName $Name
                    }
                    $Infos += $Info
                }
                if ($Infos.Count -gt 0) {
                    return $Infos
                }
            }
        } catch {
        }
    }
    return @()
}

function Get-NvidiaGpuSummaryText {
    $Infos = Get-NvidiaGpuInfos
    if ($Infos.Count -eq 0) {
        return "No NVIDIA GPU detected by nvidia-smi."
    }
    $Rows = @()
    foreach ($Info in $Infos) {
        $Compute = "unknown"
        if ($null -ne $Info.ComputeCapability) {
            $Compute = $Info.ComputeCapability
        }
        $Rows += "gpu:$($Info.Index) $($Info.Name) compute=$Compute"
    }
    return ($Rows -join "; ")
}

function New-PaddleGpuRuntimePlan {
    param(
        [Parameter(Mandatory=$true)][string]$Mode,
        [Parameter(Mandatory=$true)][string]$Reason,
        [string]$PaddleIndexUrl = "",
        [string]$CudaLabel = ""
    )
    return [PSCustomObject]@{
        Mode = $Mode
        Reason = $Reason
        PaddleIndexUrl = $PaddleIndexUrl
        CudaLabel = $CudaLabel
    }
}

function Get-PaddleGpuRuntimePlan {
    if ($CpuOnly -or $InstallCpuPaddle -or $PaddleGpuRuntime -eq "cpu") {
        return New-PaddleGpuRuntimePlan `
            -Mode "cpu" `
            -Reason "CPU runtime was requested." `
            -PaddleIndexUrl $PaddleCpuIndexUrl `
            -CudaLabel "cpu"
    }
    if ($ForceBlackwellPaddle -or $PaddleGpuRuntime -eq "blackwell" -or $PaddleGpuRuntime -eq "cuda129") {
        return New-PaddleGpuRuntimePlan `
            -Mode "cuda129" `
            -Reason "CUDA 12.9 Paddle runtime was requested." `
            -PaddleIndexUrl $PaddleCuda129IndexUrl `
            -CudaLabel "CUDA 12.9"
    }
    if ($PaddleGpuRuntime -eq "standard" -or $PaddleGpuRuntime -eq "cuda126") {
        return New-PaddleGpuRuntimePlan `
            -Mode "cuda126" `
            -Reason "CUDA 12.6 Paddle runtime was requested." `
            -PaddleIndexUrl $PaddleCuda126IndexUrl `
            -CudaLabel "CUDA 12.6"
    }
    if ($PaddleGpuRuntime -eq "cuda118") {
        return New-PaddleGpuRuntimePlan `
            -Mode "cuda118" `
            -Reason "CUDA 11.8 Paddle runtime was requested." `
            -PaddleIndexUrl $PaddleCuda118IndexUrl `
            -CudaLabel "CUDA 11.8"
    }

    $Infos = Get-NvidiaGpuInfos
    if ($Infos.Count -eq 0) {
        return New-PaddleGpuRuntimePlan `
            -Mode "cpu" `
            -Reason "No NVIDIA GPU was detected by nvidia-smi." `
            -PaddleIndexUrl $PaddleCpuIndexUrl `
            -CudaLabel "cpu"
    }

    foreach ($Info in $Infos) {
        if ((Get-ConsumerGpuRuntimeModeFromName -Name $Info.Name) -eq "cuda129") {
            return New-PaddleGpuRuntimePlan `
                -Mode "cuda129" `
                -Reason "Detected NVIDIA RTX 50/Blackwell GPU: $($Info.Name)." `
                -PaddleIndexUrl $PaddleCuda129IndexUrl `
                -CudaLabel "CUDA 12.9"
        }
    }

    foreach ($Info in $Infos) {
        if ((Get-ConsumerGpuRuntimeModeFromName -Name $Info.Name) -eq "cuda126") {
            return New-PaddleGpuRuntimePlan `
                -Mode "cuda126" `
                -Reason "Detected NVIDIA RTX 20/30/40 GPU: $($Info.Name)." `
                -PaddleIndexUrl $PaddleCuda126IndexUrl `
                -CudaLabel "CUDA 12.6"
        }
    }

    foreach ($Info in $Infos) {
        if ((Get-ConsumerGpuRuntimeModeFromName -Name $Info.Name) -eq "cuda118") {
            return New-PaddleGpuRuntimePlan `
                -Mode "cuda118" `
                -Reason "Detected pre-RTX 20 consumer NVIDIA GPU: $($Info.Name)." `
                -PaddleIndexUrl $PaddleCuda118IndexUrl `
                -CudaLabel "CUDA 11.8"
        }
    }

    foreach ($Info in $Infos) {
        if (($null -ne $Info.ComputeCapability -and $Info.ComputeCapability -ge 12.0) -or $Info.Name -match "RTX\s+50|Blackwell") {
            return New-PaddleGpuRuntimePlan `
                -Mode "cuda129" `
                -Reason "Detected NVIDIA Blackwell/RTX 50 GPU: $($Info.Name) compute=$($Info.ComputeCapability)." `
                -PaddleIndexUrl $PaddleCuda129IndexUrl `
                -CudaLabel "CUDA 12.9"
        }
    }

    foreach ($Info in $Infos) {
        if ($null -ne $Info.ComputeCapability -and $Info.ComputeCapability -lt 6.0) {
            continue
        }
        if ($null -ne $Info.ComputeCapability -and $Info.ComputeCapability -lt 7.5) {
            return New-PaddleGpuRuntimePlan `
                -Mode "cuda118" `
                -Reason "Detected legacy NVIDIA GPU best matched to Paddle CUDA 11.8: $($Info.Name) compute=$($Info.ComputeCapability)." `
                -PaddleIndexUrl $PaddleCuda118IndexUrl `
                -CudaLabel "CUDA 11.8"
        }
    }

    foreach ($Info in $Infos) {
        if ($null -ne $Info.ComputeCapability -and $Info.ComputeCapability -ge 7.5) {
            return New-PaddleGpuRuntimePlan `
                -Mode "cuda126" `
                -Reason "Detected NVIDIA GPU best matched to Paddle CUDA 12.6: $($Info.Name) compute=$($Info.ComputeCapability)." `
                -PaddleIndexUrl $PaddleCuda126IndexUrl `
                -CudaLabel "CUDA 12.6"
        }
    }

    foreach ($Info in $Infos) {
        if ($null -eq $Info.ComputeCapability) {
            return New-PaddleGpuRuntimePlan `
                -Mode "cuda126" `
                -Reason "Detected NVIDIA GPU, but compute capability was unknown. Defaulting to Paddle CUDA 12.6: $($Info.Name)." `
                -PaddleIndexUrl $PaddleCuda126IndexUrl `
                -CudaLabel "CUDA 12.6"
        }
    }

    return New-PaddleGpuRuntimePlan `
        -Mode "cpu" `
        -Reason "Detected NVIDIA GPU(s), but compute capability is below the supported PaddleOCR/Paddle runtime range: $(Get-NvidiaGpuSummaryText)." `
        -PaddleIndexUrl $PaddleCpuIndexUrl `
        -CudaLabel "cpu"
}

function Test-BlackwellGpu {
    if ($ForceBlackwellPaddle) {
        return $true
    }
    return ((Get-PaddleGpuRuntimePlan).Mode -eq "cuda129")
}

function Get-PythonWheelTag {
    $Tag = & $Python -c "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $Tag) {
        throw "Could not determine Python wheel tag."
    }
    return ($Tag | Select-Object -First 1).Trim()
}

function Get-EffectiveLlamaCudaWheelIndex {
    if ($LlamaCudaWheelIndex) {
        return $LlamaCudaWheelIndex
    }
    if ((Get-PaddleGpuRuntimePlan).Mode -eq "cuda129" -and $BlackwellLlamaCudaWheelIndex) {
        return $BlackwellLlamaCudaWheelIndex
    }
    return $DefaultLlamaCudaWheelIndex
}

function Test-PythonPackage {
    param([Parameter(Mandatory=$true)][string]$PackageName)
    & $Python -c "import importlib.metadata as md; raise SystemExit(0 if '$PackageName' in [d.metadata.get('Name','').lower() for d in md.distributions()] else 1)" 1>$null 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Get-MaxNvidiaComputeCapability {
    $Infos = Get-NvidiaGpuInfos
    $Max = $null
    foreach ($Info in $Infos) {
        if ($null -eq $Info.ComputeCapability) {
            continue
        }
        if ($null -eq $Max -or $Info.ComputeCapability -gt $Max) {
            $Max = $Info.ComputeCapability
        }
    }
    return $Max
}

function Get-CudaArchitectureForLlamaBuild {
    $Compute = Get-MaxNvidiaComputeCapability
    if ($null -eq $Compute) {
        return "native"
    }
    return ([int][Math]::Round($Compute * 10)).ToString()
}

function Get-PreferredCudaToolkitMajorForLlamaBuild {
    $RuntimePlan = Get-PaddleGpuRuntimePlan
    if ($RuntimePlan.Mode -eq "cpu") {
        return $null
    }
    if ($RuntimePlan.Mode -eq "cuda118") {
        return 11
    }
    return 12
}

function Get-RequiredCudaToolkitVersionForLlamaBuild {
    $RuntimePlan = Get-PaddleGpuRuntimePlan
    switch ($RuntimePlan.Mode) {
        "cuda129" { return "12.9" }
        "cuda126" { return "12.6" }
        "cuda118" { return "11.8" }
        default { return $null }
    }
}

function Get-RequiredCudaToolkitLabelForLlamaBuild {
    $Version = Get-RequiredCudaToolkitVersionForLlamaBuild
    if (-not $Version) {
        return "CUDA Toolkit"
    }
    return "CUDA Toolkit $Version"
}

function Get-CudaToolkitVersionFromNvccPath {
    param([Parameter(Mandatory=$true)][string]$NvccPath)

    $ToolkitRoot = Split-Path -Parent (Split-Path -Parent $NvccPath)
    $VersionName = Split-Path -Leaf $ToolkitRoot
    if ($VersionName -match "^v(?<version>\d+(\.\d+)?)") {
        return $Matches.version
    }
    return $null
}

function Get-CudaToolkitMajorFromNvccPath {
    param([Parameter(Mandatory=$true)][string]$NvccPath)

    $Version = Get-CudaToolkitVersionFromNvccPath -NvccPath $NvccPath
    if ($Version -and $Version -match "^(?<major>\d+)") {
        return [int]$Matches.major
    }
    return $null
}

function Find-Nvcc {
    $Candidates = @()
    if ($LlamaCudaToolkitRoot) {
        $Candidates += (Join-Path $LlamaCudaToolkitRoot "bin\nvcc.exe")
    }
    $Command = Get-Command nvcc.exe -ErrorAction SilentlyContinue
    if ($Command) {
        $Candidates += $Command.Source
    }
    if ($env:CUDA_PATH) {
        $Candidates += (Join-Path $env:CUDA_PATH "bin\nvcc.exe")
    }
    $ToolkitRoot = Join-Path $env:ProgramFiles "NVIDIA GPU Computing Toolkit\CUDA"
    if (Test-Path $ToolkitRoot) {
        $Candidates += Get-ChildItem -LiteralPath $ToolkitRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "bin\nvcc.exe" }
    }

    $ExistingCandidates = @()
    foreach ($Candidate in ($Candidates | Select-Object -Unique)) {
        if ($Candidate -and (Test-Path $Candidate)) {
            $ExistingCandidates += $Candidate
        }
    }
    if (-not $ExistingCandidates) {
        return $null
    }

    $RequiredVersion = Get-RequiredCudaToolkitVersionForLlamaBuild
    if ($RequiredVersion) {
        foreach ($Candidate in $ExistingCandidates) {
            $Version = Get-CudaToolkitVersionFromNvccPath -NvccPath $Candidate
            if ($Version -eq $RequiredVersion) {
                return $Candidate
            }
        }
    }

    $PreferredMajor = Get-PreferredCudaToolkitMajorForLlamaBuild
    if ($PreferredMajor) {
        foreach ($Candidate in $ExistingCandidates) {
            $Major = Get-CudaToolkitMajorFromNvccPath -NvccPath $Candidate
            if ($Major -eq $PreferredMajor) {
                return $Candidate
            }
        }
    }
    return ($ExistingCandidates | Select-Object -First 1)
}

function Find-VisualStudioWithVCTools {
    $VsWhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $VsWhere)) {
        return $null
    }
    try {
        $InstallPath = & $VsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
        if ($LASTEXITCODE -eq 0 -and $InstallPath) {
            return ($InstallPath | Select-Object -First 1).Trim()
        }
    } catch {
    }
    return $null
}

function Get-VcVars64Path {
    $InstallPath = Find-VisualStudioWithVCTools
    if (-not $InstallPath) {
        return $null
    }
    $Candidates = @(
        (Join-Path $InstallPath "VC\Auxiliary\Build\vcvars64.bat"),
        (Join-Path $InstallPath "Common7\Tools\VsDevCmd.bat")
    )
    foreach ($Candidate in $Candidates) {
        if (Test-Path $Candidate) {
            return $Candidate
        }
    }
    return $null
}

function Import-BatchEnvironment {
    param(
        [Parameter(Mandatory=$true)][string]$BatchFile,
        [string]$Arguments = ""
    )
    $Command = "`"$BatchFile`" $Arguments >nul && set"
    $Output = & cmd.exe /s /c $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to import Visual Studio build environment from $BatchFile"
    }
    foreach ($Line in $Output) {
        $Index = $Line.IndexOf("=")
        if ($Index -le 0) {
            continue
        }
        $Name = $Line.Substring(0, $Index)
        $Value = $Line.Substring($Index + 1)
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

function Ensure-VisualStudioCompilerEnvironment {
    $ExistingCl = Get-Command cl.exe -ErrorAction SilentlyContinue
    $ExistingRc = Get-Command rc.exe -ErrorAction SilentlyContinue
    $ExistingMt = Get-Command mt.exe -ErrorAction SilentlyContinue
    if ($ExistingCl -and $ExistingRc -and $ExistingMt) {
        Write-Host "  MSVC compiler: $($ExistingCl.Source)"
        $env:CC = $ExistingCl.Source
        $env:CXX = $ExistingCl.Source
        $env:RC = $ExistingRc.Source
        Write-Host "  Windows SDK tools: rc=$($ExistingRc.Source); mt=$($ExistingMt.Source)"
        return
    }

    if (-not (Find-VisualStudioWithVCTools)) {
        $Installed = Invoke-WingetInstall `
            -PackageId $VsBuildToolsWingetId `
            -ExtraArgs @("--override", "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended")
        if (-not $Installed -or -not (Find-VisualStudioWithVCTools)) {
            throw "Visual Studio 2022 Build Tools with C++ workload was not found. Install it, then rerun build_app.ps1."
        }
    }

    $VcVars = Get-VcVars64Path
    if (-not $VcVars) {
        throw "Could not find vcvars64.bat or VsDevCmd.bat in the Visual Studio Build Tools installation."
    }
    Write-Host "  Loading MSVC build environment: $VcVars"
    Import-BatchEnvironment -BatchFile $VcVars -Arguments "x64"

    $Cl = Get-Command cl.exe -ErrorAction SilentlyContinue
    if (-not $Cl) {
        throw "cl.exe was not found after loading the Visual Studio build environment."
    }
    $Rc = Get-Command rc.exe -ErrorAction SilentlyContinue
    $Mt = Get-Command mt.exe -ErrorAction SilentlyContinue
    if (-not $Rc -or -not $Mt) {
        throw "Windows SDK build tools rc.exe and mt.exe were not found after loading the Visual Studio build environment. Install the Windows 10/11 SDK component for Visual Studio Build Tools, then rerun build_app.ps1."
    }
    $env:CC = $Cl.Source
    $env:CXX = $Cl.Source
    $env:RC = $Rc.Source
    Write-Host "  MSVC compiler: $($Cl.Source)"
    Write-Host "  Windows SDK tools: rc=$($Rc.Source); mt=$($Mt.Source)"
}

function Invoke-WingetInstall {
    param(
        [Parameter(Mandatory=$true)][string]$PackageId,
        [string]$Version = "",
        [switch]$Force,
        [string[]]$ExtraArgs = @()
    )
    $Winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $Winget) {
        return $false
    }
    $VersionArgs = @()
    $VersionText = ""
    if ($Version) {
        $VersionArgs = @("--version", $Version)
        $VersionText = " $Version"
    }
    if ($Force) {
        $VersionArgs += "--force"
    }
    Write-Host "Installing $PackageId$VersionText with winget..."
    & winget install --id $PackageId --exact @VersionArgs --silent --accept-package-agreements --accept-source-agreements @ExtraArgs
    return ($LASTEXITCODE -eq 0)
}

function Install-RequiredCudaToolkit {
    $RequiredVersion = Get-RequiredCudaToolkitVersionForLlamaBuild
    if (-not $RequiredVersion) {
        return $false
    }

    $RuntimePlan = Get-PaddleGpuRuntimePlan
    Write-Host "CUDA Toolkit was not found in a version matching $($RuntimePlan.CudaLabel)."
    Write-Host "Installing CUDA Toolkit $RequiredVersion automatically for $($RuntimePlan.Mode)..."

    $Installed = Invoke-WingetInstall -PackageId $CudaToolkitWingetId -Version $RequiredVersion -Force
    if (-not $Installed) {
        return $false
    }

    return $true
}

function Get-LlamaPipTempDir {
    if ($LlamaPipTempDir) {
        return [System.IO.Path]::GetFullPath($LlamaPipTempDir)
    }

    $Root = [System.IO.Path]::GetPathRoot($RootDir)
    if ($Root) {
        return (Join-Path $Root "t")
    }

    return (Join-Path $RootDir ".pip-tmp")
}

function Restore-EnvironmentVariable {
    param(
        [Parameter(Mandatory=$true)][string]$Name,
        [AllowNull()][string]$Value
    )

    if ($null -eq $Value) {
        Remove-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
    } else {
        Set-Item -LiteralPath "Env:$Name" -Value $Value
    }
}

function New-CMakeFilePathDefinition {
    param(
        [Parameter(Mandatory=$true)][string]$Name,
        [Parameter(Mandatory=$true)][string]$Path
    )

    $Normalized = $Path.Replace("\", "/")
    return ('-D{0}:FILEPATH="{1}"' -f $Name, $Normalized)
}

function Invoke-PipWithShortTemp {
    param([Parameter(Mandatory=$true)][string[]]$Arguments)

    $ShortPipTempDir = Get-LlamaPipTempDir
    New-Item -ItemType Directory -Force -Path $ShortPipTempDir | Out-Null

    $OldTemp = $env:TEMP
    $OldTmp = $env:TMP
    $OldTmpDir = $env:TMPDIR
    try {
        $env:TEMP = $ShortPipTempDir
        $env:TMP = $ShortPipTempDir
        $env:TMPDIR = $ShortPipTempDir
        & $Python -m pip @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "pip failed: python -m pip $($Arguments -join ' ')"
        }
    } finally {
        Restore-EnvironmentVariable -Name "TEMP" -Value $OldTemp
        Restore-EnvironmentVariable -Name "TMP" -Value $OldTmp
        Restore-EnvironmentVariable -Name "TMPDIR" -Value $OldTmpDir
    }
}

function Install-ProjectRequirements {
    $RequirementsPath = Join-Path $AppDir "requirements.txt"
    $RuntimePlan = Get-PaddleGpuRuntimePlan
    $InstallLlamaSeparately = (-not $SkipDependencyInstall) -and ($LlamaCudaInstallMode -ne "skip") -and ($RuntimePlan.Mode -ne "cpu")

    if (-not $InstallLlamaSeparately) {
        Invoke-PipWithShortTemp -Arguments @("install", "-r", $RequirementsPath)
        return
    }

    $TempDir = Get-LlamaPipTempDir
    New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
    $FilteredRequirementsPath = Join-Path $TempDir "boku_requirements_without_llama.txt"
    $Lines = Get-Content -LiteralPath $RequirementsPath |
        Where-Object { $_.Trim() -notmatch "^(llama-cpp-python)(\s|[<>=!~].*)?$" }
    Set-Content -LiteralPath $FilteredRequirementsPath -Value $Lines -Encoding UTF8

    Write-Host "Installing project requirements without llama-cpp-python; it is installed later with the selected CUDA mode."
    Invoke-PipWithShortTemp -Arguments @("install", "-r", $FilteredRequirementsPath)
}

function Ensure-LlamaSourceBuildPrerequisites {
    Write-Host "Preparing llama-cpp-python CUDA source build prerequisites..."
    & $Python -m pip install --upgrade cmake ninja scikit-build-core setuptools wheel
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install Python build packages required for llama-cpp-python source build."
    }
    Ensure-VisualStudioCompilerEnvironment

    $Nvcc = Find-Nvcc
    $RequiredVersion = Get-RequiredCudaToolkitVersionForLlamaBuild
    $DetectedVersion = if ($Nvcc) { Get-CudaToolkitVersionFromNvccPath -NvccPath $Nvcc } else { $null }
    if (-not $Nvcc -or ($RequiredVersion -and $DetectedVersion -ne $RequiredVersion)) {
        if ($Nvcc -and $DetectedVersion) {
            Write-Host "Found CUDA Toolkit $DetectedVersion, but $RequiredVersion is required for this GPU runtime: $Nvcc"
        }
        $Installed = Install-RequiredCudaToolkit
        $Nvcc = Find-Nvcc
        $DetectedVersion = if ($Nvcc) { Get-CudaToolkitVersionFromNvccPath -NvccPath $Nvcc } else { $null }
        if (-not $Installed -or -not $Nvcc -or ($RequiredVersion -and $DetectedVersion -ne $RequiredVersion)) {
            $RequiredLabel = Get-RequiredCudaToolkitLabelForLlamaBuild
            throw "$RequiredLabel nvcc.exe was not found after automatic installation. Install $RequiredLabel manually or pass -LlamaCudaToolkitRoot to its install directory, then rerun build_app.ps1."
        }
    }

    $PreferredMajor = Get-PreferredCudaToolkitMajorForLlamaBuild
    $DetectedMajor = Get-CudaToolkitMajorFromNvccPath -NvccPath $Nvcc
    if ($PreferredMajor -and $DetectedMajor -and $DetectedMajor -ne $PreferredMajor) {
        $RuntimePlan = Get-PaddleGpuRuntimePlan
        $RequiredLabel = if ($PreferredMajor -eq 11) { "CUDA 11.8" } else { "CUDA 12.x" }
        throw "Selected Paddle runtime '$($RuntimePlan.Mode)' requires a $RequiredLabel Toolkit for llama-cpp-python source builds, but nvcc was found under CUDA $DetectedMajor`: $Nvcc. Install $RequiredLabel Toolkit or pass -LlamaCudaToolkitRoot to its install directory, then rerun build_app.ps1."
    }

    $CudaHome = Split-Path -Parent (Split-Path -Parent $Nvcc)
    $env:CUDA_PATH = $CudaHome
    $env:CUDA_HOME = $CudaHome
    $env:PATH = (Join-Path $CudaHome "bin") + ";" + $env:PATH
    Write-Host "  CUDA Toolkit: $CudaHome"
}

function Install-LlamaCudaWheel {
    $EffectiveLlamaCudaWheelIndex = Get-EffectiveLlamaCudaWheelIndex
    if (-not $EffectiveLlamaCudaWheelIndex) {
        throw "No llama-cpp-python CUDA wheel index is configured."
    }
    Write-Host "Installing CUDA-enabled llama-cpp-python wheel..."
    Write-Host "  index: $EffectiveLlamaCudaWheelIndex"
    & $Python -m pip install --upgrade --force-reinstall --prefer-binary --no-deps --index-url $EffectiveLlamaCudaWheelIndex "llama-cpp-python==$LlamaCppPythonVersion"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install CUDA llama-cpp-python wheel from $EffectiveLlamaCudaWheelIndex."
    }
}

function Install-LlamaCudaFromSource {
    Ensure-LlamaSourceBuildPrerequisites
    $Arch = Get-CudaArchitectureForLlamaBuild
    $ShortPipTempDir = Get-LlamaPipTempDir
    New-Item -ItemType Directory -Force -Path $ShortPipTempDir | Out-Null

    Write-Host "Building llama-cpp-python with CUDA from source..."
    Write-Host "  version: $LlamaCppPythonVersion"
    Write-Host "  CMAKE_CUDA_ARCHITECTURES=$Arch"
    Write-Host "  pip temp: $ShortPipTempDir"

    $OldCmakeArgs = $env:CMAKE_ARGS
    $OldForceCmake = $env:FORCE_CMAKE
    $OldGenerator = $env:CMAKE_GENERATOR
    $OldTemp = $env:TEMP
    $OldTmp = $env:TMP
    $OldTmpDir = $env:TMPDIR
    $OldCudaHostCxx = $env:CUDAHOSTCXX
    try {
        $Mt = Get-Command mt.exe -ErrorAction SilentlyContinue
        if (-not $Mt) {
            throw "mt.exe was not found after loading the Visual Studio build environment."
        }
        $CmakeArgs = @(
            "-DGGML_CUDA=on",
            "-DCMAKE_CUDA_ARCHITECTURES=$Arch",
            (New-CMakeFilePathDefinition -Name "CMAKE_C_COMPILER" -Path $env:CC),
            (New-CMakeFilePathDefinition -Name "CMAKE_CXX_COMPILER" -Path $env:CXX),
            (New-CMakeFilePathDefinition -Name "CMAKE_CUDA_HOST_COMPILER" -Path $env:CXX),
            (New-CMakeFilePathDefinition -Name "CMAKE_RC_COMPILER" -Path $env:RC),
            (New-CMakeFilePathDefinition -Name "CMAKE_MT" -Path $Mt.Source)
        )
        $env:CMAKE_ARGS = ($CmakeArgs -join " ")
        $env:FORCE_CMAKE = "1"
        $env:CMAKE_GENERATOR = "Ninja"
        $env:CUDAHOSTCXX = $env:CXX
        $env:TEMP = $ShortPipTempDir
        $env:TMP = $ShortPipTempDir
        $env:TMPDIR = $ShortPipTempDir
        & $Python -m pip uninstall -y llama-cpp-python
        & $Python -m pip install --upgrade --force-reinstall --no-cache-dir --no-deps --no-binary=llama-cpp-python "llama-cpp-python==$LlamaCppPythonVersion"
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to build llama-cpp-python with CUDA from source. Check that MSVC Build Tools and CUDA Toolkit are installed and usable."
        }
    } finally {
        $env:CMAKE_ARGS = $OldCmakeArgs
        $env:FORCE_CMAKE = $OldForceCmake
        $env:CMAKE_GENERATOR = $OldGenerator
        Restore-EnvironmentVariable -Name "TEMP" -Value $OldTemp
        Restore-EnvironmentVariable -Name "TMP" -Value $OldTmp
        Restore-EnvironmentVariable -Name "TMPDIR" -Value $OldTmpDir
        Restore-EnvironmentVariable -Name "CUDAHOSTCXX" -Value $OldCudaHostCxx
    }
}

function Get-LlamaCppLibrarySummary {
    $Locations = @($LlamaLibDir, $SitePackagesBinDir)
    $Rows = @()
    foreach ($Location in $Locations) {
        if (-not (Test-Path $Location)) {
            $Rows += "$($Location): missing"
            continue
        }
        $Dlls = Get-ChildItem -LiteralPath $Location -Filter "*.dll" -File -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty Name
        if (-not $Dlls) {
            $Rows += "$($Location): no DLLs"
        } else {
            $Rows += "$($Location): $($Dlls -join ', ')"
        }
    }
    return ($Rows -join " | ")
}

function Sync-LlamaCppBinaryDlls {
    if (-not (Test-Path $SitePackagesBinDir)) {
        return
    }
    New-Item -ItemType Directory -Force -Path $LlamaLibDir | Out-Null
    $Names = @("ggml-base.dll", "ggml-cpu.dll", "ggml-cuda.dll", "ggml.dll", "llama.dll", "mtmd.dll")
    foreach ($Name in $Names) {
        $Source = Join-Path $SitePackagesBinDir $Name
        if (-not (Test-Path $Source)) {
            continue
        }
        $Destination = Join-Path $LlamaLibDir $Name
        Copy-Item -LiteralPath $Source -Destination $Destination -Force
        Write-Host "Synced llama.cpp DLL: $Source -> $Destination"
    }
}

function Test-LlamaCppGpuOffload {
    $Probe = @"
import ctypes
import os
import sys
import traceback
from pathlib import Path

site = Path(r'''$SitePackages''')
subdirs = [
    "llama_cpp/lib",
    "bin",
    "nvidia/cublas/bin",
    "nvidia/cuda_nvrtc/bin",
    "nvidia/cuda_runtime/bin",
    "nvidia/cudnn/bin",
    "nvidia/cufft/bin",
    "nvidia/curand/bin",
    "nvidia/cusolver/bin",
    "nvidia/cusparse/bin",
    "nvidia/nvjitlink/bin",
]

for rel in subdirs:
    path = site / rel
    if not path.is_dir():
        continue
    try:
        os.add_dll_directory(str(path))
    except Exception:
        pass
    os.environ["PATH"] = str(path) + os.pathsep + os.environ.get("PATH", "")

for rel in ("llama_cpp/lib", "bin"):
    lib_dir = site / rel
    for name in ("ggml-base.dll", "ggml-cpu.dll", "ggml-cuda.dll", "ggml.dll", "llama.dll", "mtmd.dll"):
        dll_path = lib_dir / name
        if not dll_path.is_file():
            continue
        try:
            ctypes.CDLL(str(dll_path))
        except Exception:
            pass

try:
    import llama_cpp
    print("llama_cpp_file=" + str(Path(llama_cpp.__file__).resolve()))
    supports = getattr(llama_cpp, "llama_supports_gpu_offload", None)
    if not callable(supports):
        print("llama_supports_gpu_offload=missing")
        sys.exit(2)
    ok = bool(supports())
    print("llama_supports_gpu_offload=" + str(ok))
    sys.exit(0 if ok else 3)
except Exception:
    traceback.print_exc()
    sys.exit(1)
"@
    $ProbePath = Join-Path ([System.IO.Path]::GetTempPath()) ("boku_llama_probe_{0}.py" -f ([Guid]::NewGuid().ToString("N")))
    $StdoutPath = Join-Path ([System.IO.Path]::GetTempPath()) ("boku_llama_probe_{0}.out" -f ([Guid]::NewGuid().ToString("N")))
    $StderrPath = Join-Path ([System.IO.Path]::GetTempPath()) ("boku_llama_probe_{0}.err" -f ([Guid]::NewGuid().ToString("N")))
    try {
        Set-Content -LiteralPath $ProbePath -Value $Probe -Encoding UTF8
        $Process = Start-Process `
            -FilePath $Python `
            -ArgumentList @($ProbePath) `
            -NoNewWindow `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $StdoutPath `
            -RedirectStandardError $StderrPath
        $ExitCode = $Process.ExitCode
        foreach ($Path in @($StdoutPath, $StderrPath)) {
            if (Test-Path $Path) {
                foreach ($Line in (Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue)) {
                    Write-Host "  $Line"
                }
            }
        }
        return ($ExitCode -eq 0)
    } finally {
        Remove-Item -LiteralPath $ProbePath, $StdoutPath, $StderrPath -Force -ErrorAction SilentlyContinue
    }
}

function Confirm-LlamaCppGpuOffload {
    param([string]$Context = "llama-cpp-python")

    Write-Host "Verifying CUDA llama.cpp GPU offload support ($Context)..."
    if (Test-LlamaCppGpuOffload) {
        Write-Host "CUDA llama.cpp GPU offload is available."
        return $true
    }

    $Summary = Get-LlamaCppLibrarySummary
    $Message = "CUDA llama.cpp GPU offload could not be verified. Libraries: $Summary"
    if ($RequireLlamaCuda) {
        throw $Message
    }
    Write-Warning $Message
    Write-Warning "Continuing build. Translation may run on CPU or show a runtime error until llama-cpp-python is rebuilt with CUDA."
    return $false
}

function Install-LlamaCppPythonCuda {
    if ($LlamaCudaInstallMode -eq "skip") {
        Write-Warning "Skipping CUDA llama-cpp-python installation by request."
        return
    }

    $RuntimePlan = Get-PaddleGpuRuntimePlan
    if ($RuntimePlan.Mode -eq "cpu") {
        Write-Host "CPU runtime selected. Skipping CUDA llama-cpp-python installation."
        return
    }

    $Mode = $LlamaCudaInstallMode
    if ($Mode -eq "auto") {
        $Mode = if ($RuntimePlan.Mode -eq "cuda129") { "source" } else { "wheel" }
    }

    if ($Mode -eq "source") {
        Install-LlamaCudaFromSource
    } else {
        Install-LlamaCudaWheel
    }

    Sync-LlamaCppBinaryDlls
    Confirm-LlamaCppGpuOffload -Context "after install" | Out-Null
}

function Ensure-CompatiblePythonPackages {
    & $Python -m pip install --upgrade "numpy>=1.24,<2.4" "opencv-python==4.10.0.84" "opencv-python-headless==4.10.0.84"
}

function Install-CudaPythonPackages {
    if ($CpuOnly) {
        Write-Host "CPU-only build requested. Skipping CUDA Python package installation."
        return
    }

    $RuntimePlan = Get-PaddleGpuRuntimePlan
    Write-Host "NVIDIA GPU summary: $(Get-NvidiaGpuSummaryText)"
    Write-Host "Selected Paddle runtime: $($RuntimePlan.Mode) / $($RuntimePlan.CudaLabel) ($($RuntimePlan.Reason))"

    if ($RuntimePlan.Mode -eq "cpu") {
        Write-Host "Skipping CUDA Python package installation because CPU runtime was selected."
        return
    }

    if ($RuntimePlan.Mode -eq "cuda129") {
        Write-Host "Installing CUDA 12.9 Python runtime packages for Blackwell/RTX 50..."
        & $Python -m pip install --upgrade `
            nvidia-cublas-cu12==12.9.2.10 `
            nvidia-cuda-nvrtc-cu12==12.9.86 `
            nvidia-cuda-runtime-cu12==12.9.79 `
            nvidia-cudnn-cu12==9.9.0.52 `
            nvidia-cufft-cu12==11.4.1.4 `
            nvidia-curand-cu12==10.3.10.19 `
            nvidia-cusolver-cu12==11.7.5.82 `
            nvidia-cusparse-cu12==12.5.10.65 `
            nvidia-nvjitlink-cu12==12.9.86
    } else {
        Write-Host "Installing CUDA runtime Python packages used by the bundled app..."
        & $Python -m pip install --upgrade `
            nvidia-cublas-cu12 `
            nvidia-cuda-nvrtc-cu12 `
            nvidia-cuda-runtime-cu12 `
            nvidia-cudnn-cu12 `
            nvidia-cufft-cu12 `
            nvidia-curand-cu12 `
            nvidia-cusolver-cu12 `
            nvidia-cusparse-cu12 `
            nvidia-nvjitlink-cu12
    }

    Install-LlamaCppPythonCuda
    Ensure-CompatiblePythonPackages
}

function Ensure-PaddleRuntime {
    $RuntimePlan = Get-PaddleGpuRuntimePlan
    Write-Host "Paddle runtime plan: $($RuntimePlan.Mode) / $($RuntimePlan.CudaLabel) ($($RuntimePlan.Reason))"

    if ($RuntimePlan.Mode -eq "cpu") {
        Write-Host "Installing CPU Paddle runtime."
        Write-Host "  $($RuntimePlan.PaddleIndexUrl)"
        if (Test-PythonPackage "paddlepaddle-gpu") {
            & $Python -m pip uninstall -y paddlepaddle-gpu
        }
        & $Python -m pip install --upgrade "paddlepaddle==$PaddleVersion" -i $RuntimePlan.PaddleIndexUrl
        return
    }

    if ($RuntimePlan.Mode -eq "cuda118" -or $RuntimePlan.Mode -eq "cuda126" -or $RuntimePlan.Mode -eq "cuda129") {
        Write-Host "Installing GPU Paddle runtime: $($RuntimePlan.CudaLabel)."
        Write-Host "  $($RuntimePlan.PaddleIndexUrl)"
        if (Test-PythonPackage "paddlepaddle") {
            & $Python -m pip uninstall -y paddlepaddle
        }
        if (Test-PythonPackage "paddlepaddle-gpu") {
            & $Python -m pip uninstall -y paddlepaddle-gpu
        }
        & $Python -m pip install --upgrade "paddlepaddle-gpu==$PaddleVersion" -i $RuntimePlan.PaddleIndexUrl
        return
    }
}

function Assert-GpuBuildInputs {
    $RuntimePlan = Get-PaddleGpuRuntimePlan
    if ($RuntimePlan.Mode -eq "cpu") {
        Write-Host "CPU Paddle runtime selected. Skipping CUDA build input assertions."
        return
    }
    $MissingRuntimeDirs = @()
    foreach ($RuntimeDir in $NvidiaRuntimeDirs) {
        $RuntimePath = Join-Path $NvidiaPackageDir $RuntimeDir
        if (-not (Test-Path $RuntimePath)) {
            $MissingRuntimeDirs += $RuntimePath
        }
    }
    if ($MissingRuntimeDirs.Count -gt 0) {
        throw "Required NVIDIA CUDA runtime package files were not found after automatic installation: $($MissingRuntimeDirs -join ', ')"
    }
    if ($LlamaCudaInstallMode -eq "skip") {
        Write-Warning "CUDA llama.cpp verification was skipped by request."
    } else {
        Sync-LlamaCppBinaryDlls
        Confirm-LlamaCppGpuOffload -Context "before PyInstaller packaging" | Out-Null
    }
}

if (-not (Test-Path $Python)) {
    Write-Host "Creating virtual environment: $VenvDir"
    $BootstrapPython = Ensure-Python311
    $BootstrapCommand = $BootstrapPython.Command
    $BootstrapArgs = $BootstrapPython.Args
    & $BootstrapCommand @BootstrapArgs -m venv $VenvDir
}

if (-not (Test-Path $Python)) {
    throw "Python venv was not created: $Python"
}

Push-Location $AppDir
try {
    & $Python -m pip install --upgrade pip

    if (-not $SkipDependencyInstall) {
        Install-ProjectRequirements
        Install-CudaPythonPackages
    }

    Ensure-PaddleRuntime
    Ensure-CompatiblePythonPackages

    & $Python -m pip install --upgrade pyinstaller
    Assert-GpuBuildInputs

    $PyInstallerArgs = @(
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name", $AppName,
        "--icon", "assets\app.ico",
        "--add-data", "config.yaml;.",
        "--add-data", "LICENSE.md;.",
        "--add-data", "assets;assets",
        "--collect-all", "imagesize",
        "--copy-metadata", "imagesize",
        "--copy-metadata", "opencv-contrib-python",
        "--copy-metadata", "pyclipper",
        "--copy-metadata", "pypdfium2",
        "--copy-metadata", "python-bidi",
        "--copy-metadata", "shapely",
        "--collect-all", "PySide6",
        "--collect-all", "paddle",
        "--collect-all", "paddleocr",
        "--collect-all", "paddlex",
        "--collect-all", "llama_cpp",
        "--collect-all", "huggingface_hub",
        "--collect-all", "modelscope",
        "--collect-all", "meikiocr",
        "--hidden-import", "paddle",
        "--hidden-import", "paddleocr",
        "--hidden-import", "paddlex",
        "--hidden-import", "llama_cpp",
        "--hidden-import", "huggingface_hub",
        "--exclude-module", "PySide6.scripts",
        "--exclude-module", "PySide6.scripts.deploy_lib",
        "--exclude-module", "paddle.tensorrt",
        "--exclude-module", "paddleocr._doc2md",
        "--exclude-module", "paddleocr._doc2md.math",
        "--exclude-module", "paddlex.inference.serving",
        "--exclude-module", "modelscope.trainers",
        "--exclude-module", "modelscope.trainers.parallel",
        "--exclude-module", "modelscope.metrics",
        "--exclude-module", "modelscope.ops",
        "--exclude-module", "modelscope.server",
        "--exclude-module", "ascii__mypyc",
        "--exclude-module", "confusion__mypyc",
        "--exclude-module", "escape__mypyc",
        "--exclude-module", "magic__mypyc",
        "--exclude-module", "orchestrator__mypyc",
        "--exclude-module", "statistical__mypyc",
        "--exclude-module", "structural__mypyc",
        "--exclude-module", "utf1632__mypyc",
        "--exclude-module", "utf8__mypyc",
        "--exclude-module", "validity__mypyc",
        "--exclude-module", "torch",
        "--exclude-module", "torchvision",
        "--exclude-module", "torchaudio",
        "--exclude-module", "tensorflow"
    )
    foreach ($RuntimeDir in $NvidiaRuntimeDirs) {
        $RuntimePath = Join-Path $NvidiaPackageDir $RuntimeDir
        if (Test-Path $RuntimePath) {
            $PyInstallerArgs += @("--add-data", "$RuntimePath;nvidia\$RuntimeDir")
        }
    }
    foreach ($PackageName in $NvidiaRuntimePackages) {
        if (Test-PythonPackage $PackageName) {
            $PyInstallerArgs += @("--copy-metadata", $PackageName)
        }
    }
    $PyInstallerArgs += @("app.py")

    & $Python -m PyInstaller @PyInstallerArgs

    $DistDir = Join-Path $AppDir "dist\$AppName"
    $PreloadBat = Join-Path $DistDir "preload_models.bat"
    @"
@echo off
cd /d "%~dp0"
echo Boku No Translator model preload
echo.
echo This downloads and verifies the configured OCR and translation models.
echo First run can take a long time because the GGUF translation model is several GB.
echo Keep this window open until you see: "All configured models are ready."
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "`$exe = Join-Path (Get-Location) '$AppName.exe'; `$logsDir = Join-Path `$env:LOCALAPPDATA 'boku-no-translator\logs'; `$log = Join-Path `$logsDir 'preload_models.log'; `$appLog = Join-Path `$logsDir 'app.log'; Remove-Item -LiteralPath `$log -Force -ErrorAction SilentlyContinue; `$p = Start-Process -FilePath `$exe -ArgumentList '--preload-models' -PassThru; `$last = 0; while (-not `$p.HasExited) { if (Test-Path `$log) { `$lines = Get-Content -LiteralPath `$log -ErrorAction SilentlyContinue; if (`$lines.Count -gt `$last) { `$lines[`$last..(`$lines.Count - 1)]; `$last = `$lines.Count } }; Start-Sleep -Seconds 1 }; if (Test-Path `$log) { `$lines = Get-Content -LiteralPath `$log -ErrorAction SilentlyContinue; if (`$lines.Count -gt `$last) { `$lines[`$last..(`$lines.Count - 1)] } }; `$code = `$p.ExitCode; if (`$code -ne 0) { `$hex = ('{0:X8}' -f (`$code -band 0xffffffff)); Write-Host ('[preload] app exited with code {0} (0x{1})' -f `$code, `$hex); if (Test-Path `$appLog) { Write-Host '[preload] app.log tail:'; Get-Content -LiteralPath `$appLog -Tail 80 -ErrorAction SilentlyContinue }; exit `$code }; exit 0"
pause
"@ | Set-Content -LiteralPath $PreloadBat -Encoding ASCII

    $ReadmeFirst = Join-Path $DistDir "README_FIRST.txt"
    @"
Boku No Translator
==================

Quick start from ZIP
1. Run boku-no-translator.exe.
2. The app starts in the system tray.
3. Click the tray icon to open Settings / Usage / Language.
4. On first use, OCR and translation models may download automatically.

Recommended first run
- Run preload_models.bat once before using the overlay.
- It downloads and verifies PaddleOCR and the GGUF translation model.
- The translation model is several GB, so the first run can take a long time.

Device behavior
- OCR device and Translation device are separate settings.
- The app auto-detects NVIDIA GPUs for device selection.
- The ZIP contains the Paddle runtime selected when it was built. Rebuild on the target PC if the GPU family is different.
- If one GPU exists, the default is gpu:0.
- If no GPU exists, the default is cpu.
- If an invalid device such as gpu:1 is found on a one-GPU machine, it is corrected to gpu:0.

Status overlay
- Green means ready on the shown device.
- Yellow means CPU fallback.
- Gray means loading/downloading.
- Red means failed. Check logs at:
  %LOCALAPPDATA%\boku-no-translator\logs\app.log

No Python is required for the ZIP package. Python and llama.cpp are bundled inside _internal.
"@ | Set-Content -LiteralPath $ReadmeFirst -Encoding UTF8

    Write-Host ""
    Write-Host "Build complete:"
    Write-Host "  $DistDir\$AppName.exe"
    Write-Host "  $PreloadBat"
    Write-Host ""
    Write-Host "App name: $DisplayName ($AppName)"
} finally {
    Pop-Location
}
