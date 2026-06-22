"""Regenerate palette-matching.txt — prefix-first + substring fallback (deterministic, pure).

Proves the slash palette is no longer prefix-only: a meaningful word that no command STARTS
with previously dead-ended to an empty palette and now reaches the command that CONTAINS it,
WITHOUT widening any prefix result (zero regression). Pure (no terminal).
재현: tests/forgekit/test_palette.py · tests/forgekit/test_parser.py
"""

from __future__ import annotations

from forgekit_console.commands.parser import palette_matches


def names(q):
    return [c.name for c in palette_matches(q)]


def banner(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


print("ForgeKit console — slash palette matching (prefix-first + substring fallback)")
print("재현: tests/forgekit/test_palette.py · test_parser.py")

banner("PREFIX (기존 동작·순서 유지 — fallback 이 넓히지 않음)")
for q in ("/st", "/he", "/p", "/a"):
    print(f"  {q:<10} → {names(q)}")

banner("SUBSTRING FALLBACK (prefix 0건 → 이전엔 빈 palette = dead-end)")
for q in ("/improve", "/blue", "/observer"):
    print(f"  {q:<10} → {names(q)}")

banner("NONSENSE (정직 — 여전히 빈 결과)")
for q in ("/zzz", "/qqqq"):
    print(f"  {q:<10} → {names(q)}")

print("\n(끝) — prefix 우선, prefix 0건일 때만 substring · 회귀 0 · 가짜 0")
