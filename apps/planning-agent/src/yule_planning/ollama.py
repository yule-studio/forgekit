from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from typing import Callable, Optional, Sequence
from urllib import error, request

from .models import PlanningBlockBriefing, PlanningCheckpoint, PlanningTaskCandidate, PlanningTimeBlock

ResponseValidator = Callable[[str], Optional[str]]

_ISO_DATETIME_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
_HALLUCINATED_NOW_PATTERNS = (
    re.compile(r"현재\s*(?:오전|오후|새벽|아침|저녁|밤)?\s*\d{1,2}\s*시\s*\d{1,2}\s*분"),
    re.compile(r"지금\s*(?:오전|오후|새벽|아침|저녁|밤)?\s*\d{1,2}\s*시\s*\d{1,2}\s*분"),
)
_INTERNAL_SCORE_KEYWORDS = (
    "내부 점수",
    "priority_score",
    "score=",
    "점수=",
)


def validate_briefing_response(content: str) -> Optional[str]:
    if _ISO_DATETIME_PATTERN.search(content):
        return "ISO datetime leaked"
    for keyword in _INTERNAL_SCORE_KEYWORDS:
        if keyword in content:
            return f"internal-score keyword '{keyword}' leaked"
    for pattern in _HALLUCINATED_NOW_PATTERNS:
        if pattern.search(content):
            return "hallucinated current time leaked"
    for raw in content.splitlines():
        stripped = raw.strip()
        if stripped.startswith(("#", "##", "###")):
            return "markdown heading leaked"
    return None


def validate_conversation_response(content: str) -> Optional[str]:
    if _ISO_DATETIME_PATTERN.search(content):
        return "ISO datetime leaked"
    for keyword in _INTERNAL_SCORE_KEYWORDS:
        if keyword in content:
            return f"internal-score keyword '{keyword}' leaked"
    return None


def generate_human_briefing(
    plan_date: str,
    summary_line: str,
    fixed_schedule: Sequence[PlanningTimeBlock],
    prioritized_tasks: Sequence[PlanningTaskCandidate],
    time_block_briefings: Sequence[PlanningBlockBriefing],
    checkpoints: Sequence[PlanningCheckpoint],
    model: str = "gemma3:latest",
    endpoint: str = "http://localhost:11434",
    timeout_seconds: int = 20,
    work_mode_enabled: bool = True,
    fallback_model: Optional[str] = None,
    retry_count: int = 1,
) -> str:
    prompt = _build_prompt(
        plan_date=plan_date,
        summary_line=summary_line,
        fixed_schedule=fixed_schedule,
        prioritized_tasks=prioritized_tasks,
        time_block_briefings=time_block_briefings,
        checkpoints=checkpoints,
        work_mode_enabled=work_mode_enabled,
    )
    return generate_ollama_text(
        prompt,
        model=model,
        endpoint=endpoint,
        timeout_seconds=timeout_seconds,
        temperature=0.4,
        empty_error_message="Ollama briefing response was empty.",
        request_label="briefing",
        validate_response=validate_briefing_response,
        retry_count=retry_count,
        fallback_model=fallback_model,
    )


