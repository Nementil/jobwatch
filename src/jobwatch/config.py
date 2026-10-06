"""Loading config.yaml, with a private overlay and per-source filtering.

Two files, read in order:

    config.yaml           your search: keywords, locations, public sources
    config.private.yaml   anything that must never be published: a source a
                          site allowed you personally to read, its cookie
                          file, notes naming people. Optional, gitignored.

The private file is merged ON TOP of the public one:

* `sources` are merged by `name`: a private entry with a new name is added,
  one with an existing name replaces that source's settings key by key
  (so `{name: thehub, enabled: true}` turns a public, disabled entry on).
* `key+:` appends to a list instead of replacing it, so the private file can
  add a keyword or a location without restating the whole list.
* any other key replaces the public value.

This exists because the alternative was keeping whole commits off GitHub to
protect one source's permission, which left the public code and the code
actually running out of step.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

from .models import Job
from .sources import Source, filter_jobs

log = logging.getLogger(__name__)

PRIVATE_NAME = "config.private.yaml"


def merge(public: dict, private: dict) -> dict:
    """The public config with the private one applied. Pure."""
    out = dict(public)
    for key, value in (private or {}).items():
        if key == "sources":
            out["sources"] = _merge_sources(public.get("sources") or [], value or [])
        elif key.endswith("+"):
            base = key[:-1]
            out[base] = list(out.get(base) or []) + list(value or [])
        else:
            out[key] = value
    return out


def _merge_sources(public: Sequence[dict], private: Sequence[dict]) -> list[dict]:
    merged = [dict(s) for s in public]
    index = {s.get("name"): i for i, s in enumerate(merged)}
    for entry in private:
        name = entry.get("name")
        if name in index:
            merged[index[name]].update(entry)
        else:
            index[name] = len(merged)
            merged.append(dict(entry))
    return merged


def load(path: str | Path) -> dict:
    """config.yaml plus config.private.yaml next to it, if there is one."""
    path = Path(path)
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    private = path.with_name(PRIVATE_NAME)
    if private.exists():
        config = merge(config, yaml.safe_load(private.read_text(encoding="utf-8")) or {})
        log.info("merged %s", private.name)
    return config


def collect_matching(config: dict, sources: Iterable[Source] | None = None,
                     keywords: Sequence[str] | None = None,
                     locations: Sequence[str] | None = None) -> tuple[list[Job], int]:
    """Collect every source and filter each with its own location rule.

    Returns (matched, collected). A source with `locations:` in its config is
    filtered by that list instead of the global one: the studio feeds can
    accept Stockholm while the general boards do not. `keywords` and
    `locations` override the config's (the GUI's search boxes); a source's
    own `locations` still wins over both.
    """
    from .cli import build_sources

    keywords = config.get("keywords", []) if keywords is None else keywords
    default_locations = config.get("locations", []) if locations is None else locations
    excl_kw = config.get("exclude_keywords", [])
    excl_co = config.get("exclude_companies", [])

    matched: list[Job] = []
    collected = 0
    for source in (build_sources(config) if sources is None else sources):
        jobs = source.collect()
        collected += len(jobs)
        own = getattr(source, "locations", None)
        matched += filter_jobs(jobs, keywords, own if own is not None else default_locations,
                               excl_kw, excl_co)
    return matched, collected


def attach_locations(source: Source, entry: dict) -> Source:
    """Copy a config entry's own `locations:` onto the built source."""
    if entry.get("locations") is not None:
        source.locations = list(entry["locations"])     # type: ignore[attr-defined]
    return source
