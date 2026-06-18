# Runbook — restricted design source 접근

- 경로: `/Users/masterway/Desktop/마스터웨이 피그마 백업` (repo 밖, 민감 자산, read-only).
- macOS TCC 로 막히면 `design_source_blocked` — 절대 fake-read 하지 않음.
- 허용: 터미널/forgekit 프로세스에 'Full Disk Access' 부여, 또는 design role 이
  필요한 파일을 export 해 operator-provided reference 로 전달.
- raw .fig/export 를 repo/vault 본문에 복사 금지 — 메타/packet 만 남김.
- design role(ux-ui-designer/design-systems-designer/illustration-brand-designer/
  design-lead)만 raw 접근, 그 외는 projection.