def generate_ollama_text(
    prompt: str,
    *,
    model: str = "gemma3:latest",
    endpoint: str = "http://localhost:11434",
    timeout_seconds: int = 20,
    temperature: float = 0.4,
    empty_error_message: str = "Ollama response was empty.",
    request_label: str = "request",
    validate_response: Optional[ResponseValidator] = None,
    retry_count: int = 0,
    fallback_model: Optional[str] = None,
) -> str:
    last_error: Optional[str] = None
    primary_attempts = max(1, 1 + max(0, retry_count))

    for attempt in range(1, primary_attempts + 1):
        try:
            content = _ollama_request_once(
                prompt=prompt,
                model=model,
                endpoint=endpoint,
                timeout_seconds=timeout_seconds,
                temperature=temperature,
                empty_error_message=empty_error_message,
                request_label=request_label,
            )
        except ValueError as exc:
            last_error = str(exc)
            print(
                f"warning: ollama {request_label} attempt {attempt}/{primary_attempts} "
                f"on model={model} failed: {exc}",
                file=sys.stderr,
            )
            continue

        if validate_response is None:
            return content
        violation = validate_response(content)
        if violation is None:
            return content
        last_error = f"validation failed: {violation}"
        print(
            f"warning: ollama {request_label} attempt {attempt}/{primary_attempts} "
            f"on model={model} validation failed: {violation}",
            file=sys.stderr,
        )

    if fallback_model and fallback_model != model:
        print(
            f"info: ollama {request_label} falling back from model={model} "
            f"to model={fallback_model}",
            file=sys.stderr,
        )
        try:
            content = _ollama_request_once(
                prompt=prompt,
                model=fallback_model,
                endpoint=endpoint,
                timeout_seconds=timeout_seconds,
                temperature=temperature,
                empty_error_message=empty_error_message,
                request_label=request_label,
            )
        except ValueError as exc:
            raise ValueError(
                f"Ollama {request_label} failed (model={model} after {primary_attempts} attempts; "
                f"fallback model={fallback_model}: {exc})"
            ) from exc

        if validate_response is not None:
            violation = validate_response(content)
            if violation is not None:
                print(
                    f"warning: ollama {request_label} fallback model={fallback_model} "
                    f"validation still failed: {violation}; returning content anyway",
                    file=sys.stderr,
                )
        return content

    raise ValueError(
        f"Ollama {request_label} failed (model={model}, attempts={primary_attempts}): {last_error}"
    )


