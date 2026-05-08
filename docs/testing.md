# 테스트

기본 자동 테스트는 표준 라이브러리 `unittest` 로 실행한다.

```bash
python3 -m unittest discover -s tests -t .
```

## 디렉토리 구조

`tests/` 는 기능 영역별 하위 패키지로 정리되어 있다. 새 테스트는 해당 영역 디렉토리에 추가한다.

```text
tests/
  _bootstrap.py          # sys.path 세팅 + 캐시 격리 헬퍼
  _helpers.py            # 공유 fake message/channel/session 등
  engineering/           # engineering-agent 라우터/대화/팀 런타임
  research/              # research collector/loop/pack/sufficiency
  memory/                # memory index + retrieval
  obsidian/              # Obsidian export/writer/git/CLI sync
  discord/               # discord 봇 런타임/명령/포매터/포럼
  calendar/              # 캘린더 캐시/카테고리/E2E
  planning/              # planning 입력/스냅샷/플래너
  integrations/          # GitHub/TLS 등 외부 통합
  core/                  # agent 디스패처/워크플로/타임존 등 공통 유틸
```

## 집중 테스트 묶음

영역별로 좁혀서 돌리고 싶을 때 사용하는 묶음. 라우터 파일이 관심사별로 3 개로 쪼개져 있어 회귀 디버깅 시 실패 위치가 곧 영역을 가리킨다.

### engineering-agent (라우팅 / persistence / forum + 라우팅 결정 단위 테스트)

```bash
python3 -m unittest \
  tests.engineering.test_channel_router_routing \
  tests.engineering.test_channel_router_persistence \
  tests.engineering.test_channel_router_forum \
  tests.engineering.test_routing
```

대화 / 팀 런타임 / 리서치 루프 hook 까지 포함하려면:

```bash
python3 -m unittest discover -s tests/engineering -t .
```

### 리서치 sufficiency / collector 루프

```bash
python3 -m unittest \
  tests.research.test_collector_sufficiency \
  tests.research.test_sufficiency
```

### memory 인덱스 / retrieval

```bash
python3 -m unittest \
  tests.memory.test_index \
  tests.memory.test_retrieval
```

### Obsidian 동기화

```bash
python3 -m unittest \
  tests.obsidian.test_export \
  tests.obsidian.test_writer \
  tests.obsidian.test_cli_sync
```

## 공유 fixture

라우터 테스트에서 반복되는 가짜 Discord 채널 / 메시지 / 세션 객체는 `tests/_helpers.py` 에 모여 있다. 새 라우터 테스트를 추가할 때는 `tests._helpers` 에서 `FakeChannel`, `FakeMessage`, `FakeSession`, `isolate_cache_for_test` 등을 import 해서 사용한다. `tests/_bootstrap.py` 는 `sys.path` 에 `src/` 를 끼워 넣고 기본 `YULE_CACHE_DB_PATH` 를 격리된 파일로 돌려놓는다.

## Optional dependency skip

`tests/calendar/test_category_color.py` 는 `icalendar` 패키지가 설치돼 있어야 의미 있게 돌아간다. 패키지를 설치하지 않은 dev 환경에서는 자동으로 skip 처리되도록 가드가 들어 있어 전체 `unittest discover` 가 실패하지 않는다. 실제로 캘린더 경로를 확인하려면 `pip install -e .` 로 프로젝트 의존성을 설치한다.

## 라이브 회귀

자동화 테스트 외에 사람이 Discord 에서 직접 돌리는 4 시나리오 라이브 회귀 절차: `policies/runtime/agents/engineering-agent/live-regression.md`.
