# Nexus read foundation — evidence (Hephaistos PR1)

Hephaistos 가 Nexus(외부 지식 source)를 **정직하게** 읽는 read path 의 실측.
Nexus 복사 없음, fake-read 없음.

| 파일 | 상태 |
| --- | --- |
| `connected.json` | Nexus root 연결 — 3 요청 → 2 resolved(1 raw + 1 restricted **projection_only**) / 1 missing |
| `connected-doc-sample.json` | 읽은 문서의 **bounded** 구조화(title/summary/key_points/rules/snippet/troubleshooting) — raw dump 아님 |
| `not-connected.json` | Nexus 미연결 — **0 resolved**(아무것도 날조 안 함), read_mode=none |

## 상태 모델 (정직)
- **not_connected**: `FORGEKIT_NEXUS_ROOT`/config 미설정 → 메인 상태. docs 0.
- **missing**: 연결됐으나 path 부재.
- **blocked**: 존재하나 읽기 불가(permission/TCC/sandbox).
- **restricted**: 존재하나 raw gating — 비허용 role 은 **projection_only**(title/why), raw 본문 미노출. 허용 role(design-lead 등)만 raw.
- **exists**: 읽어서 bounded normalize.

## 경계 (PR1 범위)
- operator surface(`/resolve`·`/sources` 등)는 **PR2**. 여기는 read foundation + resolver seam(`read_plan_sources`)까지.
- normalize 는 bounded(summary ≤500자, snippet ≤300자, points cap) — 전체 raw dump 금지.
