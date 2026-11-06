"""Exact readable inputs for explicit project-owned promotion Flavors."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from literate_ai.adapters.component_lock_planning import (
    _bounded_bytes,
    _load_flavors,
    _resolve_path,
)
from literate_ai.adapters.project_initialization import (
    KNOWN_FLAVOR_SELECTORS,
    canonical_flavor_selector,
)
from literate_ai.application.flavor_selection import (
    apply_flavor_selectors,
    flavor_candidate_aliases,
    parse_flavor_selector_body,
    parse_scoped_flavor_selector,
)
from literate_ai.application.skill_closure import (
    close_specification_to_source_skills,
    load_admitted_skill_catalog,
)
from literate_ai.contracts import ContentReference
from literate_ai.contracts.skills import ResolvedSpecificationToSourceSkill
from literate_ai.projects import (
    PinnedInputClosure,
    ProjectConfigurationStore,
    specification_to_source_skill_paths,
)
from literate_ai.source_to_specification.promotion_materialization import (
    PromotionInputKind,
)


class PromotionParentFlavorError(ValueError):
    """Explicit parent Flavor inputs cannot be captured without changing authority."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _selected(project, selectors):
    catalog = _load_flavors(
        project.roots("flavor"),
        boundary=project.root,
        scale_limits=project.definition.scale_limits,
    )
    selected = apply_flavor_selectors(
        tuple(item.selection for item in catalog), selectors
    )
    return tuple(
        next(item for item in catalog if item.selection == candidate)
        for candidate in selected
    )


@dataclass(frozen=True)
class ParentFlavorInputs:
    project: object
    selectors: tuple[str, ...]
    revisions: tuple[str, ...]
    guard: PinnedInputClosure
    inputs: tuple[tuple[str, bytes, PromotionInputKind], ...]
    skill_references: tuple[object, ...]
    directories: tuple[tuple[str, str], ...]

    def directory_for(self, selector):
        return dict(self.directories).get(canonical_flavor_selector(selector)[1:])

    def require_unchanged(self):
        self.guard.require_unchanged()
        current = _selected(self.project, self.selectors)
        if tuple(item.revision.identity.uri for item in current) != self.revisions:
            raise PromotionParentFlavorError(
                "promotion.parent_flavor_changed",
                "selected parent Flavor membership changed during promotion",
            )
        if self.skill_references:
            catalog = load_admitted_skill_catalog(self.project)
            current_refs = {item.ref for item in catalog}
            if not set(self.skill_references).issubset(current_refs):
                raise PromotionParentFlavorError(
                    "promotion.parent_flavor_changed",
                    "selected parent Flavor skill closure changed during promotion",
                )
        self.guard.require_unchanged()

    def materialize(self, staging: Path) -> dict[str, PromotionInputKind]:
        """Copy only captured readable inputs inside the disposable staging tree."""
        self.require_unchanged()
        result = {}
        for relative, content, kind in self.inputs:
            target = staging / relative
            current = staging
            for part in Path(relative).parts:
                current = current / part
                if current.is_symlink():
                    raise PromotionParentFlavorError(
                        "promotion.parent_flavor_collision",
                        "parent Flavor input collides with a symlink",
                    )
            if target.exists():
                existing = PinnedInputClosure().pin(
                    target, boundary=staging, label="promotion-existing-input"
                )
                if existing != content:
                    raise PromotionParentFlavorError(
                        "promotion.parent_flavor_collision",
                        "parent Flavor input would replace different "
                        "initialized authority",
                    )
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            result[relative] = kind
        self.require_unchanged()
        return result


