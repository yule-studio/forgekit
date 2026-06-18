# Runbook — bkurs-be 배포

- **area**: deploy
- **왜 막혔나**: 프로덕션 배포는 forgekit 의 자율 범위 밖 (approval matrix: production action).
- **맥락**: bkurs

## operator 가 제공/결정해야 할 것
- [ ] 배포 대상 환경/리전
- [ ] rollout 전략(blue-green/canary) 승인
- [ ] 롤백 기준

## Terraform / ops skeleton
```hcl
resource "null_resource" "deploy" { # CI/CD 파이프라인 트리거로 대체 }
```

## 승인 / 다음 단계
- operator 가 위 절차로 직접 수행 후 forgekit 에 결과를 알려주세요.
- 승인 경로: `#승인-대기` 카드 (request_type=ACCESS/SECRET) — approval matrix 참조.
