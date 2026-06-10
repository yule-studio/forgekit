# ResearchPack — neutral data model (v0.2)

이 문서는 engineering-agent(및 향후 cto-agent / design-agent / marketing-agent)가 **연구·심의 자료를 한 덩어리로 다루기 위한 데이터 모델**을 정의한다. 코드 진실 소스는 `apps/engineering-agent/src/yule_engineering/agents/research_pack.py`.

본 모듈은 transport 비종속이며 **순수 dataclass + 작은 URL/dedup/classification helper**로만 구성된다. Discord API, 웹 자동 수집, vision 분석, 파일 쓰기는 모두 본 모듈 밖이다.

v0.2에서 강화된 점:
- 11종 `SourceType` enum과 typed source 컨스트럭터 (역할별 research profile 적용 가능).
- `ResearchSource`에 `collected_by_role` / `why_relevant` / `risk_or_limit` / `confidence` / `collected_at` 메타 추가.
- `ResearchRequest`(어느 역할이 어떤 topic을 요청했는지) 와 `ResearchFinding`(자료에서 도출한 결론) 데이터클래스.
- `pack_to_dict()` / `pack_to_markdown()` 직렬화 helper.
- 기존 v0 API(예: `pack_from_discord_message`, `merge_packs`, `extract_urls`)는 변경 없이 동작 — backward compatible.

## 1. 누가 이 데이터를 쓰나

| 소비자 | 입력으로 사용하는 시점 | 산출 |
|---|---|---|
| forum publisher (`discord/research_forum.py`) | thread 본문/댓글 포맷 | thread post + 역할 댓글 |
| dispatcher / workflow | reference 추천 보강 (URL 리스트) | DispatchPlan.reference_sources에 사용자 1순위로 합성 |
| Obsidian export (`agents/obsidian_export.py`) | thread → 마크다운 변환 | YAML frontmatter + 본문 |

세 소비자 모두 **같은 ResearchPack 인스턴스**를 입력으로 받을 수 있어야 하므로 transport 정보(channel_id/thread_id/message_id)는 옵션이고, Discord 외 origin도 같은 모델로 표현된다.

## 2. 자료 구조

```
ResearchPack
├── title: str               # 필수, 빈 입력은 "(untitled)"
├── summary: str             # 한두 문단
├── primary_url: str?        # 가장 중요한 URL (보통 첫 source의 URL)
├── sources: tuple[ResearchSource, ...]
├── findings: tuple[ResearchFinding, ...]   # NEW v0.2 — 자료에서 도출한 결론
├── request: ResearchRequest?               # NEW v0.2 — 누가/언제/왜 요청했는지
├── tags: tuple[str, ...]
├── created_at: datetime?    # merge 시 가장 이른 timestamp
└── extra: Mapping[str, Any]
```

```
ResearchRequest                              # NEW v0.2
├── request_id: str           # auto-id "req-<short hash>"
├── topic: str                # 요청 주제
├── role: str                 # 어느 역할이 요청했는지
├── session_id: str?          # WorkflowSession 연결
├── context: Mapping[str, Any]
└── created_at: datetime?
```

```
ResearchSource
├── source_url: str?
├── title / summary: str?
├── source_type: SourceType   # NEW v0.2 — user_message / url / web_result / image_reference / ...
├── collected_by_role: str?   # NEW v0.2 — 누가 이 자료를 수집했는지 (선호)
├── author_role: str?         # legacy — collected_by_role 미지정 시 fallback
├── why_relevant: str?        # NEW v0.2
├── risk_or_limit: str?       # NEW v0.2
├── confidence: str?          # NEW v0.2 — high|medium|low
├── collected_at: datetime?   # NEW v0.2 (선호) / legacy posted_at fallback
├── channel_id / thread_id / message_id: int?
├── attachment_id: str?       # NEW v0.2
├── source_id: str?           # NEW v0.2 (수동 지정 시) / 없으면 stable_id 자동 계산
├── attachments: tuple[ResearchAttachment, ...]
└── extra: Mapping[str, Any]
```

