# Добавляет домен(ы) в сеть: сразу на роутере, следом везде.
# Использование: .\add-domain.ps1 example.com [ещё.com ...]
param([Parameter(Mandatory=$true, ValueFromRemainingArguments=$true)][string[]]$Domains)

$ErrorActionPreference = "Stop"
$repo = "D:\Projects\apps\NetRules"
$t0 = Get-Date
function step($n, $t) { "[{0,5:N1}с] {1}" -f ((Get-Date)-$t0).TotalSeconds, $t }

# Любая нативная команда (ssh, gh, git) может писать в stderr и при успехе: предупреждение
# ssh про known_hosts, уведомление GitHub. При $ErrorActionPreference="Stop" PowerShell 5.1
# превращает такую строку в NativeCommandError и роняет скрипт. Поэтому все вызовы идут
# через эту обёртку: реакция ослаблена, stderr склеен со stdout, итог судим по коду возврата.
# Возвращает объект: Out (строки вывода) и Code (код возврата).
function Native([scriptblock]$cmd) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = @(& $cmd 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prev }
    [pscustomobject]@{ Out = $out; Code = $code }
}

Set-Location $repo

# Нормализуем ДО роутера, чтобы на роутер и в репозиторий уехало одно и то же имя.
# Нижний регистр: в SNI и в DNS-запросе имя приходит строчным, а sing-box сравнивает
# суффикс строкой - "GitHub.com" в наборе не совпадёт ни с чем.
$Domains = $Domains | ForEach-Object { $_.TrimStart([char]0xFEFF).Trim().ToLower() } |
    Where-Object { $_ -ne '' }
# Имена уходят в оболочку роутера и VPS: пускаем только то, что бывает в имени хоста.
foreach ($d in $Domains) {
    if ($d -notmatch '^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$') { throw "странное имя домена: '$d'" }
}

# --- 1. Мгновенно на роутере: срочный набор + перечитка конфига без перезапуска
$piCmd = "/root/urgent/add-domain.sh " + ($Domains -join " ")
$pi = (Native { ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@192.168.10.1 $piCmd }).Out
step 1 "роутер: $($pi -join '; ')"

# --- 2. В репозиторий: чтобы доехало до всех устройств и осталось навсегда
# TrimStart по U+FEFF обязателен: Get-Content отдаёт метку порядка байт как часть
# первой строки, скрипт её сохранял, сортировка уводила строку в конец файла - и
# домен уезжал в набор с невидимым префиксом. Ровно так addyosmani.com 14 суток
# лежал в релизе latest, не совпадая ни с одним запросом.
$cur = Get-Content "src\main-domains.lst" |
    ForEach-Object { $_.TrimStart([char]0xFEFF).Trim().ToLower() } |
    Where-Object { $_ -ne '' }
$add = $Domains | Where-Object { $cur -notcontains $_ }
if ($add) {
    # Out-File -Encoding utf8 в PowerShell 5.1 пишет файл С меткой BOM: чтение выше её
    # срезало, а запись ставила обратно первой строке. Так 25.08 addyosmani.com снова
    # уехал в набор битым. Пишем UTF-8 без метки.
    $srcPath = Join-Path (Get-Location) "src\main-domains.lst"
    [System.IO.File]::WriteAllLines($srcPath, [string[]](($cur + $add) | Sort-Object -Unique), (New-Object System.Text.UTF8Encoding $false))
    git add -A | Out-Null
    git -c user.name="HRYNdev" -c user.email="240798276+HRYNdev@users.noreply.github.com" commit -q -m "добавлены домены: $($add -join ', ')" | Out-Null
    # git пишет в stderr не только ошибки: GitHub присылает туда уведомление
    # "Bypassed rule violations for refs/heads/main". При $ErrorActionPreference="Stop"
    # PowerShell 5.1 превращает любую строку stderr нативной команды в NativeCommandError
    # и роняет скрипт на успешном пуше (поймано 25.08.2026: шаги 1-2 прошли, 3-5 нет).
    # Поэтому вокруг пуша ослабляем реакцию и судим по коду возврата, а не по stderr.
    $push = Native { git push -q }
    $pushOut = ($push.Out -join "`n").Trim()
    if ($push.Code -ne 0) { throw "git push не прошёл (код $($push.Code)): $pushOut" }
    $myCommit = (git rev-parse HEAD).Trim()
    if ($pushOut) { step 2 "пуш прошёл, git сказал в stderr: $($pushOut -replace '\s+',' ')" }
    step 2 "репозиторий: добавлено $($add.Count), всего $((Get-Content 'src\main-domains.lst' | Where-Object {$_ -ne ''}).Count)"
} else {
    step 2 "репозиторий: все домены уже были"
    $myCommit = $null
}

