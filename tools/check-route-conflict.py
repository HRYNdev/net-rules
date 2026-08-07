#!/usr/bin/env python3
"""Ловит противоречие двух своих же списков: домен велено гнать в тоннель,
а собранный ads-набор его режет.

В боевом конфиге правило reject по набору ads стоит ВЫШЕ правила маршрута
(kelevra-box/desktop/final.json: reject ads - позиция 3, маршрут - позиция 4),
поэтому при совпадении домен просто отвергается. Молча: сборка зелёная,
в логе пусто, снаружи выглядит как "сайт не работает".

Проверяются оба слоя маршрута:
  - свои списки в репозитории (src/*-domains.lst);
  - community-наборы, которые клиент тянет с сервера подписки
    (их имена лежат в src/route-sets.txt).

Домены, про которые уже решено "пусть режется" (трекеры и аналитика),
перечислены в src/route-ads-known.txt и на код возврата не влияют - они
только печатаются. Падаем лишь на НОВОМ конфликте: ровно так 30.07.2026
приехали play.googleapis.com и beacons.gvt2.com, из-за которых перестал
открываться Google Play.

Использование: python3 tools/check-route-conflict.py [ads-src.txt] [--offline]
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

ADS_SRC = '/tmp/ads-src.txt'
SUB_BASE = 'https://subkv.chickenkiller.com/rules/community'
TIMEOUT = 30


def pravila_ads(path):
    """Домены из исходника ads в формате AdGuard: ||example.com^ -> example.com"""
    blocked = set()
    for line in open(path, encoding='utf-8'):
        m = re.match(r'^\|\|([^\^/$]+)\^', line.strip())
        if m:
            blocked.add(m.group(1).lower())
    return blocked


def spisok(path):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding='utf-8'):
        s = line.split('#')[0].split('!')[0].strip().lower()
        if s:
            out.append(s)
    return out


def svoi_marshrutnye():
    """(домен, откуда) из своих списков репозитория."""
    out = []
    import glob
    for path in sorted(glob.glob('src/*-domains.lst')):
        for n, line in enumerate(open(path, encoding='utf-8'), 1):
            s = line.strip()
            if s and not s.startswith('#'):
                out.append((s.lower(), '%s:%d' % (path, n)))
    return out


def community_marshrutnye(tmpdir):
    """(домен, откуда) из наборов подписки. Сервер недоступен - не падаем,
    а честно говорим, что этот слой не проверен."""
    imena = spisok('src/route-sets.txt')
    out, propuweno = [], []
    for name in imena:
        srs = os.path.join(tmpdir, name + '.srs')
        js = os.path.join(tmpdir, name + '.json')
        url = '%s/%s.srs' % (SUB_BASE, name)
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                data = r.read()
            open(srs, 'wb').write(data)
            subprocess.run(['sing-box', 'rule-set', 'decompile', '--output', js, srs],
                           check=True, capture_output=True)
            for rule in json.load(open(js))['rules']:
                for key in ('domain', 'domain_suffix'):
                    v = rule.get(key, [])
                    for d in ([v] if isinstance(v, str) else v):
                        out.append((d.lower(), 'подписка/%s' % name))
        except Exception as e:
            propuweno.append('%s (%s)' % (name, type(e).__name__))
    return out, propuweno


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    offline = '--offline' in sys.argv
    ads_src = args[0] if args else ADS_SRC

    blocked = pravila_ads(ads_src)
    known = set(spisok('src/route-ads-known.txt'))

    routed = svoi_marshrutnye()
    propuweno = []
    if offline:
        propuweno = ['слой подписки не проверялся: запуск с --offline']
    else:
        with tempfile.TemporaryDirectory() as tmp:
            comm, propuweno = community_marshrutnye(tmp)
        routed += comm

    print('маршрутных доменов %d, правил в ads %d, известных пересечений %d'
          % (len(routed), len(blocked), len(known)))
    if propuweno:
        print('НЕ ПРОВЕРЕНО: ' + ', '.join(propuweno))

    staroe, novoe = [], []
    for d, otkuda in routed:
        chasti = d.split('.')
        for i in range(len(chasti)):
            b = '.'.join(chasti[i:])
            if b in blocked:
                (staroe if d in known else novoe).append((d, b, otkuda))
                break

    if staroe:
        print('')
        print('Известные пересечения (решено: пусть режется) - %d:' % len(staroe))
        for d, b, otkuda in sorted(set(staroe)):
            print('  %-34s %-24s режет ||%s^' % (d, otkuda, b))

    if not novoe:
        print('')
        print('новых конфликтов нет')
        return 0

    print('')
    print('КОНФЛИКТ: домен велено гнать в тоннель, а свой же ads-набор его режет.')
    print('В конфиге reject по ads стоит выше маршрута, значит домен отвергается.')
    for d, b, otkuda in sorted(set(novoe)):
        print('  %-34s %-24s режет ||%s^' % (d, otkuda, b))
    print('')
    print('Лечение: если домен нужен - строка в src/ads-allow.txt;')
    print('если резать правильно - строка в src/route-ads-known.txt.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
