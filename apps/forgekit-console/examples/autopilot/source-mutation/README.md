# source-code safe-class mutation — evidence (#240, WT3)

note/docs/examples 를 넘어 **실제 소스 파일(.py)** 에 대한 safe-class mutation 이 가능해졌다.
단 하나의 action — `source-format`(공백 전용 정규화) — 만 열렸고, 강한 hard rail 아래에서만 동작한다.

| 파일 | 무엇 |
| --- | --- |
| `before-snippet.txt` / `after-snippet.txt` | 실 `runner.py` 사본에 trailing whitespace 를 넣고 정규화한 전/후 |
| `outcome.json` | ExecOutcome — executed/verified/lines_changed/before·after hash |

## 실측 (재현)
```
BoundedMutator(repo, source_prefixes=("apps/forgekit-console/src/",)).execute(
    ExecTask("source-format", "apps/forgekit-console/src/.../runner.py"))
```
- 실 source 파일 사본의 trailing-whitespace 33줄 → **0줄**, `executed=True/verified=True`, **여전히 파싱됨**.
- **semantics preserved**: 비공백 문자 시퀀스가 전/후 동일(공백만 이동).

## hard rails (실제 강제 — 테스트로 고정: `tests/forgekit/test_source_mutation.py`)
- **기본 OFF**: `source_prefixes` 미설정이면 source 수정 거부(소스 트리 기본 불가침).
- **allowlist + 확장자**: 활성 prefix 아래 `.py` 만. traversal/절대경로/그 외 경로 거부.
- **공백 전용 guard**: 비공백이 바뀌면 safe-class 아님 → **write 전에 거부**(approval 경로로).
- **diff/line cap**: 한도 초과 → 거부, write 안 함.
- **이미 깨진 파일 skip**: 파싱 불가 파일은 손대지 않음.
- **verify + rollback**: write 후 re-read 일치 + 파싱 통과 검증. 실패 시 **원본 정확 복원(rollback)**, executed=False.
- **no-op 정직**: 이미 깨끗하면 executed=False(가짜 성공 아님).

## 아직 닫지 않은 경계 (정직)
- `source-format` 는 **공백 전용**(semantics-preserving). import 정렬/타입·린트 자동수정 같은
  **의미 보존이 아닌** safe-class 는 아직 열지 않음 — 그건 별도 verify(테스트 실행 등)와 함께 후속.
- daemon 자동 tick 은 기본적으로 note 만 — **source-format 자동화는 opt-in**(source_prefixes 주입)이며
  현재 always-on 기본 경로에는 연결하지 않음(자율 범위 확대는 승인 사안).