# --- 3. Дождаться сборки набора - именно НАШЕЙ, а не последней по времени.
# Раньше брался просто самый свежий запуск: пока наш ещё не появился в списке, это был
# вчерашний запуск по расписанию, он давно "completed/success" - скрипт рапортовал об
# успехе, а настоящая сборка только стартовала (и идёт до ~20 минут). Теперь ищем запуск
# по sha нашего коммита и ждём его конца.
if ($myCommit) {
    $runId = $null
    $deadline = (Get-Date).AddMinutes(2)
    while (-not $runId -and (Get-Date) -lt $deadline) {
        $r = Native { gh run list --repo HRYNdev/net-rules --commit $myCommit --event push --limit 1 --json databaseId }
        if ($r.Code -eq 0) {
            $j = ($r.Out -join "`n") | ConvertFrom-Json
            if ($j) { $runId = @($j)[0].databaseId }
        }
        if (-not $runId) { Start-Sleep -Seconds 5 }
    }
    if (-not $runId) { Write-Warning "запуск сборки для коммита $myCommit не появился за 2 минуты"; exit 1 }
    step 3 "жду сборку $runId (коммит $($myCommit.Substring(0,7)))"
    $st = $null
    $deadline = (Get-Date).AddMinutes(30)
    do {
        Start-Sleep -Seconds 10
        $r = Native { gh run view $runId --repo HRYNdev/net-rules --json status,conclusion }
        if ($r.Code -eq 0) { $st = ($r.Out -join "`n") | ConvertFrom-Json }
    } while ((-not $st -or $st.status -ne "completed") -and (Get-Date) -lt $deadline)
    step 3 "сборка: $($st.status)/$($st.conclusion)"
    if ($st.conclusion -ne "success") { Write-Warning "сборка не прошла или не успела за 30 минут, на роутере домен уже работает, но до остальных устройств не доедет"; exit 1 }
} else {
    step 3 "новых коммитов нет, сборку не жду"
}

# --- 4. Забрать свежий набор на раздачу (не ждать 20 минут).
# Повторяем: GitHub отдаёт свежий файл не мгновенно после сборки.
$synced = $false
foreach ($try in 1..4) {
    $vps = (Native { ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@77.239.102.44 "/opt/sync-rules.sh; tail -1 /var/log/sync-rules.log" }).Out
    $last = ($vps | Select-Object -Last 1)
    if ($last -match 'неудач 0') { $synced = $true; step 4 "раздача (попытка $try): $last"; break }
    if ($try -lt 4) { Start-Sleep -Seconds 10 }
}
if (-not $synced) { step 4 "раздача: не обновилась, $last" }

# --- 5. Проверка: домен реально в наборе на раздаче и работает на роутере.
# Многострочный текст НЕ передаём аргументом ssh: PowerShell 5.1 ломает кавычки, а
# переводы строк CRLF оболочка роутера (ash) читает как часть слов - отсюда было
# "ash: syntax error: unexpected word". Скрипт собираем с LF, кодируем в base64 и
# отдаём одной строкой без кавычек и переводов строк; на роутере base64 -d | sh.
$remote = @'
curl -s -o /tmp/v.srs -m 30 https://subkv.chickenkiller.com/rules/main-domains.srs
/usr/bin/sing-box rule-set decompile --output /tmp/v.json /tmp/v.srs 2>/dev/null
for d in __DOMAINS__; do
  inset=$(grep -c "\"$d\"" /tmp/v.json)
  ip=$(nslookup $d 127.0.0.1 2>/dev/null | grep -oE '198\.18\.[0-9.]+' | head -1)
  echo "$d|$inset|${ip:-net}"
done
'@
$remote = ($remote -replace "`r", "") -replace '__DOMAINS__', ($Domains -join ' ')
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remote))
$check = (Native { ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@192.168.10.1 "echo $b64 | base64 -d | sh" }).Out
step 5 "проверка:"
foreach ($line in $check) {
    $p = $line -split '\|'
    if ($p.Count -eq 3) { "        {0}: в наборе={1}, на роутере={2}" -f $p[0], $p[1], $p[2] }
    else { "        $line" }
}
""
"Готово. На роутере работает сразу, на устройствах вне дома - при следующем обновлении набора."
