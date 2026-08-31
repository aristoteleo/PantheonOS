"""`app check-compat` — the machine answer to "who breaks if this ships?"

Diffs two versions of an App's promises (§06): the tools face, the named
interface contracts, and the semver the manifest claims. The verdict is
mechanical so it can gate three doors with one rule — an agent's merge, a
store publish, and an instance upgrade:

  breaking change  → the interfaces carrying the broken tool must bump
                     their version, and the app must bump MAJOR;
  additive change  → MINOR (or more);
  neither          → any version (PATCH covers it).

The primitive is reflect.signature_diff; this module classifies its
findings and holds them against what the manifests actually declare.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schema import AppManifest, ToolSig


def _parse_semver(v: str) -> tuple[int, int, int]:
    parts = (v.split("-")[0].split("+")[0].split("."))
    nums = []
    for p in parts[:3]:
        try:
            nums.append(int(p))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)  # type: ignore[return-value]


def classify_tool_diff(
    old: list[ToolSig], new: list[ToolSig]
) -> tuple[list[str], list[str]]:
    """(breaking, additive) findings between two tool faces.

    Breaking: removed tool, removed param, REQUIRED param added, param type
    change, optional→required flip. Additive: new tool, new optional param.
    A required→optional flip is additive (every old call still works).
    """
    om = {t.name: t for t in old}
    nm = {t.name: t for t in new}
    breaking: list[str] = []
    additive: list[str] = []

    for name in sorted(set(om) - set(nm)):
        breaking.append(f"tool removed: {name}")
    for name in sorted(set(nm) - set(om)):
        additive.append(f"tool added: {name}")
    for name in sorted(set(om) & set(nm)):
        op = {p.name: p for p in om[name].params}
        np = {p.name: p for p in nm[name].params}
        for pn in sorted(set(op) - set(np)):
            breaking.append(f"{name}: param removed: {pn}")
        for pn in sorted(set(np) - set(op)):
            if np[pn].required:
                breaking.append(f"{name}: required param added: {pn}")
            else:
                additive.append(f"{name}: optional param added: {pn}")
        for pn in sorted(set(op) & set(np)):
            if (op[pn].type or None) != (np[pn].type or None):
                breaking.append(
                    f"{name}: param {pn} type {op[pn].type} -> {np[pn].type}")
            if not op[pn].required and np[pn].required:
                breaking.append(f"{name}: param {pn} became required")
            elif op[pn].required and not np[pn].required:
                additive.append(f"{name}: param {pn} became optional")
    return breaking, additive


@dataclass
class CompatReport:
    breaking: list[str] = field(default_factory=list)
    additive: list[str] = field(default_factory=list)
    #: Contract-keeping failures: an interface whose member broke without a
    #: version bump, a version that did not move enough, a member vanished.
    violations: list[str] = field(default_factory=list)

    @property
    def required_bump(self) -> str:
        return "major" if self.breaking else ("minor" if self.additive else "none")

    @property
    def ok(self) -> bool:
        return not self.violations

    def render(self) -> str:
        lines: list[str] = []
        for b in self.breaking:
            lines.append(f"  breaking  {b}")
        for a in self.additive:
            lines.append(f"  additive  {a}")
        for v in self.violations:
            lines.append(f"  VIOLATION {v}")
        lines.append(f"  required bump: {self.required_bump}")
        lines.append("  verdict: " + ("OK" if self.ok else "REFUSED"))
        return "\n".join(lines)


def check_compat(old: AppManifest, new: AppManifest) -> CompatReport:
    """Hold `new` against the promises `old` made."""
    report = CompatReport()
    report.breaking, report.additive = classify_tool_diff(
        old.provides.tools, new.provides.tools)

    # Which tools broke, exactly — interface bump enforcement needs names.
    broken_tools = {f.split(":")[0].split(" ")[-1] if f.startswith("tool removed") else f.split(":")[0]
                    for f in report.breaking}

    old_ifaces = {i.name: i for i in old.provides.interfaces}
    new_ifaces = {i.name: i for i in new.provides.interfaces}
    for name, oi in old_ifaces.items():
        ni = new_ifaces.get(name)
        if ni is None:
            report.violations.append(f"interface dropped: {name}@{oi.version}")
            continue
        members_broken = sorted(set(oi.tools) & broken_tools)
        dropped = sorted(set(oi.tools) - set(ni.tools))
        if (members_broken or dropped) and ni.version <= oi.version:
            what = ", ".join(members_broken + [f"{d} (dropped)" for d in dropped])
            report.violations.append(
                f"interface {name}@{oi.version} member(s) broke [{what}] "
                f"but version stayed {ni.version} — bump it")

    ov, nv = _parse_semver(old.version), _parse_semver(new.version)
    if report.breaking and nv[0] <= ov[0]:
        report.violations.append(
            f"breaking changes but version {old.version} -> {new.version} "
            f"does not bump MAJOR")
    elif report.additive and not report.breaking and nv[:2] <= ov[:2]:
        report.violations.append(
            f"additive changes but version {old.version} -> {new.version} "
            f"does not bump MINOR")
    elif nv < ov:
        report.violations.append(
            f"version went backwards: {old.version} -> {new.version}")
    return report
