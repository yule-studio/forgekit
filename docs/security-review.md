# Security Review — cross-cutting 보안 검토 워크플로 (SSoT)

> 본 문서는 `engineering-agent / security-engineer` (cross-cutting 리뷰 게이트)의
> **사람용 SSoT** 다. 역할 계약 코드 SSoT 는
> [`agents/engineering-agent/security-engineer/manifest.json`](../agents/engineering-agent/security-engineer/manifest.json) +
> [`CLAUDE.md`](../agents/engineering-agent/security-engineer/CLAUDE.md).
> 거버넌스/승인은 [`approval-matrix.md`](approval-matrix.md) ·
> [`autonomy-policy.md`](autonomy-policy.md) · [`engineering-agent-governance.md`](engineering-agent-governance.md)
> 와 cross-link.

## 1. 왜 별도 주체인가 (구조 선택)

보안은 backend / frontend / devops / AI 를 **가로지르는 cross-cutting concern** 이다.
각 역할이 자기 도메인 보안을 1차로 보지만, 도메인 경계를 넘는 위협(서버는 검증하는데
프론트가 토큰을 노출, CI 가 과다 권한, agent tool 이 승인 게이트 우회)은 단일 도메인
리뷰에서 새기 쉽다.

**1차 rollout 은 `security-agent` 별도 부서가 아니라 `engineering-agent/security-engineer`
역할이다.** 이유:

- 현재 context loader / grant table / role manifest / council runtime 이 모두
  engineering-agent 부서 + role 멤버 구조 위에 서 있다. 역할로 도입하면 기존 계약을
  재사용하며 **최소 변경**으로 end-to-end 가 닫힌다.
- 별도 부서는 org-chart SSoT(8 부서 고정, `test_slash_command_grants` 가 정확 집합을 핀)
  변경 + 새 manifest + runner pool + grant 부서 추가를 요구한다 — 더 큰 변경이고 이번
  슬라이스 범위를 넘는다.
- **단, 이 역할은 7-role deliberation council 의 8번째 seat 가 아니다.** council registry
  (`role_profiles_data.py`, `test_registry_covers_seven_engineering_roles` 가 고정)는
  그대로 두고, security-engineer 는 manifest 의 `cross_cutting_reviewers` 에 둔다 — 매
  토의에 항상 들어가는 게 아니라 **특정 변경 유형에서만 끼어드는 게이트**다.
- 장기적으로 root `CLAUDE.md`/`agents/engineering-agent/CLAUDE.md` 의 org diagram 이 예고한
  `(future) security-agent` 부서로 승격 가능 — 마이그레이션 경로는 §7.

## 2. 언제 끼어드는가 (intercept triggers)

다음 변경 유형 중 하나라도 해당하면 security-engineer 리뷰가 **필수 게이트**로 들어온다.
코드 SSoT 는 manifest 의 `intercept_triggers`.

| 트리거 | 예시 |
| --- | --- |
| auth / authz 변경 | 로그인, 권한 검사, 토큰 발급/검증, 세션 전략 |
| secret handling 변경 | `.env`, key, credential, secret store 접근/저장 |
| public surface 추가 | 새 endpoint / route / webhook / upload / postMessage 핸들러 |
| deployment / security-sensitive | CI/CD 권한, container/image, 의존성(supply-chain), env/config 노출 |
| agent 경계 변경 | tool grant 추가, approval gate / autonomy boundary 수정 |
| 명시 요청 | `/security-review` 슬래시 또는 운영자 요청 |

트리거가 없으면 게이트는 **열리지 않는다** — 모든 작업에 보안 리뷰를 강제하지 않는다
(noise 방지). 트리거 판정은 tech-lead 의 intake/triage 에서 1차로 내린다.

## 3. Inputs / Outputs / Boundaries

### Inputs
- 변경 대상 역할의 산출물(RoleDraft / diff / API 계약 / UI 흐름 / 배포 계획 / tool grant)
- tech-lead 의 작업 분해 + intercept 사유
- ResearchPack 의 보안 문서 / CVE / 공식 권고
- 현재 grant table + approval matrix

### Outputs
- 도메인별 finding: `[severity] 위치 — 무엇이/왜 위험/영향/권고(서버측 강제 포함)`
- blocking vs non-blocking 분류
- 서버측 강제로 옮겨야 할 제어 목록
- secret/PII 노출 판정, approval gate 우회 가능성 판정
- tech-lead / 운영자에게 보낼 merge 가능 여부 권고

### Boundaries (하드레일)
- **코드를 직접 머지/배포하지 않는다 — 권고만.**
- secret 값을 읽거나 기록하지 않는다(패턴/경계만).
- 사용자 승인 없이 destructive command 금지.
- 결과는 게이트웨이(tech-lead)를 통해 외부 회신.

## 4. 검토 범위 체크리스트 (4 도메인)

체크 항목 코드 SSoT 는 manifest `review_domains`. 요약:

- **backend** — authentication · authorization · session/JWT/cookie · IDOR(object-level
  권한) · input validation · SQLi / SSRF / file upload · rate limiting · secret exposure ·
  audit logging.
- **frontend** — client = hostile surface · secret 비노출 · XSS / CSP · token storage ·
  CSRF · postMessage origin 검증 · clickjacking / frame-ancestors · permission UI 는
  보안 경계가 아님.
- **devops** — secret management · CI/CD least privilege · dependency / supply-chain ·
  container / image hardening · env/config exposure · rollback / readiness / observability.
- **AI / agent** — prompt injection · tool overreach · approval gate bypass · data
  exfiltration · auditability.

## 5. 절대 금지 (anti-goals)

- **"브라우저 개발자 도구를 사용자가 못 열게 막는다" 를 보안 목표로 정의하지 않는다.**
  클라이언트는 신뢰 경계가 아니다. 민감 제어는 **서버 검증으로 강제**한다.
- 권한 UI 숨김을 권한 경계로 착각하지 않는다.
- detection evasion / obfuscation 을 보안으로 포장하지 않는다.

## 6. `/security-review` 흐름과 grant

- `/security-review` 는 built-in 슬래시(카탈로그: `agents/grants/slash-command-grants.json`).
  engineering-agent 부서에 L1 로 부여돼 있고, `engineering-agent/security-engineer`
  role override 가 이를 **강조 부여**한다(+`/diff`, +`compact-to-vault`, +`vault-curate`).
- 런타임 grant 강제(advisory/block)는 [`agent-slash-commands.md`](agent-slash-commands.md)
  §"grant 강제" 참조 — security-engineer 가 `/security-review` 를 호출하면 ALLOW.
- 검토 세션은 `compact-to-vault` 로 vault task-log 에 적립할 수 있다(검토 추적성).

### auto-dispatch (변경 감지 → 게이트 판정)

[`agents/harness/security_gate.py`](../apps/engineering-agent/src/yule_engineering/agents/harness/security_gate.py)
의 `assess_security_review(change)` 가 변경 metadata(`paths` / `summary` / `labels`)를 보고 §2 의
intercept trigger 6종(auth / secret / public_surface / deployment / client_security / agent_safety)을
매칭해 `SecurityReviewDecision(required, triggers, reasons)` 를 낸다.

- 결과는 execution receipt 의 `security_status`(`required` / `not_required` / `skipped` /
  `not_evaluated`)에 남고, dispatch 결선에서는 `RoleRunnerInput.metadata['change']` 로 전달돼
  receipt 에 자동 기록된다(`hot_path.dispatch_receipt`).
- CLI: `yule harness security-review --change-path <p> --change-summary <s>` (required 면 exit 2).
  `yule harness receipt --change-path … --change-summary …` 로도 security 판정이 receipt 에 표면화.

**false-positive / false-negative tradeoff(의도된 설계).** 휴리스틱은 키워드/경로 기반이라
**과탐(over-trigger)** 쪽으로 기운다 — 누락(미탐)보다 낫다(보안 검토는 auth/secret 버그 누락보다
싸다). 매칭이 하나라도 잡히면 `required=True`. skip 은 절대 암묵적이지 않고 `force_skip_reason` 으로
명시·감사된다(silence ≠ skip). 코드 SSoT 와 회귀는
[`security_gate.py`](../apps/engineering-agent/src/yule_engineering/agents/harness/security_gate.py) +
[`tests/agents/test_security_gate.py`](../tests/agents/test_security_gate.py).

## 7. 후속 / 마이그레이션

- security-engineer 가 deliberation 에서 1급 참여자가 돼야 하면 `role_profiles_data.py`
  레지스트리에 8번째 profile 을 추가하고 `_EXPECTED_ROLES` 핀을 갱신한다(별도 PR).
- 보안 책임이 부서 규모로 커지면 `security-agent` 부서로 승격: org-chart SSoT + grant
  부서 추가 + `_EXPECTED_DEPARTMENTS` 갱신.
- ✅ 자동 dispatch 판정(`security_gate.assess_security_review`)은 결선 완료(§6). 남은 후속:
  변경 metadata 를 dispatch 입력에 자동 채우는 producer 결선(현재는 caller 가
  `metadata['change']` 를 채워 넘긴다)과, required 판정 시 security-engineer review 를 실제
  job 으로 auto-insert 하는 큐 결선.

## 8. 관련 문서

- 역할 계약: [`security-engineer/manifest.json`](../agents/engineering-agent/security-engineer/manifest.json) · [`CLAUDE.md`](../agents/engineering-agent/security-engineer/CLAUDE.md)
- 슬래시/grant/enforcement: [`agent-slash-commands.md`](agent-slash-commands.md)
- 승인/자율: [`approval-matrix.md`](approval-matrix.md) · [`autonomy-policy.md`](autonomy-policy.md)
- 거버넌스 hard rail: [`engineering-agent-governance.md`](engineering-agent-governance.md)
- outbound secret redaction: `packages/security/src/yule_security/paste_guard.py`