def capture_parent_flavor_inputs(project, selectors, *, proposed_aliases=()):
    """Capture only explicit positive custom selections, never parent defaults."""
    requested = []
    for raw in selectors:
        coordinate, effective = parse_scoped_flavor_selector(raw)
        if not effective.startswith("+"):
            continue
        slot, alias = parse_flavor_selector_body(effective[1:])
        canonical = canonical_flavor_selector("+" + alias)[1:]
        if canonical in KNOWN_FLAVOR_SELECTORS or canonical in proposed_aliases:
            continue
        if coordinate is not None or slot is not None:
            raise PromotionParentFlavorError(
                "promotion.parent_flavor_scope_unsupported",
                "project promotion requires unscoped parent Flavor selections",
            )
        requested.append("+" + canonical)
    if not requested or project is None:
        return None
    requested = tuple(requested)
    snapshot = ProjectConfigurationStore(project.root).read()
    if snapshot.definition != project.definition:
        raise PromotionParentFlavorError(
            "promotion.parent_changed", "parent project changed during promotion"
        )
    guard = PinnedInputClosure.for_project(project)
    guard.pin(
        project.root / "literate.project.json",
        boundary=project.root,
        label="promotion-flavor-parent",
        expected_content=snapshot.content,
    )
    selected = _selected(project, requested)
    captured = {}
    roots = []
    directories = {}

    def add(source, relative, kind, *, identity=None):
        content = guard.pin(
            source,
            boundary=project.root,
            label="parent-flavor-input:" + relative,
            expected_identity=identity,
        )
        previous = captured.get(relative)
        if previous is not None and previous != (content, kind):
            raise PromotionParentFlavorError(
                "promotion.parent_flavor_collision",
                "selected parent Flavors disagree on a readable input",
            )
        captured[relative] = (content, kind)
        return content

    for entry in selected:
        directory = entry.path.parent.relative_to(project.root)
        if directory.parts[:1] != ("flavors",):
            raise PromotionParentFlavorError(
                "promotion.parent_flavor_input_scope",
                "parent Flavor promotion requires a canonical flavors directory",
            )
        for alias in flavor_candidate_aliases(entry.selection):
            directories[alias] = directory.as_posix()
        if entry.path.name != "flavor.md":
            raise PromotionParentFlavorError(
                "promotion.parent_flavor_authoring_required",
                "parent Flavor promotion requires canonical readable Markdown",
            )
        add(
            entry.path,
            (directory / "flavor.md").as_posix(),
            PromotionInputKind.REVIEWED_FLAVOR,
            identity=entry.source_identity,
        )
        references = (
            *entry.definition.specification_fragments,
            *entry.definition.authoring_inputs,
            *(item.content for item in entry.definition.contributions),
        )
        for reference in references:
            source = _resolve_path(
                entry.path.parent, reference.uri, boundary=project.root
            )
            # Normalize only within a prospective project root. Both the source
            # and the eventual target receive independent boundary checks.
            virtual = Path(project.root.anchor) / "__litai_promotion__"
            target = Path(os.path.abspath(virtual / directory / reference.uri))
            if not target.is_relative_to(virtual):
                raise PromotionParentFlavorError(
                    "promotion.parent_flavor_input_scope",
                    "parent Flavor reference escapes the promoted project",
                )
            relative = target.relative_to(virtual)
            if relative.is_relative_to(directory):
                kind = PromotionInputKind.REVIEWED_FLAVOR
            elif relative.parts[:1] == ("skills",) and reference.kind in {
                "agent-skill",
                "specification-to-source-skill",
            }:
                kind = PromotionInputKind.FORWARD_SKILL
            else:
                raise PromotionParentFlavorError(
                    "promotion.parent_flavor_input_scope",
                    "parent Flavor input has no promoted readable authority owner",
                )
            content = add(
                source, relative.as_posix(), kind, identity=reference.identity
            )
            if reference.kind == "specification-to-source-skill":
                roots.append(
                    ResolvedSpecificationToSourceSkill.from_reference(
                        ContentReference(
                            reference.kind, relative.as_posix(), reference.identity
                        ),
                        content,
                        source="selected parent Flavor",
                    )
                )
    closed = ()
    if roots:
        catalog = load_admitted_skill_catalog(project)
        closed = close_specification_to_source_skills(roots, catalog)
        by_identity = {}
        for path in specification_to_source_skill_paths(project):
            content = _bounded_bytes(path, code="promotion.parent_flavor_skill_missing")
            by_identity.setdefault(hashlib.sha256(content).hexdigest(), []).append(path)
        for skill in closed:
            paths = by_identity.get(skill.content_identity.digest, ())
            if not paths:
                raise PromotionParentFlavorError(
                    "promotion.parent_flavor_skill_missing",
                    "selected parent Flavor skill has no exact admitted source",
                )
            path = sorted(paths)[0]
            relative = path.relative_to(project.root)
            if relative.parts[:1] != ("skills",):
                raise PromotionParentFlavorError(
                    "promotion.parent_flavor_input_scope",
                    "parent Flavor skills require a canonical skills directory",
                )
            add(
                path,
                relative.as_posix(),
                PromotionInputKind.FORWARD_SKILL,
                identity=skill.content_identity,
            )
    guard.require_unchanged()
    return ParentFlavorInputs(
        project,
        requested,
        tuple(item.revision.identity.uri for item in selected),
        guard,
        tuple((path, *value) for path, value in sorted(captured.items())),
        tuple(item.ref for item in closed),
        tuple(sorted(directories.items())),
    )