```
ResearchFinding                              # NEW v0.2
├── finding_id: str           # auto-id "find-<short hash>"
├── title: str
├── summary: str
├── role: str                 # 결론을 도출한 역할
├── supporting_source_ids: tuple[str, ...]   # ResearchSource.stable_id 참조
├── confidence: str           # high|medium|low (default medium)
├── risk_or_limit: str?
└── created_at: datetime?
```

```
ResearchAttachment
├── kind: str                 # "image" / "file" / "embed" / 자유 형식
├── url: str
├── filename / content_type: str?
├── size_bytes: int?
├── description: str?
└── attachment_id: str?       # NEW v0.2 (Discord 첨부 id 등 upstream 식별자)
```

## 3. SourceType (v0.2)

11종 + UNKNOWN. 직렬화 시 enum value(스네이크 케이스 문자열)를 그대로 사용.

| Value | 누가 수집하나 (role profile 우선순위 — `agents/deliberation.py` ROLE_RESEARCH_PROFILES) |
|---|---|
| `user_message` | tech-lead 1순위. 사용자가 직접 쓴 요구사항. |
| `url` | tech-lead/product-designer/frontend-engineer 공통 자료. 사용자가 붙인 링크. |
| `web_result` | 모든 역할 공통 보조. 검색으로 발견한 자료. |
| `image_reference` | product-designer 1순위. moodboard / screenshot / 디자인 캡처. |
| `file_attachment` | product-designer 보조. 비-이미지 파일은 그대로, 이미지는 자동으로 image_reference로 승격. |
| `github_issue` | qa-engineer / tech-lead 우선. |
| `github_pr` | backend-engineer 우선. 변경 영향·머지 상태. |
| `code_context` | backend-engineer / frontend-engineer 우선. 본 레포의 파일·라인 영역. |
| `official_docs` | backend / frontend 1순위. RFC, 프레임워크 docs, 보안 가이드. |
| `community_signal` | qa-engineer 우선. Reddit/forum/discussion. 기본 `confidence=low`. |
| `design_reference` | product-designer 1순위. Pinterest / Notefolio / Behance / Awwwards / Canva / Wix Templates. |

## 4. 파생 속성

- `ResearchPack.urls` — `primary_url + 모든 source.source_url`을 dedup해 반환.
- `ResearchPack.attachments` — 모든 source의 attachment를 dedup. dedup 키는 `(kind, cleaned_url)`.
- `ResearchPack.author_roles` — 등장한 역할 주소(`role` = collected_by_role 또는 author_role)를 first-seen 순서로 dedup.
- `ResearchPack.sources_by_type()` — `SourceType` 기준으로 source를 그룹핑한 dict.
- `ResearchSource.role` — `collected_by_role` 우선, 없으면 legacy `author_role`로 fallback.
- `ResearchSource.timestamp` — `collected_at` 우선, 없으면 legacy `posted_at`.
- `ResearchSource.discord_origin` — channel_id/thread_id/message_id 중 하나라도 있으면 True.
- `ResearchSource.stable_id` — 명시 `source_id`가 있으면 그대로, 없으면 `(message_id, thread_id, channel_id, attachment_id, url, title)`의 sha1 앞 10자리.

## 5. Helper 함수

### 5.1 URL helpers
- `extract_urls(text) -> tuple[str, ...]` — 자유 텍스트에서 URL을 정규식으로 추출, 끝 punctuation(`.,);`) 제거, dedup.
- `dedup_urls(iterable) -> tuple[str, ...]` — None/빈 입력 drop, first-seen 순서 보존.

### 5.2 첨부 분류 (v0.2)
- `classify_attachment(filename=..., content_type=..., fallback=SourceType.FILE_ATTACHMENT) -> SourceType` — MIME prefix(`image/`) 또는 파일 확장자(`.png`/`.jpg`/`.svg`/`.heic`/...)로 이미지 판별. vision 분석은 하지 않는다.
- `normalize_attachment_metadata(att) -> ResearchAttachment` — content_type 소문자, filename 트림, 음수 size_bytes drop, 이미지로 분류되면 generic kind를 `image`로 자동 승격.