def _ollama_request_once(
    *,
    prompt: str,
    model: str,
    endpoint: str,
    timeout_seconds: int,
    temperature: float,
    empty_error_message: str,
    request_label: str,
) -> str:
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")

    req = request.Request(
        endpoint.rstrip("/") + "/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError(f"Ollama {request_label} request failed: {exc}") from exc

    result = data.get("response")
    if not isinstance(result, str) or not result.strip():
        raise ValueError(empty_error_message)
    return result.strip()


def _build_prompt(
    plan_date: str,
    summary_line: str,
    fixed_schedule: Sequence[PlanningTimeBlock],
    prioritized_tasks: Sequence[PlanningTaskCandidate],
    time_block_briefings: Sequence[PlanningBlockBriefing],
    checkpoints: Sequence[PlanningCheckpoint],
    work_mode_enabled: bool = True,
) -> str:
    schedule_lines = [
        f"- {_format_time_range(block.start, block.end)} | {block.title}"
        for block in fixed_schedule[:5]
    ] or ["- 고정 일정 없음"]
    task_lines = [
        f"- {task.title} | 우선순위={_priority_label(task.priority_level)} | 이유={_summarize_reasons(task.reasons)}"
        for task in prioritized_tasks[:5]
    ] or ["- 우선 작업 없음"]
    block_lines = [
        f"- {_format_time_range(briefing.start, briefing.end)} | {briefing.title} | {briefing.briefing}"
        for briefing in time_block_briefings[:5]
    ] or ["- 시간대별 브리핑 없음"]
    checkpoint_lines = [
        f"- {datetime.fromisoformat(checkpoint.remind_at).strftime('%H:%M')} | {checkpoint.block_title} | {checkpoint.prompt}"
        for checkpoint in checkpoints[:5]
    ] or ["- 체크포인트 없음"]

    if work_mode_enabled:
        mode_block = (
            "현재 모드: 회사 업무 우선 모드\n"
            "- '업무 수행' 일정은 회사 일과 시간이며 그 시간대에는 다른 todo를 끼우지 말 것\n"
            "- '업무 수행'이 아닌 다른 이벤트들은 모두 퇴근 이후의 일정으로 간주할 것\n"
            "- todo는 일과 시간 사이의 빈 시간이나 퇴근 후 시간에 안내할 것"
        )
    else:
        mode_block = (
            "현재 모드: 자유 모드 (회사 일정 없음)\n"
            "- '업무 수행' 일정이 입력에 있어도 무시하고 todo 우선순위 기준으로 안내할 것\n"
            "- todo는 하루 전체 시간을 자유롭게 사용해 안내할 것"
        )

    return f"""당신은 개인 Planning Agent의 브리핑 작성자입니다.
다음 daily-plan 입력을 보고 한국어로 자연스럽고 실용적인 아침 브리핑을 Discord 메시지 형태로 작성하세요.

내용 조건:
- 2~4개의 짧은 문단으로 쓸 것
- 각 문단은 2~3개의 짧은 문장으로 한 흐름을 이루게 쓸 것
- 너무 딱딱하거나 과장하지 말 것
- 가장 먼저 할 일 1개를 분명하게 추천할 것
- 가능하면 2순위, 3순위는 같은 문단 안에서 숨이 쉬게 이어서 설명할 것
- 시간 블록이 있으면 어느 시간대에 무엇을 하면 좋을지 짚을 것
- 체크포인트가 있으면 마지막 문단에서 짧게 한 줄로 언급할 것
- 내부 점수, 우선순위 계산 숫자, ISO datetime 원문은 절대 노출하지 말 것
- 사용자가 입력하지 않은 수치 평가(예: 95점, 87점)를 만들어내지 말 것
- 현재 시각/지금 몇 시 몇 분 같은 분 단위 시각을 임의로 만들어내지 말 것 (입력에는 날짜만 있고 분 단위 현재 시각은 없음. 절대 "현재 11시 35분입니다" 같은 표현을 쓰지 말 것)
- 시각 표기는 입력에 있는 시간 블록 시작/종료 시각만 사용할 것 (예: "09:00부터 10:00까지")

양식 규칙(매우 중요):
- 같은 흐름의 문장들은 줄바꿈(\\n) 한 번만 두어 같은 문단 안에 묶을 것
- 주제가 달라지는 문단 사이에만 빈 줄(\\n\\n) 한 줄을 둘 것
- 모든 문장 사이에 빈 줄을 넣지 말 것 (Discord에서 너무 띄엄띄엄 보임)
- 한 문장을 한 문단으로 만들지 말 것 (반드시 같은 흐름의 다른 문장과 묶어 둘 것)
- bullet (-, *)이나 마크다운 헤딩(#)은 사용하지 말고 자연스러운 산문으로 작성

출력 양식 예시(이 모양 그대로 따라 쓸 것):
오늘 가장 먼저 'A 작업'을 09:00부터 10:00까지 진행하는 것을 추천합니다.
이 작업은 우선순위가 높고 오늘 마감이라 흐름을 먼저 잡아두는 편이 안정적입니다.
끝나면 바로 'B 작업'으로 이어서 정리하면 좋습니다.

오후에는 13:00부터 'C 작업'을 진행할 수 있습니다.
14:00 체크포인트에서 진행 상태를 짧게 점검해 주세요.

{mode_block}

날짜:
{plan_date}

헤드라인 요약:
{summary_line}

고정 일정:
{chr(10).join(schedule_lines)}

우선 작업:
{chr(10).join(task_lines)}

시간대별 브리핑 초안:
{chr(10).join(block_lines)}

체크포인트:
{chr(10).join(checkpoint_lines)}
"""


def _format_time_range(start_value: str, end_value: str) -> str:
    start_label = datetime.fromisoformat(start_value).strftime("%H:%M")
    end_label = datetime.fromisoformat(end_value).strftime("%H:%M")
    return f"{start_label}~{end_label}"


def _priority_label(value: str) -> str:
    return {
        "high": "높음",
        "medium": "중간",
        "low": "낮음",
    }.get(value, value)


def _summarize_reasons(reasons: Sequence[str]) -> str:
    filtered = [reason.strip() for reason in reasons if reason.strip()]
    if not filtered:
        return "특별한 사유 없음"
    return ", ".join(filtered[:3])
