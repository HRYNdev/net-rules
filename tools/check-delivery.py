#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сверяет, что правила доехали по всей цепи: исходник в репозитории ->
ассеты релиза latest на GitHub -> раздача, которую реально тянет роутер
(subkv.chickenkiller.com). Синк на VPS при неудаче молча оставляет старый
файл - расхождение между точками никак не видно снаружи, пока кто-то не
сверит их вручную. Этот инструмент делает такую сверку и не молчит: любое
расхождение или недоступная точка - ненулевой код возврата.

Сравнение точное по символам (без strip невидимых символов, без смены
регистра) - именно так ловится, например, BOM (U+FEFF), прилипший к началу
имени домена и не совпадающий ни с одним запросом.

Использование:
  python3 tools/check-delivery.py [--src-dir DIR] [--offline] [--json]
"""
import argparse
import json
import sys
import unicodedata
import urllib.error
import urllib.request

RELEASE_BASE = 'https://github.com/HRYNdev/net-rules/releases/latest/download'
RAZDACHA_BASE = 'https://subkv.chickenkiller.com/rules'
TIMEOUT = 25

TOCHKI = ('domains', 'subnets')
IMENA_FAILOV = {
    'domains': 'main-domains',
    'subnets': 'main-subnets',
}


class OshibkaTochki(Exception):
    """Точку не удалось получить или разобрать - это не 'расхождение',
    а отдельная беда: не смогли проверить."""


def kod_podozritelnyh(s):
    """Невидимые/непечатаемые символы строки в виде U+XXXX, чтобы их
    было видно в консоли."""
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if ch == '﻿' or cat in ('Cf', 'Cc') or (cat == 'Zs' and ch != ' '):
            out.append('U+%04X' % ord(ch))
    return out


def opisat(s):
    podozr = kod_podozritelnyh(s)
    if podozr:
        return '%r [%s]' % (s, ', '.join(podozr))
    return repr(s)


def iz_lst_faila(path):
    """Строки src/*.lst: одна запись на строку, '#'-комментарии и пустые
    строки пропускаются. Сам текст записи не трогаем ни strip'ом невидимых
    символов, ни сменой регистра - только отрезаем перевод строки."""
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    out = set()
    for line in text.splitlines():
        proverka = line.strip()
        if not proverka or proverka.startswith('#'):
            continue
        out.add(line)
    return out


def obojti_json(node, out):
    """Рекурсивно собрать все строковые листья структуры json - обёртка
    вида {"version":..,"rules":[{"domain_suffix":[...]}]} нам заранее
    неизвестна дословно, поэтому не полагаемся на конкретные ключи."""
    if isinstance(node, str):
        out.add(node)
    elif isinstance(node, dict):
        for v in node.values():
            obojti_json(v, out)
    elif isinstance(node, list):
        for v in node:
            obojti_json(v, out)


def vzyat_url(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'check-delivery.py'})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = r.read()
    except urllib.error.URLError as e:
        raise OshibkaTochki('%s: сеть/HTTP (%s)' % (url, e))
    except OSError as e:
        raise OshibkaTochki('%s: сеть (%s)' % (url, e))
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as e:
        raise OshibkaTochki('%s: не utf-8 (%s)' % (url, e))
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise OshibkaTochki('%s: битый json (%s)' % (url, e))
    out = set()
    obojti_json(parsed, out)
    return out


def vzyat_tochki(src_dir, offline, override_urls=None):
    """Возвращает {(kind, tochka): set() либо ('__error__', text)}.
    kind: 'domains' | 'subnets'; tochka: 'istochnik' | 'vypusk' | 'razdacha'."""
    urls = {
        ('domains', 'vypusk'): '%s/main-domains.json' % RELEASE_BASE,
        ('subnets', 'vypusk'): '%s/main-subnets.json' % RELEASE_BASE,
        ('domains', 'razdacha'): '%s/main-domains.json' % RAZDACHA_BASE,
        ('subnets', 'razdacha'): '%s/main-subnets.json' % RAZDACHA_BASE,
    }
    if override_urls:
        urls.update(override_urls)

    tochki = {}
    oshibki = []

    if not offline:
        for kind in TOCHKI:
            path = '%s/%s.lst' % (src_dir, IMENA_FAILOV[kind])
            try:
                tochki[(kind, 'istochnik')] = iz_lst_faila(path)
            except OSError as e:
                oshibki.append('исходник %s: не удалось прочитать (%s)' % (path, e))

    for (kind, tochka), url in urls.items():
        try:
            tochki[(kind, tochka)] = vzyat_url(url)
        except OshibkaTochki as e:
            oshibki.append('%s/%s: %s' % (kind, tochka, e))

    return tochki, oshibki


def sravnit(imya_a, a, imya_b, b):
    """Возвращает (совпадает?, список_строк_отчёта)."""
    only_a = a - b
    only_b = b - a
    stroki = []
    stroki.append('%s: %d записей, %s: %d записей' % (imya_a, len(a), imya_b, len(b)))
    if not only_a and not only_b:
        stroki.append('  совпадают полностью')
        return True, stroki
    if only_a:
        stroki.append('  есть в %s, нет в %s (%d):' % (imya_a, imya_b, len(only_a)))
        for s in sorted(only_a):
            stroki.append('    %s' % opisat(s))
    if only_b:
        stroki.append('  есть в %s, нет в %s (%d):' % (imya_b, imya_a, len(only_b)))
        for s in sorted(only_b):
            stroki.append('    %s' % opisat(s))
    return False, stroki


NAZVANIYA_TOCHEK = {
    'istochnik': 'исходник (src/*.lst)',
    'vypusk': 'выпуск (релиз latest)',
    'razdacha': 'раздача (subkv)',
}


def main():
    p = argparse.ArgumentParser(description='Сверка доставки правил по цепи исходник -> выпуск -> раздача')
    p.add_argument('--src-dir', default='src', help='путь к каталогу src (по умолчанию src рядом с репозиторием)')
    p.add_argument('--offline', action='store_true', help='сверять только выпуск и раздачу, без исходника')
    p.add_argument('--json', action='store_true', help='машинный вывод')
    args = p.parse_args()

    tochki, oshibki = vzyat_tochki(args.src_dir, args.offline)

    cepochka = [('vypusk', 'razdacha')] if args.offline else [('istochnik', 'vypusk'), ('vypusk', 'razdacha')]

    otchet = {'kinds': {}, 'oshibki': oshibki}
    vse_sovpalo = True
    tekst = []

    for kind in TOCHKI:
        tekst.append('=== %s ===' % kind)
        otchet['kinds'][kind] = {'pary': [], 'oshibki_tochek': []}
        for a, b in cepochka:
            key_a, key_b = (kind, a), (kind, b)
            if key_a not in tochki or key_b not in tochki:
                nedostupno = [NAZVANIYA_TOCHEK[x] for x, y in ((a, key_a), (b, key_b)) if y not in tochki]
                tekst.append('%s <-> %s: не проверено, точка недоступна (см. ошибки выше)'
                              % (NAZVANIYA_TOCHEK[a], NAZVANIYA_TOCHEK[b]))
                otchet['kinds'][kind]['oshibki_tochek'].append('%s<->%s' % (a, b))
                continue
            sovpalo, stroki = sravnit(NAZVANIYA_TOCHEK[a], tochki[key_a], NAZVANIYA_TOCHEK[b], tochki[key_b])
            tekst.extend(stroki)
            otchet['kinds'][kind]['pary'].append({'a': a, 'b': b, 'sovpalo': sovpalo})
            if not sovpalo:
                vse_sovpalo = False
        tekst.append('')

    if oshibki:
        tekst.append('НЕ ПРОВЕРЕНО (ошибка получения точки):')
        for e in oshibki:
            tekst.append('  %s' % e)

    if args.json:
        print(json.dumps(otchet, ensure_ascii=False, indent=2))
    else:
        print('\n'.join(tekst).rstrip())

    if oshibki:
        return 2
    if not vse_sovpalo:
        return 1
    if not args.json:
        print('цепь цела: исходник, выпуск и раздача совпадают' if not args.offline
              else 'выпуск и раздача совпадают')
    return 0


if __name__ == '__main__':
    sys.exit(main())
