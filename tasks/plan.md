# ss-be AWS Ingress 노출 계획

상태: **제안만 함. 코드는 아직 바꾸지 않았다.**

## 현재 상태 (확인한 사실)

- `apps/ss-be/06_ingress.yaml`은 ArgoCD용 Ingress다 (`name: argocd`, `namespace: argocd`, `/argocd` → `argocd-server:80`).
- 이 파일이 `apps/ss-be/kustomization.yaml`의 `resources`에 들어가 있다(커밋 `d70b969`). 그래서 ss-be ArgoCD Application이 **argocd 네임스페이스의 ArgoCD Ingress까지 관리하게 된다**.
- ss-be API는 전부 `/api/v1/...` 아래에 있다 (`/api/v1/demo/*`, `/api/v1/stt`).
- ss-fe는 상대 경로 `/api/...`로 호출한다(vite dev proxy 기준). 그래서 FE와 BE가 같은 ALB 아래에서 경로로 나뉘면 CORS 설정이 필요 없다.
- ss-be Service: `ss-be:80` (port name `http`) → pod `8080`. readiness는 `/actuator/health/readiness`이고, Redis가 끊기면 503을 준다.
- ss-be에는 아직 cookie/CORS 코드가 없다. `SESSION_COOKIE_SECURE`는 지금 쓰이지 않는다.

## 문제점

1. **ArgoCD Ingress가 ss-be 앱에 섞여 있다.** ss-be 앱을 삭제하거나 prune하면 ArgoCD Ingress도 같이 지워져 ArgoCD UI 접속이 끊길 수 있다. 다른 곳에서 이미 이 Ingress를 관리하고 있다면 두 소유자가 서로 덮어쓰게 된다.
2. ~~`defaultBackend: deny`에 짝이 되는 `actions.deny` annotation이 없다.~~ → **`defaultBackend`를 제거하여 해결했다.** 이제 매칭되지 않는 경로는 ALB 기본 404로 응답하고, 사용자에게 보여 줄 에러 페이지는 CloudFront 에러 정책이 처리한다.
3. ss-be를 외부로 내보내는 Ingress가 아직 없다.

## 제안 변경

### A. ArgoCD Ingress를 ss-be 앱에서 분리한다

전제: ArgoCD는 이미 클러스터에 떠 있다. ArgoCD 자체는 CD 대상이 아니며, 초기화 때만 필요하다. 그래서 다른 팀원이 repo root의 `init/` 폴더에서 관리하고 수동으로 적용한다(ArgoCD sync 대상이 아님).

- ss-be 쪽: `06_ingress.yaml`의 ArgoCD Ingress 내용을 지우고 ss-be Ingress로 바꾼다(B). ArgoCD Ingress 파일은 팀원의 `init/`으로 넘긴다.
- ss-be 앱이 `d70b969` 이후 이미 sync되었다면, ArgoCD Ingress에 ss-be 앱의 tracking label/annotation(`app.kubernetes.io/instance` 또는 `argocd.argoproj.io/tracking-id`)이 붙어 있을 수 있다. 이 상태에서 git에서만 빼면 prune 대상이 되어 Ingress가 지워진다. 따라서 **sync 전에 tracking 표시를 떼거나 prune을 끈 상태로 sync한다.** (실제로 sync되었는지는 확인하지 못했다. `kubectl -n argocd get ingress argocd --show-labels`로 확인한다.)
- ArgoCD Ingress에서 `defaultBackend`(deny)를 제거했다. `init/`으로 옮길 때도 이 상태를 유지한다. group 안 어느 Ingress에도 `defaultBackend`를 두지 않는다.

### B. `apps/ss-be/06_ingress.yaml`을 ss-be Ingress로 바꾼다

같은 ALB(`group.name: sayscene-public`)를 공유하면서 `/api` 경로만 ss-be로 보낸다.

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ss-be
  labels:
    app: ss-be
  # namespace는 적지 않는다. ArgoCD Application의 destination namespace를 따른다.
  annotations:
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
    # ArgoCD와 같은 ALB를 공유한다. order는 group 안에서 겹치지 않게 한다.
    alb.ingress.kubernetes.io/group.name: sayscene-public
    alb.ingress.kubernetes.io/group.order: "20"
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTP":80}]'
    alb.ingress.kubernetes.io/security-group-prefix-lists: pl-22a6434b
    alb.ingress.kubernetes.io/backend-protocol: HTTP
    alb.ingress.kubernetes.io/healthcheck-protocol: HTTP
    # target-type ip라서 pod 8080에 직접 검사한다. Redis 장애 시 503으로 빠진다.
    alb.ingress.kubernetes.io/healthcheck-path: /actuator/health/readiness
    alb.ingress.kubernetes.io/success-codes: "200"
spec:
  ingressClassName: alb
  rules:
    - http:
        paths:
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: ss-be
                port:
                  name: http
```

설계 포인트:
- `/actuator`는 `/api` 밖에 있으므로 외부에 노출되지 않는다. ALB health check는 target(pod)에 직접 붙으므로 문제없다.
- `listen-ports`와 `security-group-prefix-lists`는 ArgoCD Ingress와 같은 값으로 맞춘다. group 안에서 listener 설정이 다르면 충돌한다.
- `host` 규칙은 넣지 않았다. 도메인이 정해지면 그때 추가한다.

## 열린 질문 / 위험

| 항목 | 내용 | 기본 가정 |
|---|---|---|
| ArgoCD Ingress 소유처 | 팀원이 root `init/`에서 수동으로 관리한다(확정) | ss-be 앱에서는 제거만 한다 |
| `pl-22a6434b` | 어떤 prefix list인가? CloudFront origin-facing이면 외부 트래픽은 CloudFront를 거쳐야만 들어온다 | ArgoCD와 같은 값을 쓴다 |
| 타임아웃 | ALB idle timeout 기본값은 60s인데 `STT_TIMEOUT_SECONDS`는 120s다. CloudFront를 앞에 두면 origin 응답 타임아웃 기본값은 30s다 | 이번 범위 밖. 긴 STT 요청에서 504가 나면 `load-balancer-attributes: idle_timeout.timeout_seconds=...`를 group 안 **한 Ingress에만** 추가한다 |
| HTTPS | listener가 HTTP:80뿐이다 | TLS는 CloudFront/ACM 단계에서 따로 처리한다 |
| ss-fe `/` 라우팅 | FE가 같은 ALB의 `/`로 붙어야 상대 경로 `/api`가 동작한다 | 별도 작업 |

## 롤백

- ss-be Ingress: `06_ingress.yaml`을 되돌리고 ArgoCD sync를 하면 ALB에서 `/api` rule과 target group만 빠진다. ALB 자체는 ArgoCD Ingress가 남아 있으므로 유지된다.
- ArgoCD Ingress 분리: ss-be 앱에서 실수로 prune되면 `init/`의 Ingress를 `kubectl apply -f`로 다시 적용해 복구한다. ArgoCD UI가 Ingress로만 노출되어 있다면 복구는 `kubectl`로 해야 한다.
