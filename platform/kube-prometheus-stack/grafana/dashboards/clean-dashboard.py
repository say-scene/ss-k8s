#!/usr/bin/env python3
"""Grafana 가 내보낸 대시보드 JSON 을 저장소에 넣을 형태로 정리한다.

    python3 clean-dashboard.py <받은파일> <저장할파일>

id·version 은 그 Grafana 안에서만 의미가 있어 다른 인스턴스에 넣을 때 충돌한다.
targets 의 key 는 에디터 세션 찌꺼기다.
키를 정렬해 두어야 다시 내보내 덮어쓸 때 diff 에 바뀐 패널만 보인다.
"""
import json
import sys

src, dst = sys.argv[1], sys.argv[2]
d = json.load(open(src, encoding='utf-8'))
d.pop('id', None)
d.pop('version', None)
for panel in d.get('panels', []):
    for target in panel.get('targets', []):
        target.pop('key', None)
with open(dst, 'w', encoding='utf-8') as f:
    json.dump(d, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write('\n')
print(f"panels={len(d.get('panels', []))} uid={d.get('uid')} title={d.get('title')}")
