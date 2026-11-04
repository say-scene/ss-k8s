# Grafana 대시보드 백업

UI 로 만든 대시보드를 JSON 으로 남긴다. `grafana.db` 는 기본 노드의 `/var/lib/grafana` 에 있는 `local` PV 라 **EC2 를 지우면 같이 사라진다.** `Retain` 은 PVC 삭제만 막아 준다.

| 파일 | 대시보드 | uid |
|---|---|---|
| `ss-service.json` | ss-service-dashboard-패널 추가 | `adtknr5` |

`ss-stt` 와 `Unified Dashboard` 는 아직 받지 않았다.

## 복구

```
Dashboards → New → Import → Upload dashboard JSON file → Import
```

같은 `uid` 가 있으면 덮어쓸지 묻는다. 데이터소스는 묻지 않는다 — JSON 안의 `uid: prometheus` 가 차트 고정값이라 재설치해도 맞는다.

ConfigMap 으로 자동 적용할 수도 있다. `grafana/values.yaml` 의 sidecar 가 `grafana_dashboard` 라벨을 보고 있다.

```sh
kubectl -n monitoring create configmap grafana-dashboard-ss-service --from-file=ss-service.json
kubectl -n monitoring label configmap grafana-dashboard-ss-service grafana_dashboard=1
```

**이 ConfigMap 은 저장소에 넣지 않는다.** 넣으면 UI 에서 고친 것이 Sync 때마다 되돌아간다. 패널이 더 바뀌지 않게 되면 그때 넣는다.

## 백업 갱신

```
Grafana → 대시보드 → Export ▾ → Export as code
  Model   Classic   V2 Resource 는 실험 스키마라 안 된다
  Format  JSON
  Share dashboard with another instance   끈다. 켜면 __inputs 가 붙어 sidecar 가 못 읽는다
→ Download file
```

받은 파일을 그대로 덮어쓰지 않는다. `id`·`version`·에디터 찌꺼기를 빼고 키를 정렬해야 diff 에 바뀐 패널만 보인다.

```sh
python3 clean-dashboard.py ~/Downloads/<받은파일>.json ss-service.json
```

## 알려진 문제

| 패널 | 문제 |
|---|---|
| GPU VRAM 예약률 | 두 선의 단위가 다르다(0~100 vs 0~1). `vllm:gpu_cache_usage_perc` 가 현재 버전에 있는지도 확인하지 않았다 |
| 응답시간 비교 — 사용자 vs 모델 | ss-be 와 vLLM 의 모집단이 달라 순서가 뒤집혀 보인다 |
| 전체 요청수 | 누적 카운터를 그대로 그려 계속 우상향한다 |

지표 해석은 [부하 분석 기록](https://github.com/say-scene/ss-docs/blob/main/monitoring/vllm-load-analysis-v1.md) 에 있다.
