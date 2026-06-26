# Commands — <agent> 평가 명령 기록

> 이 _template 을 agent 폴더로 복사해 실제 명령/출력을 채운다. 재현 가능하게 적는다.

## 설치 확인
```bash
# 예: which <bin>  /  command -v <bin>
```

## 버전 확인
```bash
# 예: <bin> --version
```

## 기본 실행
```bash
# 예: <bin> "간단한 요청"
```

## non-interactive 실행 테스트
```bash
# 사람 입력 없이 1-shot 으로 도는지 (예: <bin> -p "prompt"  또는  echo "prompt" | <bin>)
```

## workspace 지정 테스트
```bash
# 작업 디렉터리를 명시 지정할 수 있는지 (예: <bin> --cwd <path>  또는  cd <path> && <bin> …)
```

## 로그 캡처 방식
```bash
# stdout/stderr/exit code 캡처 (예: <bin> … >out.log 2>err.log; echo "exit=$?")
```

## 실패 케이스 기록
- 무엇을 시도했는가 / 기대 / 실제 / 종료코드 / 재현 명령
- (실패는 findings.md 약점 섹션과 nexus/evaluations 로 연결)
