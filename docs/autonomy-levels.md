# Autonomy Levels — L0~L5

> ForgeKit executor 가 가질 수 있는 자율 등급. 각 레벨은 **허용 작업**과 **승인 필요 작업**을
> 나눈다. 정책 판정은 `packages/forge-policy`(예정)와 기존 [approval-matrix.md](approval-matrix.md) /
> [autonomy-policy.md](autonomy-policy.md) 가 SSoT.

| Level | 이름 | 허용(자동) | 승인 필요 |
| --- | --- | --- | --- |
| **L0** | Manual Assistant | 제안·설명·초안만 (사람이 직접 실행) | 모든 실제 행위 |
| **L1** | Read-only Operator | 읽기(파일/상태/로그/검색) | 모든 쓰기 |
| **L2** | Workspace Operator | workspace 내 파일 생성/수정, 로컬 테스트 실행 | workspace 밖 쓰기, 네트워크 변경 |
| **L3** | Project Operator | 브랜치 생성, 다중 파일 변경, 빌드/테스트 | 외부 영향(배포/외부 API 쓰기), git push |
| **L4** | Git Operator | commit·push·PR 생성 (작업 브랜치) | **merge**, protected branch 직접 쓰기, release/tag |
| **L5** | DevOps Operator | 배포 파이프라인 트리거, 인프라 작업 | 프로덕션 배포, 민감 자격 접근, 파괴적 인프라 변경 |

## 원칙

- **상위 레벨은 하위 권한을 포함**하되, 각 레벨의 "승인 필요" 경계는 항상 사람 게이트로 남는다.
- **merge·release·프로덕션·민감 자격**은 어떤 레벨에서도 자동화하지 않는다(human owner 전용).
- executor 별 자율 등급은 도구 평가([tool-evaluation.md](tool-evaluation.md))와 policy 가 함께 결정한다.
- fake 실행/권한 상향 위장 금지 — 실제 보유 권한만 행사한다.
