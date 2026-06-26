# armory/skills — 재사용 skill 저장소

반복 가치가 있는 작업 단위를 **skill** 로 승격해 저장한다.

승격 기준:
- labs 의 실험 결과 중 **반복적으로 쓰이고 검증된 것만** 승격한다.
- 1회성 실험·미검증 패턴은 승격하지 않는다(labs 에 남긴다).
- 승격된 skill 은 추후 `packages/forge-registry` 에 등록되어야 런타임에서 보인다.

> 흐름: labs(실험) → **armory/skills**(승격) → forge-registry(등록). adopted ≠ equipped.
