# goal governance enforcement — evidence

`goal-governance-evidence.txt` 는 goal 루프가 **PM → gateway → tech-lead → specialist**
artifact 흐름을 강제함을 결정적으로 재현한다 (issue #450):

1. big goal 분해 시 **PM brief 가 첫 governance artifact** 로 기록되고 goal 이 governance-required 가 된다.
2. `/goal govern <id>` 가 design-chain readiness ladder 를 표면 (incomplete → 정직하게 pending).
3. design chain 미완이면 goal-exec tick 이 specialist 실행을 **차단** (physical run 없음).
4. operator/tech-lead 가 스택 2안 비교 + 서명으로 chain 을 완료하면 gate 가 열린다.

재생성: `PYTHONPATH=<packages/*/src:apps/forgekit-console/src> python3 _regen.py > goal-governance-evidence.txt`
회귀: `python3 -m unittest tests.forgekit.test_goal_governance_enforcement`
코드 SSoT: `packages/forgekit-runtime/src/forgekit_runtime/runtime/goal_governance.py` · 문서 `docs/pm-techlead-lane.md` §7.8
