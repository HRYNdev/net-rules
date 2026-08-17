#!/usr/bin/env python3
"""Ловит строку исходника, которая выглядит как домен, но доменом не является.

Такая строка проходит весь конвейер молча: она уезжает в .json, компилируется в
.srs, доезжает до роутера и телефонов — и не совпадает ни с одним реальным
запросом, потому что в SNI и в DNS приходит чистое имя. Снаружи это выглядит как
"домен в списке есть, а сайт мимо туннеля".

Почему этого не ловил шаг «Проверить наборы». Он сверяет исходник с набором и
требует, чтобы каждая строка исходника попала в набор. Битая строка туда попадает
— битой. Сверка битого с битым сходится, и проверка честно печатает "покрыты все
258 записей".

Так 30.07.2026 в src/main-domains.lst приехал addyosmani.com с невидимым BOM
(U+FEFF) в начале строки: файл был записан целиком в UTF-8 с меткой порядка байт,
а первой строкой по алфавиту оказался как раз он. add-domain.ps1 метку не срезает
(Get-Content отдаёт её как часть текста), сортировка увела строку в конец файла,
и в релизе latest 14 суток пролежал домен "﻿addyosmani.com". Его же не видит
и check-route-conflict.py: там строка тоже берётся как есть.

Проверяется то, что уходит в наборы:
  src/*-domains.lst — имя хоста;
  src/*-subnets.lst — подсеть CIDR.
Пустые строки и строки с # игнорируются, как и в сборке.

Использование: python3 tools/check-src-names.py
"""
import glob
import ipaddress
import re
import sys
import unicodedata

# Имя хоста: только ASCII-строчные, метки 1..63, без ведущего и хвостового дефиса.
# Заглавные не разрешаем сознательно: в SNI и в DNS-запросе имя приходит в нижнем
# регистре, а sing-box сравнивает суффикс строкой — "GitHub.com" в наборе не
# совпадёт ни с чем и будет такой же тихой дырой, как BOM.
IMYA = re.compile(r'^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$')


def pochemu(s):
    """Человеческая причина, по которой строка не является именем хоста."""
    nevidimye = [c for c in s if unicodedata.category(c) in ('Cf', 'Zs', 'Cc')]
    if nevidimye:
        imena = ', '.join('U+%04X (%s)' % (ord(c), unicodedata.name(c, 'без имени'))
                          for c in dict.fromkeys(nevidimye))
        return 'невидимые символы: ' + imena
    if not s.isascii():
        chuzhie = ', '.join('%s U+%04X' % (c, ord(c)) for c in dict.fromkeys(s) if not c.isascii())
        return ('не-ASCII символы: ' + chuzhie +
                ' — либо это омоглиф (читается латиницей, а байты другие), '
                'либо честное национальное имя: тогда нужен его punycode (xn--…)')
    if s != s.lower():
        return 'заглавные буквы: в SNI и DNS имя приходит строчным, суффикс не совпадёт'
    if '/' in s or ':' in s:
        return 'это не имя хоста, а адрес со схемой или путём — нужен только хост'
    if s.startswith('.') or s.endswith('.') or '..' in s:
        return 'пустая метка (лишняя точка)'
    if '.' not in s:
        return 'нет точки — домен верхнего уровня целиком в туннель не отправляем'
    if '_' in s:
        return 'подчёркивание: в именах хостов его нет'
    return 'недопустимые символы для имени хоста'


def stroki(path):
    """(номер, строка) — ровно то, что сборка считает записью."""
    for n, line in enumerate(open(path, encoding='utf-8'), 1):
        s = line.rstrip('\n').rstrip('\r')
        if not s.strip() or s.strip().startswith('#'):
            continue
        yield n, s


def main():
    bedy = []
    fajlov = 0

    for path in sorted(glob.glob('src/*-domains.lst')):
        fajlov += 1
        vsego = 0
        for n, s in stroki(path):
            vsego += 1
            if not IMYA.fullmatch(s):
                bedy.append((path, n, s, pochemu(s)))
        print('%s: записей %d' % (path, vsego))

    for path in sorted(glob.glob('src/*-subnets.lst')):
        fajlov += 1
        vsego = 0
        for n, s in stroki(path):
            vsego += 1
            try:
                ipaddress.ip_network(s.strip())
                if s != s.strip():
                    raise ValueError('лишние пробелы по краям строки')
            except ValueError as e:
                bedy.append((path, n, s, 'не подсеть CIDR: %s' % e))
        print('%s: записей %d' % (path, vsego))

    if not fajlov:
        print('НЕ ПРОВЕРЕНО: не найдено ни одного src/*-domains.lst или src/*-subnets.lst')
        return 1

    if not bedy:
        print('')
        print('все записи — набираемые имена и подсети')
        return 0

    print('')
    print('НЕНАБИРАЕМАЯ ЗАПИСЬ: строка уедет в набор и не совпадёт ни с чем.')
    print('Сборка при этом зелёная — сверка исходника с набором сходится, оба битые.')
    for path, n, s, prichina in bedy:
        print('  %s:%d' % (path, n))
        print('      как лежит: %r' % s)
        print('      беда:      %s' % prichina)
    print('')
    print('Лечение: привести строку к чистому имени хоста (только ASCII, строчные).')
    return 1


if __name__ == '__main__':
    sys.exit(main())
