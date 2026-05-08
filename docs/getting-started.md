# Getting Started

이 문서는 Yule Studio Agent 를 처음 셋업할 때 거쳐야 할 단계를 정리한다.

## 1. 설치

### 빠른 설치 (macOS + Homebrew)

```bash
./scripts/bootstrap
```

이 스크립트는 다음 작업을 수행한다.

- Homebrew 확인
- `gh` 와 Python 확인
- `.venv` 생성
- `pip`, `setuptools`, `wheel` 업그레이드
- 프로젝트 editable install
- `.env.example` 이 있으면 `.env.local` 템플릿 생성
- 기존 `.env.local` 이 있으면 덮어쓰지 않고, `.env.example` 대비 빠진 키만 안내

선택 AI CLI 까지 함께 설치하려면:

```bash
./scripts/bootstrap --all
```

### 수동 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
```

## 2. 수동 인증

다음 로그인은 자동화하지 않는다.

```bash
gh auth login
claude
codex
gemini
copilot
```

Ollama 는 필요할 때 실행한다.

```bash
open -a Ollama
# 또는
ollama serve
```

## 3. 환경 변수

루트의 `.env.local` 에 토큰·채널 ID 등을 설정한다. 키 카테고리·기본값·운영 노하우는 [configuration.md](configuration.md) 를 참고한다.

## 4. 헬스 체크

```bash
yule doctor
yule context engineering-agent
yule context planning-agent
```

`doctor` 는 Python 환경 / Discord 토큰 / Obsidian vault 등 주요 의존성을 확인한다.

## 5. 첫 실행

CLI 로 단일 명령을 돌려본다.

```bash
yule daily warmup --json
yule github issues --limit 30
yule calendar events --json
```

Discord 봇 dev 환경 일괄 기동:

```bash
yule discord up --dry-run   # 인벤토리만 확인
yule discord up             # 실제로 띄움 (dev 전용)
```

상시 운영 (systemd) 은 [operations.md](operations.md) 를 본다.

## 6. 모듈 직접 실행 (옵션)

엔트리포인트 설치가 덜 맞물려 있을 때 모듈 형태로 동일하게 사용할 수 있다.

```bash
PYTHONPATH=src python3 -m yule_orchestrator doctor
PYTHONPATH=src python3 -m yule_orchestrator planning daily --json
PYTHONPATH=src python3 -m yule_orchestrator discord bot
```
