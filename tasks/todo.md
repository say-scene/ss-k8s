# ss-be Ingress TODO

상세는 `tasks/plan.md` 참고.

## Phase 1 — ArgoCD Ingress 분리

- [x] **T1. ArgoCD Ingress를 ss-be 앱에서 뺀다** (ArgoCD Ingress는 팀원이 root `init/`에서 수동으로 관리)
  - 사전 확인: `kubectl -n argocd get ingress argocd --show-labels`로 ss-be 앱의 tracking 표시가 있는지 본다. 있으면 sync 전에 떼거나 prune을 끈다
  - [x] ArgoCD Ingress에서 `defaultBackend`(deny)를 제거했다. 에러 응답은 CloudFront 에러 정책이 처리한다
  - 수용 기준: ArgoCD Ingress는 `init/`에서만 관리되고, `/argocd` 접속이 유지된다
  - 검증: `kubectl kustomize apps/ss-be`에 `namespace: argocd` 리소스가 없다 / `kubectl -n argocd describe ingress argocd`에 에러 이벤트가 없다 / `curl -I http://<ALB>/argocd`
  - ⚠️ 클러스터 적용은 승인이 필요하다(인프라 변경)

**Checkpoint 1:** ArgoCD UI 접속이 정상이고 ALB 이벤트에 에러가 없다.

## Phase 2 — ss-be 노출

- [x] **T2. `apps/ss-be/06_ingress.yaml`을 ss-be Ingress로 작성한다** (T1 이후)
  - 변경: `/api` Prefix → `ss-be:http`, `group.name: sayscene-public`, `group.order: "20"`, healthcheck `/actuator/health/readiness`
  - 수용 기준: kustomize 출력에 Ingress `ss-be`가 namespace 없이 포함된다
  - 검증: `kubectl kustomize apps/ss-be` / `kubectl apply --dry-run=server -k apps/ss-be -n <ss-be ns>`

- [ ] **T3. ArgoCD sync 후 외부 경로를 확인한다** (T2 이후, 승인 필요)
  - 수용 기준: ALB에 `/api*` rule이 생기고 target group이 healthy 상태다
  - 검증: `aws elbv2 describe-target-health ...` / `curl -X POST -H 'Content-Type: application/json' -d '{"message":"hi"}' http://<ALB>/api/v1/demo/echo` → 200 / `curl http://<ALB>/actuator/health` → 404 (외부에 노출되지 않음)

**Checkpoint 2:** echo API 200, ArgoCD UI 정상, `/actuator`는 외부 차단.

## 보류 (필요해지면)

- [ ] ALB idle timeout을 STT 120s에 맞춘다 (504가 실제로 관측되면)
- [ ] host/도메인 규칙, HTTPS listener
- [ ] ss-fe `/` 라우팅
