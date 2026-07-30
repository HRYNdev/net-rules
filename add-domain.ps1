# Добавляет домен(ы) в сеть: сразу на роутере, следом везде.
# Использование: .\add-domain.ps1 example.com [ещё.com ...]
param([Parameter(Mandatory=$true, ValueFromRemainingArguments=$true)][string[]]$Domains)

$ErrorActionPreference = "Stop"
$repo = "D:\Projects\apps\NetRules"
$t0 = Get-Date
function step($n, $t) { "[{0,5:N1}с] {1}" -f ((Get-Date)-$t0).TotalSeconds, $t }

Set-Location $repo

# --- 1. Мгновенно на роутере: срочный набор + перечитка конфига без перезапуска
$piCmd = "/root/urgent/add-domain.sh " + ($Domains -join " ")
$pi = ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@192.168.10.1 $piCmd 2>&1
step 1 "роутер: $($pi -join '; ')"

# --- 2. В репозиторий: чтобы доехало до всех устройств и осталось навсегда
$cur = Get-Content "src\main-domains.lst" | Where-Object { $_ -ne '' }
$add = $Domains | Where-Object { $cur -notcontains $_ }
if ($add) {
    (($cur + $add) | Sort-Object -Unique) | Out-File "src\main-domains.lst" -Encoding utf8
    git add -A | Out-Null
    git -c user.name="HRYNdev" -c user.email="vladimirshev10@gmail.com" commit -q -m "добавлены домены: $($add -join ', ')" | Out-Null
    git push -q 2>&1 | Out-Null
    step 2 "репозиторий: добавлено $($add.Count), всего $((Get-Content 'src\main-domains.lst' | Where-Object {$_ -ne ''}).Count)"
} else {
    step 2 "репозиторий: все домены уже были"
}

# --- 3. Дождаться сборки набора
$runId = (gh run list --repo HRYNdev/net-rules --limit 1 --json databaseId | ConvertFrom-Json).databaseId
$deadline = (Get-Date).AddMinutes(4)
do {
    Start-Sleep -Seconds 8
    $st = (gh run view $runId --repo HRYNdev/net-rules --json status,conclusion | ConvertFrom-Json)
} while ($st.status -ne "completed" -and (Get-Date) -lt $deadline)
step 3 "сборка: $($st.status)/$($st.conclusion)"
if ($st.conclusion -ne "success") { Write-Warning "сборка не прошла, на роутере домен уже работает, но до остальных устройств не доедет"; exit 1 }

# --- 4. Забрать свежий набор на раздачу (не ждать 20 минут)
$vps = ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@77.239.102.44 "/opt/sync-rules.sh; tail -1 /var/log/sync-rules.log" 2>&1
step 4 "раздача: $($vps | Select-Object -Last 1)"

# --- 5. Проверка: домен реально в наборе на раздаче и работает на роутере
$check = ssh -o StrictHostKeyChecking=no root@192.168.10.1 @"
curl -s -o /tmp/v.srs -m 30 https://subkv.chickenkiller.com/rules/main-domains.srs
/usr/bin/sing-box rule-set decompile --output /tmp/v.json /tmp/v.srs 2>/dev/null
for d in $($Domains -join ' '); do
  inset=`$(grep -c "\`"`$d\`"" /tmp/v.json)
  ip=`$(nslookup `$d 127.0.0.1 2>/dev/null | grep -oE '198\.18\.[0-9.]+' | head -1)
  echo "  `$d: в наборе=`$inset, на роутере=`${ip:-нет}"
done
"@ 2>&1
step 5 "проверка:"
$check | ForEach-Object { "        $_" }
""
"Готово. На роутере работает сразу, на устройствах вне дома - при следующем обновлении набора."
