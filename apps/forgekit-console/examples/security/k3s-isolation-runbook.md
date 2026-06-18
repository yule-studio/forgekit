# Runbook — red/blue 격리 k3s 환경

- 전용 namespace(예: `forgekit-drill`) + NetworkPolicy 로 외부/타 namespace 격리.
- 드릴 대상은 이 namespace 안 내 자산만. 공용 인터넷/3rd-party 금지.
- 기본 dry-run/plan-only. active 드릴은 operator 승인 후에만.
- offensive tooling 은 일반 모드에 노출하지 않음 — 계획/방어 runbook 만.
