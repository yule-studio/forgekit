# nexus/evaluations — 도구 평가 결과 저장소

CLI agent / tool 의 평가 결과와 도구별 장단점을 저장하는 위치다.

- `labs/cli-agents/<agent>/` 에서 나온 평가(evaluation.yaml / findings.md)의 **정제된 결론**을 여기에 축적한다.
- 이 데이터는 **Hephaistos 의 tool-selector 가 도구 선택 기준으로 참조**할 수 있는 근거가 된다.
- 도구별 적합도(fit_for_forgekit), 안전 모델, 비교 우위를 한곳에서 조회할 수 있게 한다.

> 원칙: 평가 없는 도구 선택 금지. 평가 결과는 재현 가능한 근거와 함께 남긴다.