### 5.3 Typed source 컨스트럭터 (v0.2)
- `source_from_user_message(content, collected_by_role, ...)` → USER_MESSAGE
- `source_from_url(url, collected_by_role, ...)` → URL
- `source_from_web_result(url, title, summary, collected_by_role, ...)` → WEB_RESULT
- `source_from_image_reference(url, collected_by_role, filename=..., content_type=..., attachment_id=...)` → IMAGE_REFERENCE (첨부 1개 자동 생성)
- `source_from_file_attachment(url, collected_by_role, filename=..., ...)` → FILE_ATTACHMENT (이미지면 자동으로 IMAGE_REFERENCE로 승격)
- `source_from_github_issue(url, title, collected_by_role, issue_number=..., repository=..., ...)` → GITHUB_ISSUE
- `source_from_github_pr(url, title, collected_by_role, pr_number=..., state=..., ...)` → GITHUB_PR
- `source_from_code_context(repo_path, summary, collected_by_role, line_range=..., ...)` → CODE_CONTEXT
- `source_from_official_docs(url, title, collected_by_role, publisher=..., ...)` → OFFICIAL_DOCS
- `source_from_community_signal(url, title, collected_by_role, platform=..., ...)` → COMMUNITY_SIGNAL (default confidence `low`)
- `source_from_design_reference(url, title, collected_by_role, platform=..., ...)` → DESIGN_REFERENCE

### 5.4 Pack 생성
- `pack_from_discord_message(title, content, ...)` — legacy. 한 Discord 메시지 → USER_MESSAGE 단일-source pack.
- `pack_from_request(request, sources=..., findings=..., ...)` — `ResearchRequest`를 명시적으로 묶는 진입점.
- `merge_packs([p1, p2, ...])` — N개 pack을 합쳐 source/finding/tag/url을 union+dedup. 빈 입력은 ValueError.
- `pack_with_extra_source(pack, source)` — 기존 pack에 source 1개 추가. dedup 키 일치 시 무시.
- `pack_with_finding(pack, finding)` — 기존 pack에 finding 1개 추가. `finding_id` 일치 시 무시.

### 5.5 Request / Finding 생성
- `make_research_request(topic, role, session_id=..., context=..., request_id=..., created_at=...)` — auto request_id `req-<short>`.
- `make_finding(title, summary, role, supporting_source_ids=..., confidence="medium", risk_or_limit=..., finding_id=..., created_at=...)` — auto finding_id `find-<short>`.

### 5.6 직렬화 (v0.2)
- `pack_to_dict(pack) -> dict` — JSON 직렬화 가능한 평면 dict. SQLite cache / 외부 transport용.
- `pack_to_markdown(pack) -> str` — 사람용 Markdown. 요청 → source_type별 그룹 (`SourceType` enum 순서 = canonical) → finding 순으로 렌더링.

### 5.7 Source dedup 키 (v0.2 갱신)

**`(source_type, message_id, thread_id, channel_id, attachment_id, cleaned_url)`** 6-튜플.

- `source_type`이 추가되어 같은 메시지에서 발생한 user_message와 image_reference는 별개 source로 보존된다.
- `attachment_id`가 추가되어 같은 사용자가 여러 첨부를 한 번에 올렸을 때 식별자별로 분리된다.
- `title`/`summary`는 여전히 dedup 키에 들어가지 않는다 — enrichment는 `dataclasses.replace`로 직접 수행한다.

## 6. 사용 예

### 6.1 legacy — 단일 Discord 메시지 (v0)

```python
from yule_engineering.agents.research_pack import (
    pack_from_discord_message, merge_packs,
)

p1 = pack_from_discord_message(
    title="Stripe Pricing 패턴",
    content="hero step copy 강조 — https://stripe.com/pricing 참고",
    author_role="engineering-agent/product-designer",
    channel_id=1499287359483805879,
    thread_id=1500000000000000001,
    message_id=1500000000000000002,
)

p2 = pack_from_discord_message(
    title="Wix 랜딩 grid",
    content="https://wix.com/templates 참고",
    channel_id=1499287359483805879,
    thread_id=1500000000000000001,
    message_id=1500000000000000003,
)

bundle = merge_packs([p1, p2])
# bundle.urls → ('https://stripe.com/pricing', 'https://wix.com/templates')
```

