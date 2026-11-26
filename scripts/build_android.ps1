# 安卓构建脚本：封装本机特有的环境参数
#
# 用法：
#   .\scripts\build_android.ps1            # 构建 debug APK
#   .\scripts\build_android.ps1 -Release   # 构建 release APK
#   .\scripts\build_android.ps1 -Install   # 构建后直接安装到已连接的设备
#   .\scripts\build_android.ps1 -DryRun    # 只打印将要使用的环境参数
#
param(
    [switch]$Release,
    [switch]$Install,
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"   # Flutter 会往 stderr 打提示，不能当异常处理

# ---------- 路径 ----------
$ProjectRoot = Split-Path -Parent $PSScriptRoot          # AI_角色扮演
$AsciiJunction = "D:\ai_roleplay"                        # 纯 ASCII 构建路径
$FrontendDir = Join-Path $AsciiJunction "frontend"
$GradleHome = Join-Path $ProjectRoot ".gradle_home"
$JdkHome = "D:\software\JDK"                             # JDK 21
$AndroidSdk = "D:\software\andriod_data\sdk"
$Adb = Join-Path $AndroidSdk "platform-tools\adb.exe"
$DartExe = "D:\flutter\flutter\bin\cache\dart-sdk\bin\dart.exe"
$FlutterTools = "D:\flutter\flutter\bin\cache\flutter_tools.snapshot"

# ---------- 1. 确保 ASCII junction 存在（中文路径会让 Kotlin 编译失败）----------
if (-not (Test-Path (Join-Path $AsciiJunction "frontend\pubspec.yaml"))) {
    if (Test-Path $AsciiJunction) {
        Write-Host "移除失效的 junction: $AsciiJunction"
        (Get-Item $AsciiJunction).Delete()
    }
    Write-Host "创建 junction: $AsciiJunction -> $ProjectRoot"
    New-Item -ItemType Junction -Path $AsciiJunction -Target $ProjectRoot | Out-Null
}

# ---------- 2. 环境变量 ----------
$env:JAVA_HOME = $JdkHome
$env:GRADLE_USER_HOME = $GradleHome
$env:ANDROID_HOME = $AndroidSdk
$env:ANDROID_SDK_ROOT = $AndroidSdk
$env:FLUTTER_ALREADY_LOCKED = "true"   # 跳过 Flutter 工具锁（CI/脚本场景）
# 全局 pub 缓存目录只读，pub 需要写 active_roots，因此改用项目内缓存
$env:PUB_CACHE = Join-Path $ProjectRoot ".pub_cache"

Write-Host "JAVA_HOME        = $env:JAVA_HOME"
Write-Host "GRADLE_USER_HOME = $env:GRADLE_USER_HOME"
Write-Host "ANDROID_HOME     = $env:ANDROID_HOME"
Write-Host "构建目录          = $FrontendDir"

if ($DryRun) {
    Write-Host "`n[DryRun] 环境检查完成，未执行构建。"
    exit 0
}

# ---------- 3. 磁盘检查（构建需要 ~3GB 空闲）----------
$freeGb = [math]::Round((Get-PSDrive -Name D).Free / 1GB, 2)
Write-Host "D 盘可用空间: ${freeGb}GB"
if ($freeGb -lt 3) {
    Write-Warning "D 盘可用空间不足 3GB，构建可能因磁盘空间失败。"
}

# ---------- 4. 拉取依赖 ----------
Push-Location $FrontendDir
try {
    & $DartExe $FlutterTools pub get 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "pub get 失败" }

    # ---------- 5. 构建 ----------
    $buildArgs = @("build", "apk", "--android-skip-build-dependency-validation")
    if ($Release) { $buildArgs += "--release" } else { $buildArgs += "--debug" }

    Write-Host "`n执行: flutter $($buildArgs -join ' ')"
    & $DartExe $FlutterTools @buildArgs 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "构建失败" }

    $apk = if ($Release) {
        Join-Path $FrontendDir "build\app\outputs\flutter-apk\app-release.apk"
    } else {
        Join-Path $FrontendDir "build\app\outputs\flutter-apk\app-debug.apk"
    }

    # 磁盘满时 assembleDebug 末尾的 copy 可能失败，回退取原始产物
    if (-not (Test-Path $apk)) {
        $fallback = Join-Path $FrontendDir "build\app\outputs\apk\debug\app-debug.apk"
        if (Test-Path $fallback) { $apk = $fallback }
    }

    if (-not (Test-Path $apk)) { throw "未找到构建产物" }
    Write-Host "`nAPK: $apk ($([math]::Round((Get-Item $apk).Length / 1MB, 1)) MB)"

    # ---------- 6. 安装 ----------
    if ($Install) {
        Write-Host "`n安装到设备..."
        & $Adb wait-for-device
        & $Adb install -r $apk
        if ($LASTEXITCODE -ne 0) { throw "安装失败" }
        & $Adb shell am start -n "com.roleplay.ai_roleplay_chat/.MainActivity"
        Write-Host "已启动应用"
    }
}
finally {
    Pop-Location
}
