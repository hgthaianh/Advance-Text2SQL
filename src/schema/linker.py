"""Deterministic alias-driven linking, independent of intent, permissions and SQL."""

import re
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

from src.nlp.normalizer import Normalizer, strip_accents
from src.schema.registry import ROOT, SchemaRegistry, load_yaml
from src.schema.relationship_graph import RelationshipGraph
from src.shared.models import NormalizedText, SchemaCandidate, SchemaLinkResult

# Stop words are ignored only in approximate matching and unmatched-term reporting.
# They are retained inside exact business phrases.
STOP_WORDS = frozenset("cua cac cho va la duoc trong theo tai voi nhung mot hay toi xem liet ke top the of a an and by in".split())
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class RankingConfig:
    exact_score: float = 1.0
    accent_score: float = 0.98
    token_score: float = 0.86
    prefix_score: float = 0.82
    fuzzy_score: float = 0.79
    token_match_min: float = 0.80
    fuzzy_match_min: float = 0.80
    context_boost: float = 0.04
    relationship_boost: float = 0.01
    context_reserve: float = 0.05
    schema_penalty: float = 0.02
    automatic_penalty: float = 0.04
    inferred_penalty: float = 0.06
    concept_penalty: float = 0.08
    ambiguity_margin: float = 0.015
    minimum_score: float = 0.60
    min_fuzzy_chars: int = 5

    def __post_init__(self) -> None:
        scores = (self.exact_score, self.accent_score, self.token_score, self.prefix_score, self.fuzzy_score)
        if not all(0 <= score <= 1 for score in scores) or list(scores) != sorted(scores, reverse=True):
            raise ValueError("Match scores must descend from exact to fuzzy within [0, 1]")
        for name in ("token_match_min", "fuzzy_match_min", "minimum_score"):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        for name in ("context_boost", "relationship_boost", "context_reserve", "schema_penalty", "automatic_penalty", "inferred_penalty", "concept_penalty", "ambiguity_margin"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.min_fuzzy_chars < 1:
            raise ValueError("min_fuzzy_chars must be positive")


def _phrase(text: str) -> str:
    return " ".join(_TOKEN.findall(Normalizer().normalize(text).normalized_text))


def _fold(text: str) -> str:
    return strip_accents(_phrase(text))


def _singular(word: str) -> str:
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "sses", "xes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def identifier_aliases(identifier: str) -> list[str]:
    """Derive conservative spaced/singular aliases; trim foreign-key suffixes."""
    words = identifier.replace(".", " ").replace("_", " ").split()
    if not words:
        return []
    aliases = [" ".join(words), " ".join(_singular(w) for w in words)]
    if len(words) > 1 and words[-1] == "id":
        aliases.extend((" ".join(words[:-1]), words[-2]))
    return list(dict.fromkeys(aliases))


def validate_aliases(data: dict, registry: SchemaRegistry) -> dict:
    """Validate references and per-target duplicates, preserving cross-target ambiguity."""
    if set(data) - {"version", "tables", "columns", "concepts"}:
        raise ValueError("Unknown alias dictionary section")
    result = {"tables": {}, "columns": {}, "concepts": {}}
    for section in result:
        entries = data.get(section, {})
        if not isinstance(entries, dict):
            raise ValueError(f"{section} must be a mapping")
        for name, entry in entries.items():
            if not isinstance(entry, dict) or set(entry) - ({"aliases", "tables", "columns"} if section == "concepts" else {"aliases"}):
                raise ValueError(f"Invalid alias entry: {name}")
            aliases = entry.get("aliases")
            if not isinstance(aliases, list) or not aliases or any(not isinstance(a, str) or not _fold(a) for a in aliases):
                raise ValueError(f"Nonempty alias list required: {name}")
            normalized = [_fold(a) for a in aliases]
            if len(normalized) != len(set(normalized)):
                raise ValueError(f"Duplicate aliases (including accent variants): {name}")
            if section == "tables":
                canonical = registry.resolve_table(name)
            elif section == "columns":
                canonical = registry.resolve_column(name)
            else:
                canonical = name
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("Concept name cannot be empty")
                for key in ("tables", "columns"):
                    if not isinstance(entry.get(key, []), list):
                        raise ValueError(f"Concept {key} must be a list: {name}")
                if not entry.get("tables") and not entry.get("columns"):
                    raise ValueError(f"Concept has no schema targets: {name}")
                entry = dict(entry,
                             tables=[registry.resolve_table(t) for t in entry.get("tables", [])],
                             columns=[registry.resolve_column(c) for c in entry.get("columns", [])])
            if canonical in result[section]:
                raise ValueError(f"Duplicate canonical reference: {canonical}")
            result[section][canonical] = entry
    return result


def alias_coverage(registry: SchemaRegistry, aliases: dict) -> dict[str, int | float]:
    """Return coverage counts after validation; all remaining items have automatic aliases."""
    aliases = validate_aliases(aliases, registry)
    table_count, column_count = len(registry.tables), len(registry.columns)
    return {
        "tables_indexed": table_count, "columns_indexed": column_count,
        "tables_explicit": len(aliases["tables"]), "columns_explicit": len(aliases["columns"]),
        "table_coverage_percent": 100 * len(aliases["tables"]) / table_count,
        "column_coverage_percent": 100 * len(aliases["columns"]) / column_count,
        "columns_automatic_only": column_count - len(aliases["columns"]),
        "explicit_aliases": sum(len(e["aliases"]) for group in aliases.values() for e in group.values()),
        "unknown_references": 0,
    }


@dataclass(frozen=True)
class _Alias:
    name: str
    kind: Literal["table", "column", "concept"]
    text: str
    surface: str
    source: Literal["manual", "schema", "automatic"]


@dataclass
class _Hit:
    candidate: SchemaCandidate
    start: int
    end: int
    cap: float


def _trigrams(text: str) -> set[str]:
    return {text[i:i + 3] for i in range(len(text) - 2)}


class SchemaLinker:
    """Reusable in-memory index. No YAML reads occur during link calls.

    Use schema-qualified output identifiers. candidate_tables provides ranking
    context, not an allowlist. Scores are evidence ranks, not probabilities.
    """

    def __init__(
        self, registry: SchemaRegistry | None = None,
        aliases_path: str | Path = ROOT / "schema/aliases.yaml",
        relationship_graph: RelationshipGraph | None = None,
        config: RankingConfig | None = None,
    ) -> None:
        self.registry = registry if registry is not None else SchemaRegistry()
        self.config = config or RankingConfig()
        self.graph = relationship_graph if relationship_graph is not None else RelationshipGraph(self.registry)
        self.aliases = validate_aliases(load_yaml(aliases_path), self.registry)
        self.index: dict[str, list[_Alias]] = defaultdict(list)
        self.token_index: dict[str, set[str]] = defaultdict(set)
        self.trigram_index: dict[str, set[str]] = defaultdict(set)
        for section, kind in (("tables", "table"), ("columns", "column"), ("concepts", "concept")):
            for name, entry in self.aliases[section].items():
                self._add(name, kind, entry["aliases"], "manual")
        for name, table in self.registry.tables.items():
            self._add(name, "table", [*table.aliases, table.label, table.description], "schema")
            self._add(name, "table", identifier_aliases(table.name) + identifier_aliases(name), "automatic")
            for column in table.columns:
                key = f"{name}.{column.name}"
                self._add(key, "column", [*column.aliases, column.label, column.description], "schema")
                automatic = identifier_aliases(column.name) + identifier_aliases(f"{table.name}.{column.name}") + identifier_aliases(key)
                if column.name == "id":
                    # A bare table mention does not implicitly ask for its primary key.
                    automatic = [alias for alias in automatic if alias.split()[-1] == "id"]
                self._add(key, "column", automatic, "automatic")
        self.max_tokens = max(len(key.split()) for key in self.index)
        for key in self.index:
            for token in set(key.split()) - STOP_WORDS:
                self.token_index[token].add(key)
            for trigram in _trigrams(key):
                self.trigram_index[trigram].add(key)

    def _add(self, name: str, kind: str, aliases: list[str | None], source: str) -> None:
        for alias in aliases:
            if not alias:
                continue
            surface = _phrase(alias)
            key = strip_accents(surface)
            if not key or set(key.split()) <= STOP_WORDS:
                continue
            # Earlier sources have priority when their normalized alias is identical.
            if not any(e.name == name and e.kind == kind for e in self.index[key]):
                self.index[key].append(_Alias(name, kind, alias, surface, source))

    def _hits(self, text: str | NormalizedText) -> tuple[list[_Hit], list[str]]:
        normalized = text.normalized_text if isinstance(text, NormalizedText) else Normalizer().normalize(text).normalized_text
        tokens = list(_TOKEN.finditer(normalized))
        surfaces = [m.group() for m in tokens]
        folded = [strip_accents(word) for word in surfaces]
        hits: list[_Hit] = []

        def add(key: str, start: int, end: int, mode: str, cap: float) -> None:
            for entry in self.index[key]:
                match_type, maximum = mode, cap
                if mode == "exact" and " ".join(surfaces[start:end]) != entry.surface:
                    match_type, maximum = "accent_insensitive", self.config.accent_score
                penalty = {"manual": 0, "schema": self.config.schema_penalty, "automatic": self.config.automatic_penalty}[entry.source]
                score = maximum - self.config.context_reserve - penalty
                hits.append(_Hit(SchemaCandidate(
                    name=entry.name, candidate_type=entry.kind, score=max(0, score),
                    matched_text=normalized[tokens[start].start():tokens[end - 1].end()],
                    matched_alias=entry.text, match_type=match_type, alias_source=entry.source,
                ), start, end, maximum))

        for start in range(len(tokens)):
            for end in range(start + 1, min(len(tokens), start + self.max_tokens) + 1):
                key = " ".join(folded[start:end])
                if key in self.index:
                    add(key, start, end, "exact", self.config.exact_score)
        # A longer recognized phrase wins over its contained fragments. Equal spans
        # remain together so genuine cross-target ambiguity is never discarded.
        spans = {(hit.start, hit.end) for hit in hits}
        maximal = {span for span in spans if not any(a <= span[0] and b >= span[1] and (a, b) != span for a, b in spans)}
        hits = [hit for hit in hits if (hit.start, hit.end) in maximal]
        covered = {i for a, b in maximal if b - a > 1 for i in range(a, b)}
        # Complete multiword aliases block weaker overlapping matches. Single-word
        # hits do not block longer typo/reordered phrases that may explain them.
        for start in range(len(tokens)):
            if start in covered or folded[start] in STOP_WORDS or folded[start].isdigit():
                continue
            for end in range(start + 1, min(len(tokens), start + self.max_tokens) + 1):
                if end - 1 in covered:
                    break
                if (start, end) in spans:
                    continue
                window = " ".join(folded[start:end])
                words = set(folded[start:end]) - STOP_WORDS
                choices: set[str] = set()
                for word in words:
                    choices.update(self.token_index.get(word, ()))
                if len(window) >= self.config.min_fuzzy_chars:
                    counts: dict[str, int] = defaultdict(int)
                    grams = _trigrams(window)
                    for gram in grams:
                        for key in self.trigram_index.get(gram, ()):
                            counts[key] += 1
                    choices.update(key for key, count in counts.items() if count >= max(1, len(grams) // 3))
                for key in sorted(choices):
                    if abs(len(key.split()) - (end - start)) > 1:
                        continue
                    target_words = set(key.split()) - STOP_WORDS
                    overlap = len(words & target_words) / max(1, len(words | target_words))
                    if len(words) > 1 and len(target_words) > 1 and overlap >= self.config.token_match_min:
                        add(key, start, end, "token", self.config.token_score)
                    elif len(window) >= self.config.min_fuzzy_chars and len(key) >= self.config.min_fuzzy_chars:
                        similarity = SequenceMatcher(None, window, key, autojunk=False).ratio()
                        if similarity < self.config.fuzzy_match_min:
                            continue
                        if key.startswith(window) or window.startswith(key) or key.endswith(window) or window.endswith(key):
                            add(key, start, end, "prefix", self.config.prefix_score)
                        else:
                            add(key, start, end, "fuzzy", self.config.fuzzy_score)
        return hits, folded

    @staticmethod
    def _best(candidates: list[SchemaCandidate]) -> list[SchemaCandidate]:
        ordered = sorted(candidates, key=lambda c: (-c.score, -len(c.matched_alias.split()), c.name, c.matched_alias))
        unique = {}
        for candidate in ordered:
            unique.setdefault(candidate.name, candidate)
        return list(unique.values())

    def _rank(self, text: str | NormalizedText, candidate_tables: list[str] | None = None) -> SchemaLinkResult:
        hits, words = self._hits(text)
        context = {self.registry.resolve_table(t) for t in (candidate_tables or [])}
        context.update(h.candidate.name for h in hits if h.candidate.candidate_type == "table" and h.candidate.match_type in ("exact", "accent_insensitive"))
        # Qualified, distinctive column phrases also establish table context.
        for hit in hits:
            c = hit.candidate
            if c.candidate_type == "column" and c.alias_source == "manual" and c.match_type in ("exact", "accent_insensitive"):
                targets = {e.name for e in self.index[_fold(c.matched_alias)] if e.kind == "column"}
                if len(targets) == 1:
                    context.add(c.name.rsplit(".", 1)[0])
        candidates: list[SchemaCandidate] = []
        matched: set[int] = set()
        for hit in hits:
            c = hit.candidate.model_copy()
            table = c.name.rsplit(".", 1)[0] if c.candidate_type == "column" else c.name
            boost = self.config.context_boost if table in context else 0
            related = table in self.graph.adjacency and bool((self.graph.adjacency[table] - {table}) & context)
            if related:
                boost += self.config.relationship_boost
                c.reason = "foreign_key_neighbor_of_context"
            c.score = round(min(hit.cap, c.score + boost), 6)
            if c.score < self.config.minimum_score:
                continue
            matched.update(range(hit.start, hit.end))
            candidates.append(c)
            if c.candidate_type == "column":
                candidates.append(c.model_copy(update={
                    "name": table, "candidate_type": "table",
                    "score": round(max(0, c.score - self.config.inferred_penalty), 6),
                    "reason": "owner_of_matched_column",
                }))
            elif c.candidate_type == "concept":
                entry = self.aliases["concepts"][c.name]
                for kind, names in (("table", entry["tables"]), ("column", entry["columns"])):
                    for name in names:
                        candidates.append(c.model_copy(update={
                            "name": name, "candidate_type": kind,
                            "score": round(max(0, c.score - self.config.concept_penalty), 6),
                            "match_type": "related_business_concept", "reason": c.name,
                        }))
        grouped = {kind: self._best([c for c in candidates if c.candidate_type == kind and c.score >= self.config.minimum_score]) for kind in ("table", "column", "concept")}
        ambiguous = set()
        for kind in ("table", "column"):
            by_text: dict[str, list[SchemaCandidate]] = defaultdict(list)
            for c in grouped[kind]:
                if c.match_type != "related_business_concept":
                    by_text[c.matched_text].append(c)
            for phrase, group in by_text.items():
                if len(group) > 1 and group[0].score - group[1].score <= self.config.ambiguity_margin:
                    ambiguous.add(phrase)
        return SchemaLinkResult(
            tables=grouped["table"], columns=grouped["column"], concepts=grouped["concept"],
            unmatched_terms=list(dict.fromkeys(w for i, w in enumerate(words) if i not in matched and w not in STOP_WORDS and not w.isdigit())),
            ambiguous_terms=sorted(ambiguous),
        )

    def link_tables(self, text: str | NormalizedText, top_k: int = 10) -> list[SchemaCandidate]:
        """Rank semantic table mentions and owners of matched business columns."""
        if top_k < 0:
            raise ValueError("top_k cannot be negative")
        return self._rank(text).tables[:top_k]

    def link_columns(self, text: str | NormalizedText, candidate_tables: list[str] | None = None, top_k: int = 20) -> list[SchemaCandidate]:
        """Rank columns; table context boosts candidates without hiding alternatives."""
        if top_k < 0:
            raise ValueError("top_k cannot be negative")
        return self._rank(text, candidate_tables).columns[:top_k]

    def link(self, text: str | NormalizedText, *, table_top_k: int = 10, column_top_k: int = 20) -> SchemaLinkResult:
        """Link tables, columns and concepts, and expose ambiguity and unmatched tokens."""
        if table_top_k < 0 or column_top_k < 0:
            raise ValueError("top_k cannot be negative")
        result = self._rank(text)
        result.tables = result.tables[:table_top_k]
        result.columns = result.columns[:column_top_k]
        return result

    def coverage(self) -> dict[str, int | float]:
        return alias_coverage(self.registry, self.aliases)
