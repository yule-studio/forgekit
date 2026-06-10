# Discussion Response (`discussion-response`)

## 도입 이유
Discord 멤버 봇이 부서 토론 thread 에 응답할 때 사용하는 페이로드 빌더.
응답 양식 (인용 / 출처 / TL;DR / 다음 행동) 을 표준화해서 운영자가 매번
다른 모양의 답변을 받지 않도록 한다.

## 동작
- kind: `delivery`
- hooks_provided: `COMPLETION` (응답 페이로드 마무리)
- hooks_consumed: `PREFLIGHT` (역할 컨텍스트 주입)
- risk_class: `MEDIUM` — 잘못된 표준 양식이 응답 일관성 깨뜨림
- autonomy_level: `advisory`

## 환경변수
없음.

## 운영 가이드
- 끄는 방법: 끄지 말 것. 끄면 멤버 봇 응답 양식이 free-form 으로 회귀.
- 양식 변경: 코드 안 템플릿 (`apps/engineering-agent/src/yule_engineering/agents/conversation/discussion_response.py`)
  편집 + 회귀 테스트 (`tests/conversation/test_discussion_response*`).

## 관련 정책 / 이슈
- F7 Discord conversation 양식화
- `policies/runtime/agents/engineering-agent/discord-workflow.md`