### 6.2 v0.2 — 역할별 Research Profile에 맞춘 typed 수집

```python
from yule_engineering.agents.research_pack import (
    make_research_request, make_finding,
    pack_from_request, pack_with_finding,
    source_from_user_message, source_from_url,
    source_from_image_reference, source_from_design_reference,
    source_from_official_docs, source_from_github_issue,
)

req = make_research_request(
    topic="새 hero 섹션",
    role="engineering-agent/tech-lead",
    session_id="ws-abc123",
)

sources = (
    source_from_user_message(
        content="hero 섹션 다시 짜야 해",
        collected_by_role="engineering-agent/tech-lead",
        channel_id=1498929862881054721,
        message_id=900,
    ),
    source_from_design_reference(
        url="https://www.behance.net/example",
        title="behance hero 패턴",
        collected_by_role="engineering-agent/product-designer",
        platform="behance",
        why_relevant="step copy 강조 패턴",
    ),
    source_from_image_reference(
        url="https://cdn.example/moodboard.png",
        collected_by_role="engineering-agent/product-designer",
        filename="moodboard.png",
        content_type="image/png",
        attachment_id="att-7",
    ),
    source_from_official_docs(
        url="https://react.dev/reference",
        title="React reference",
        collected_by_role="engineering-agent/frontend-engineer",
        publisher="React",
    ),
    source_from_github_issue(
        url="https://github.com/o/r/issues/42",
        title="기존 hero 회귀 보고",
        collected_by_role="engineering-agent/qa-engineer",
        issue_number=42,
        repository="o/r",
    ),
)

pack = pack_from_request(request=req, sources=sources, tags=("hero", "ux"))
pack = pack_with_finding(
    pack,
    make_finding(
        title="hero copy 단순화 + CTA 색 강조 채택",
        summary="3줄 → 2줄, primary CTA 컬러 강화",
        role="engineering-agent/product-designer",
        supporting_source_ids=(sources[1].stable_id, sources[2].stable_id),
        confidence="high",
        risk_or_limit="모바일 그리드 미검증",
    ),
)

# 직렬화: 외부 transport / 디버깅용
import json; json.dumps(pack_to_dict := __import__("yule_engineering.agents.research_pack", fromlist=["pack_to_dict"]).pack_to_dict(pack), ensure_ascii=False)
# 사람용 마크다운: forum thread / 운영자 리뷰용
print(__import__("yule_engineering.agents.research_pack", fromlist=["pack_to_markdown"]).pack_to_markdown(pack))
```

## 7. 변경 절차

- 새 필드를 추가할 때는 본 문서 §2 트리를 먼저 갱신하고 코드 dataclass와 함께 푼다.
- 새 `SourceType` 값을 추가하면 §3 표 + `agents/deliberation.py`의 `KNOWN_SOURCE_TYPES` / `ROLE_RESEARCH_PROFILES`도 함께 갱신해 정합성을 유지한다.
- dedup 키를 바꾸면 forum publisher / Obsidian export 모두 동작이 흔들리므로 별도 PR로 처리하고 영향 범위를 PR 본문에 명시한다.
- transport 정보(channel_id 등)를 필수로 올리지 않는다 — Discord 외 origin도 같은 모델로 흘려야 한다.
- `pack_to_markdown` 헤더 순서는 `SourceType` enum 순서를 따른다 (canonical sort). enum 순서가 바뀌면 마크다운 diff에 영향이 있음을 PR에 명시.

## 8. 후속 작업

1. `discord/research_forum.py` adapter — ResearchPack을 thread post/댓글로 포맷 (이미 구현됨; v0.2 source_type을 그대로 활용 가능).
2. dispatcher에서 사용자 제공 reference 1순위로 ResearchPack URL 흡수.
3. `agents/obsidian_export.py` — ResearchPack + deliberation 결과를 Markdown으로 변환 (이미 구현됨).
4. URL 정규화 강화 — 트래킹 파라미터 제거, 동일 host의 prefix 통합 등.
5. vision 분석 어댑터 — IMAGE_REFERENCE source의 첨부 이미지를 외부 vision 모델에 위임해 `summary`를 채우는 후속 모듈.
