"""Armory — the MVP skill / loadout / weapon catalog Hephaistos forges from.

First-cut catalog seeded in Python (a JSON/YAML manifest loader is a planned seam —
``armory/skills/...`` files can override/extend later). Scope is intentionally the
``backend-java-local`` loadout + java-spring family, per the MVP. Nexus source refs are
declared here as references (status resolved at read time — not_connected today).
Pure / stdlib-only.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from .models import (
    NEXUS_AREA,
    NEXUS_PATTERN,
    NEXUS_SNIPPET,
    NEXUS_TROUBLESHOOTING,
    LoadoutSpec,
    NexusSourceRef,
    SkillSpec,
    WeaponSpec,
)

_WEAPONS = (
    WeaponSpec("openjdk", "OpenJDK", "runtime", "java -version", "brew install openjdk@21"),
    WeaponSpec("gradle", "Gradle", "tool", "gradle -v", "brew install gradle"),
    WeaponSpec("mysql", "MySQL", "service", "mysql --version", "brew install mysql"),
    WeaponSpec("redis", "Redis", "service", "redis-cli --version", "brew install redis"),
    WeaponSpec("docker", "Docker", "service", "docker --version", "https://docker.com"),
    WeaponSpec("intellij", "IntelliJ IDEA", "ide", "", "jetbrains toolbox"),
    WeaponSpec("vscode", "VS Code", "ide", "code --version", "https://code.visualstudio.com"),
    WeaponSpec("gh", "GitHub CLI", "cli", "gh --version", "brew install gh"),
)

_SKILLS = (
    SkillSpec(
        "java-spring", "Java / Spring Boot",
        domains=("backend",), languages=("java",), frameworks=("spring-boot", "spring"),
        topics=("rest", "service-layer", "transaction"),
        rules=("controller 에 비즈니스 로직 금지 (service 계층 분리)",
               "트랜잭션 경계는 service 메서드에 명시"),
        commands=("./gradlew build", "./gradlew test"),
        verification=("./gradlew test", "./gradlew bootRun --dry-run"),
        related_weapons=("openjdk", "gradle"), related_loadouts=("backend-java-local",),
        related_roles=("backend-engineer",),
        nexus_refs=(NexusSourceRef(NEXUS_AREA, "20-areas/backend/java-spring"),
                    NexusSourceRef(NEXUS_PATTERN, "40-patterns/backend/transaction-boundary.md")),
    ),
    SkillSpec(
        "auth-jwt", "Auth / JWT",
        domains=("backend", "security"), languages=("java",), frameworks=("spring-boot",),
        topics=("auth-jwt", "auth", "jwt", "refresh-token", "security"),
        rules=("refresh token 저장/만료 정책 명시", "security filter chain 순서 주의",
               "access/refresh 분리, refresh 회전(rotation) 고려"),
        verification=("./gradlew test --tests '*Jwt*'",),
        related_weapons=("openjdk",), related_loadouts=("backend-java-local",),
        related_roles=("backend-engineer", "security-engineer"),
        nexus_refs=(NexusSourceRef(NEXUS_SNIPPET, "50-snippets/java/spring-security-jwt.md"),
                    NexusSourceRef(NEXUS_TROUBLESHOOTING, "60-troubleshooting/spring/bean-cycle-error.md")),
    ),
    SkillSpec(
        "mysql", "MySQL",
        domains=("backend", "database"), topics=("mysql", "sql", "persistence", "transaction"),
        rules=("스키마 변경은 migration 으로", "N+1 쿼리 주의"),
        commands=("mysql -u root -e 'SELECT 1'",),
        verification=("mysql --version", "mysql -u root -e 'SELECT 1'"),
        forbidden=("승인 없는 schema migration", "운영 DB 직접 변경"),
        related_weapons=("mysql",), related_loadouts=("backend-java-local",),
        related_roles=("backend-engineer",),
        nexus_refs=(NexusSourceRef(NEXUS_AREA, "20-areas/backend/database/mysql"),),
    ),
    SkillSpec(
        "redis", "Redis",
        domains=("backend", "database"), topics=("redis", "cache", "session", "refresh-token"),
        rules=("TTL 명시", "캐시 무효화 전략 정의"),
        commands=("redis-cli ping",),
        verification=("redis-cli ping",),
        forbidden=("운영 redis FLUSHALL 금지",),
        related_weapons=("redis",), related_loadouts=("backend-java-local",),
        related_roles=("backend-engineer",),
        nexus_refs=(NexusSourceRef(NEXUS_AREA, "20-areas/backend/database/redis"),),
    ),
    SkillSpec(
        "docker", "Docker",
        domains=("backend", "devops"), topics=("docker", "container", "local-env"),
        rules=("local 의존(mysql/redis)은 compose 로",),
        commands=("docker compose up -d",),
        verification=("docker --version", "docker compose config"),
        forbidden=("운영 container/registry 변경 금지",),
        related_weapons=("docker",), related_loadouts=("backend-java-local",),
        related_roles=("backend-engineer", "devops-engineer"),
    ),
    SkillSpec(
        "security-review", "Security Review",
        domains=("security",), topics=("security", "auth-jwt", "review"),
        rules=("secret 하드코딩 금지", "입력 검증/권한 체크 누락 점검"),
        verification=("git grep -nE '(password|secret|api[_-]?key)\\s*=' || true",),
        forbidden=("실제 exploit/active attack 금지(plan-first)",),
        related_roles=("security-engineer",),
        nexus_refs=(NexusSourceRef(NEXUS_PATTERN, "40-patterns/security/authz-checklist.md"),),
    ),
)

_LOADOUTS = (
    LoadoutSpec(
        "backend-java-local", "Backend Java (local)",
        intended_roles=("backend-engineer",),
        required_weapons=("openjdk", "gradle", "docker"),
        optional_weapons=("mysql", "redis", "intellij", "vscode", "gh"),
        environment_assumptions=("local JDK 21", "docker for mysql/redis"),
        verify_commands=("java -version", "gradle -v", "docker --version"),
    ),
)

_WEAPON_BY_ID: Dict[str, WeaponSpec] = {w.id: w for w in _WEAPONS}
_SKILL_BY_ID: Dict[str, SkillSpec] = {s.id: s for s in _SKILLS}
_LOADOUT_BY_ID: Dict[str, LoadoutSpec] = {l.id: l for l in _LOADOUTS}


def all_skills() -> Tuple[SkillSpec, ...]:
    return _SKILLS


def all_loadouts() -> Tuple[LoadoutSpec, ...]:
    return _LOADOUTS


def all_weapons() -> Tuple[WeaponSpec, ...]:
    return _WEAPONS


def skill(skill_id: str) -> Optional[SkillSpec]:
    return _SKILL_BY_ID.get(skill_id)


def loadout(loadout_id: str) -> Optional[LoadoutSpec]:
    return _LOADOUT_BY_ID.get(loadout_id)


def weapon(weapon_id: str) -> Optional[WeaponSpec]:
    return _WEAPON_BY_ID.get(weapon_id)


__all__ = ("all_skills", "all_loadouts", "all_weapons", "skill", "loadout", "weapon")
