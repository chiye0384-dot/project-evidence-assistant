param([ValidateRange(1,65535)][int]$Port = 8765, [switch]$EnableQuery, [switch]$EnableSubmission, [switch]$EnableConsumption, [switch]$EnableJudging, [switch]$EnableFollowUp, [switch]$CheckAccess, [switch]$FollowUpStrictFunction, [switch]$FollowUpTwoPhase, [switch]$FollowUpClaimParts, [switch]$FollowUpProseKind, [switch]$FollowUpScopeView, [switch]$FollowUpScopeColumns, [switch]$FollowUpIndexColumns, [switch]$FollowUpSelectedTable, [ValidateRange(0,65535)][int]$ProxyPort = 0)
$ErrorActionPreference = 'Stop'
if ($FollowUpIndexColumns -and (-not $FollowUpTwoPhase -or $FollowUpScopeView -or $FollowUpScopeColumns)) {
    throw 'Index columns requires two-phase and excludes both legacy scope switches. No key was read.'
}
if ($FollowUpScopeColumns -and (-not $FollowUpTwoPhase -or $FollowUpScopeView)) {
    throw 'Scope columns requires two-phase and excludes scope-view. No key was read.'
}
if ($FollowUpSelectedTable -and (-not $FollowUpTwoPhase -or $FollowUpClaimParts)) {
    throw 'Selected table requires two-phase and excludes claim-parts. No key was read.'
}
if ($FollowUpScopeView -and -not $FollowUpTwoPhase) {
    throw 'Scope view requires the approved two-phase mode. No key was read.'
}
if ($FollowUpProseKind -and -not $FollowUpTwoPhase) {
    throw 'Prose kind requires the approved two-phase mode. No key was read.'
}
if ($FollowUpClaimParts -and -not $FollowUpTwoPhase) {
    throw 'Selected parts requires the approved two-phase mode. No key was read.'
}
if ($FollowUpStrictFunction -and $FollowUpTwoPhase) {
    throw 'Follow-up modes are mutually exclusive. No key was read.'
}
if (($FollowUpStrictFunction -or $FollowUpTwoPhase) -and -not $EnableFollowUp) {
    throw 'Strict function transport requires the approved follow-up switch. No key was read.'
}
if ($EnableFollowUp -and -not ($EnableQuery -or $EnableSubmission -or $EnableConsumption -or $EnableJudging)) {
    throw 'Follow-up requires at least one approved topic switch. No key was read.'
}
$taskProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskPython = Join-Path $taskProjectRoot '.venv\Scripts\python.exe'
$taskRun = Join-Path $taskProjectRoot 'run.py'
if (-not (Test-Path -LiteralPath $taskPython) -or -not (Test-Path -LiteralPath $taskRun)) {
    throw 'Project-local Python or run.py is missing.'
}
$taskListeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($taskListeners.Count -gt 0) {
    throw "Port $Port is occupied. No process was stopped. Use -Port 8766 if needed."
}
Write-Host 'Official DeepSeek API. Project cumulative budget <= CNY 50; previous spending is retained.'
if ($ProxyPort -gt 0) { Write-Host "This process uses the explicit loopback HTTP proxy 127.0.0.1:$ProxyPort for BOTH access check and model calls. Keep your current TUN setting." }
if ($EnableQuery) { Write-Host 'Query outlines and sections use the SAME cumulative project budget.' }
if ($EnableSubmission) { Write-Host 'Submission outlines and sections use the SAME cumulative project budget.' }
if ($EnableConsumption) { Write-Host 'Consumption outlines and sections use the SAME cumulative project budget.' }
if ($EnableJudging) { Write-Host 'Judging outlines and sections use the SAME cumulative project budget.' }
if ($EnableFollowUp) { Write-Host 'Follow-up on enabled topics uses the SAME cumulative project budget.' }
Write-Host 'Input the key locally. Do not send it to the chat. Startup makes no model request.'
$taskSecureKey = Read-Host 'DeepSeek API key (hidden)' -AsSecureString
$taskKeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($taskSecureKey)
$taskPreviousKey = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'Process')
Push-Location -LiteralPath $taskProjectRoot
try {
    $env:DEEPSEEK_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($taskKeyPointer)
    if ($CheckAccess) {
        Write-Host 'Checking official GET /models metadata only. No completion is requested. Safe result is saved locally.'
        $taskAccessCheck = Join-Path $taskProjectRoot 'scripts\Check-DeepSeekAccess.py'
        if (-not (Test-Path -LiteralPath $taskAccessCheck)) { throw 'Local metadata check helper is missing.' }
        $taskAccessArguments = @($taskAccessCheck)
        if ($ProxyPort -gt 0) { $taskAccessArguments += @('--proxy-port', $ProxyPort) }
        & $taskPython @taskAccessArguments
        if ($LASTEXITCODE -ne 0) { throw 'Official model-list access check did not pass. No model request or server launch. See the safe result above.' }
    }
    $taskArguments = @($taskRun, '--port', $Port, '--enable-paid-model')
    if ($ProxyPort -gt 0) { $taskArguments += @('--deepseek-proxy-port', $ProxyPort) }
    if ($EnableQuery) { $taskArguments += '--enable-query-model' }
    if ($EnableSubmission) { $taskArguments += '--enable-submission-model' }
    if ($EnableConsumption) { $taskArguments += '--enable-consumption-model' }
    if ($EnableJudging) { $taskArguments += '--enable-judging-model' }
    if ($EnableFollowUp) { $taskArguments += '--enable-followup-model' }
    if ($FollowUpStrictFunction) { $taskArguments += '--followup-strict-function' }
    if ($FollowUpTwoPhase) { $taskArguments += '--followup-two-phase' }
    if ($FollowUpClaimParts) { $taskArguments += '--followup-claim-parts' }
    if ($FollowUpSelectedTable) { $taskArguments += '--followup-selected-table' }
    if ($FollowUpProseKind) { $taskArguments += '--followup-prose-kind' }
    if ($FollowUpScopeView) { $taskArguments += '--followup-scope-view' }
    if ($FollowUpScopeColumns) { $taskArguments += '--followup-scope-columns' }
    if ($FollowUpIndexColumns) { $taskArguments += '--followup-index-columns' }
    & $taskPython @taskArguments
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($taskKeyPointer)
    if ($null -eq $taskPreviousKey) {
        Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
    } else {
        $env:DEEPSEEK_API_KEY = $taskPreviousKey
    }
    Pop-Location
}
