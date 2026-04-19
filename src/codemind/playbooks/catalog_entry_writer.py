"""
Persist repository catalog entries (SQLite + LanceDB embeddings).

Used by:
  - ``PlaybookTools.save_catalog_entry`` (optional agent tool for other flows)
  - ``PlaybookExecutor`` after ``generate_catalog`` finishes with JSON output

The ``generate_catalog`` playbook no longer relies on the agent calling
``save_catalog_entry``; the executor validates structured output and calls
``CatalogEntryWriter.persist`` directly.
"""

from __future__ import annotations

import json
import traceback
import uuid
from datetime import UTC, datetime
from typing import Any

import numpy as np

from codemind.storage.database import CatalogStore
from codemind.playbooks.json_answer_extract import extract_top_level_json_object

# Backwards-compatible name for callers (e.g. PlaybookExecutor)
parse_json_object_from_answer = extract_top_level_json_object


def normalize_catalog_params(params: dict) -> dict:
    """Normalize nested LLM output to flat catalog persist format."""
    normalized = dict(params)

    catalog_entry = normalized.pop("catalog_entry", None)
    if isinstance(catalog_entry, dict):
        for k, v in catalog_entry.items():
            if k not in normalized or normalized[k] is None or normalized[k] == "" or normalized[k] == []:
                normalized[k] = v

    identity = normalized.pop("identity", None)
    if isinstance(identity, dict):
        if "name" in identity and "repo_name" not in normalized:
            normalized["repo_name"] = identity["name"]
        if "url" in identity and "repo_url" not in normalized:
            normalized["repo_url"] = identity["url"]
        if "branch" in identity and "branch" not in normalized:
            normalized["branch"] = identity["branch"]

    if "name" in normalized and "repo_name" not in normalized:
        normalized["repo_name"] = normalized.pop("name")

    if "url" in normalized and "repo_url" not in normalized:
        normalized["repo_url"] = normalized.pop("url")

    purpose = normalized.pop("purpose", None)
    if isinstance(purpose, dict):
        if "description" not in normalized:
            normalized["description"] = purpose.get("short_summary", "")
        if "summary_detailed" not in normalized:
            normalized["summary_detailed"] = purpose.get("detailed_explanation", "")
        if "summary_high_level" not in normalized:
            normalized["summary_high_level"] = purpose.get("short_summary", "")

    arch = normalized.get("architecture")
    if isinstance(arch, dict):
        parts = []
        if arch.get("layers"):
            val = arch["layers"]
            parts.append("Layers: " + (", ".join(val) if isinstance(val, list) else str(val)))
        if arch.get("design_patterns"):
            val = arch["design_patterns"]
            parts.append("Patterns: " + (", ".join(val) if isinstance(val, list) else str(val)))
        if arch.get("data_flow"):
            val = arch["data_flow"]
            parts.append("Data Flow: " + (", ".join(val) if isinstance(val, list) else str(val)))
        normalized["architecture"] = "\n".join(parts) if parts else json.dumps(arch)

    ts = normalized.get("tech_stack")
    if isinstance(ts, dict):
        all_tech = []
        for key, val in ts.items():
            if isinstance(val, list):
                all_tech.extend(val)
            elif isinstance(val, dict):
                for _sub_key, sub_val in val.items():
                    if isinstance(sub_val, list):
                        all_tech.extend(sub_val)
                    elif isinstance(sub_val, str):
                        all_tech.append(sub_val)
            elif isinstance(val, str):
                all_tech.append(val)
        normalized["tech_stack"] = ", ".join(all_tech) if all_tech else json.dumps(ts)
    elif isinstance(ts, list):
        normalized["tech_stack"] = ", ".join(ts)

    qa = normalized.pop("quality_assessment", None)
    if isinstance(qa, dict):
        if "quality_score" not in normalized:
            normalized["quality_score"] = qa.get("score", 0)
        if "pros" not in normalized and qa.get("pros"):
            normalized["pros"] = qa["pros"]
        if "cons" not in normalized and qa.get("cons"):
            normalized["cons"] = qa["cons"]
    elif isinstance(qa, (int, float)):
        if "quality_score" not in normalized:
            normalized["quality_score"] = int(qa)

    spec = normalized.get("specification")
    if isinstance(spec, dict):
        normalized["specification"] = json.dumps(spec, indent=2)

    if "description" not in normalized:
        normalized["description"] = normalized.get(
            "summary_detailed", normalized.get("summary_high_level", "")
        )

    if "estimated_cost" not in normalized:
        if isinstance(catalog_entry, dict) and "estimated_cost" in catalog_entry:
            normalized["estimated_cost"] = catalog_entry["estimated_cost"]
        elif "quality_assessment" in normalized and isinstance(normalized["quality_assessment"], dict):
            qb = normalized["quality_assessment"]
            if "estimated_cost" in qb:
                normalized["estimated_cost"] = qb["estimated_cost"]

    if "business_functionalities" not in normalized:
        if isinstance(catalog_entry, dict) and "business_functionalities" in catalog_entry:
            normalized["business_functionalities"] = catalog_entry["business_functionalities"]

    holistic = normalized.pop("holistic_documentation", None)
    if isinstance(holistic, dict):
        for hk, hv in holistic.items():
            if hk not in normalized or normalized[hk] in (None, "", []):
                normalized[hk] = hv

    return normalized


class CatalogEntryWriter:
    """Persist catalog metadata and embedded chunks (same pipeline as legacy tool)."""

    def __init__(self, lance_storage, embedder, db) -> None:
        self.lance = lance_storage
        self.embedder = embedder
        self.db = db

    async def persist(self, params: dict) -> dict:
        """Normalize *params* and write to SQLite + LanceDB."""
        try:
            params = normalize_catalog_params(params)
            print(f"[CatalogEntryWriter] Normalized params keys: {list(params.keys())}")

            repo_id = params["repo_id"]
            description = params["description"]
            main_content = params.get("summary_detailed", description)

            if not self.embedder:
                return {"error": "No embedder available", "success": False}

            metadata_dict: dict[str, Any] = {
                "description": params.get("description", ""),
                "architecture": params.get("architecture", ""),
                "tech_stack": params.get("tech_stack", ""),
                "topics": params.get("topics", []),
                "repo_name": params.get("repo_name", ""),
                "repo_url": params.get("repo_url", ""),
                "branch": params.get("branch", ""),
                "org": params.get("org", ""),
                "summary_high_level": params.get("summary_high_level", ""),
                "category": params.get("category", "Uncategorized"),
                "quality_score": params.get("quality_score", 0),
                "specification": params.get("specification", ""),
                "pros": params.get("pros", []),
                "cons": params.get("cons", []),
                "first_author": params.get("first_author", ""),
                "total_commits": params.get("total_commits", 0),
                "last_pr_title": params.get("last_pr_title", ""),
                "estimated_cost": params.get("estimated_cost", 0),
                "estimated_dev_months": params.get("estimated_dev_months", 0),
                "team_size_estimate": params.get("team_size_estimate", 0),
                "complexity_tier": params.get("complexity_tier", "medium"),
                "business_functionalities": params.get("business_functionalities", []),
                "taxonomy_labels": params.get("taxonomy_labels", []),
                "glossary_domain_terms": params.get("glossary_domain_terms", []),
                "ontology_entity_types": params.get("ontology_entity_types", []),
                "ontology_relationships": params.get("ontology_relationships", ""),
                "potential_business_capabilities": params.get("potential_business_capabilities", []),
                "technical_capabilities": params.get("technical_capabilities", []),
                "frameworks_used": params.get("frameworks_used", []),
                "operational_deployment": params.get("operational_deployment", ""),
                "data_and_integrations": params.get("data_and_integrations", ""),
                "security_compliance": params.get("security_compliance", ""),
                "testing_observability": params.get("testing_observability", ""),
                "developer_guide": params.get("developer_guide", ""),
            }

            full_entry = {
                "description": description,
                "summary_detailed": main_content,
                **metadata_dict,
            }
            full_content_str = json.dumps(full_entry, indent=2)

            if self.db:
                try:
                    with self.db.get_session() as session:
                        existing = session.query(CatalogStore).filter_by(repo_id=repo_id).first()
                        if existing:
                            existing.content = full_content_str
                            existing.metadata_json = metadata_dict
                            existing.repo_name = params.get("repo_name")
                            existing.org = params.get("org", "")
                            existing.updated_at = int(datetime.now(UTC).timestamp())
                        else:
                            new_entry = CatalogStore(
                                repo_id=repo_id,
                                repo_name=params.get("repo_name"),
                                org=params.get("org", ""),
                                content=full_content_str,
                                metadata_json=metadata_dict,
                                created_at=int(datetime.now(UTC).timestamp()),
                                updated_at=int(datetime.now(UTC).timestamp()),
                            )
                            session.add(new_entry)
                        session.commit()
                        print(f"[CatalogEntryWriter] Saved catalog entry to SQLite for {repo_id}")
                except Exception as e:
                    print(f"[CatalogEntryWriter] SQLite save failed: {e}")

            chunks: list[str] = []
            _tax = metadata_dict.get("taxonomy_labels") or []
            _fw = metadata_dict.get("frameworks_used") or []
            _cap = (metadata_dict.get("business_functionalities") or [])[:8]
            meta_text = (
                f"Repo: {params.get('repo_name', repo_id)}\n"
                f"Category: {metadata_dict['category']}\n"
                f"Topics: {', '.join(metadata_dict['topics'])}\n"
                f"Taxonomy: {', '.join(_tax)}\n"
                f"Frameworks: {', '.join(_fw)}\n"
                f"Business capabilities: {', '.join(_cap)}\n"
                f"Technical capabilities: {', '.join((metadata_dict.get('technical_capabilities') or [])[:10])}\n"
                f"Stack: {metadata_dict['tech_stack']}\n"
                f"Summary: {metadata_dict['summary_high_level']}\n"
                f"Domain terms: {', '.join((metadata_dict.get('glossary_domain_terms') or [])[:15])}"
            )
            chunks.append(meta_text)

            text_to_split = main_content
            chunk_size = 1000
            overlap = 200
            start = 0
            while start < len(text_to_split):
                end = start + chunk_size
                chunk_text = text_to_split[start:end]
                chunks.append(chunk_text)
                start += chunk_size - overlap

            embeddings = self.embedder.provider.encode_batch(chunks)

            lance_rows = []
            for i, (txt, emb) in enumerate(zip(chunks, embeddings)):
                emb_arr = np.asarray(emb, dtype=np.float32)
                nrm = float(np.linalg.norm(emb_arr))
                # Skip degenerate (all-zero) chunks — Lance cosine distance is NaN for them.
                if nrm <= 1e-12:
                    print(
                        f"[CatalogEntryWriter] Skipping Lance row {i} for {repo_id}: "
                        "zero-norm embedding (cannot rank with cosine)."
                    )
                    continue
                vec = (emb_arr / nrm).tolist()
                lance_rows.append(
                    {
                        "catalog_id": str(uuid.uuid4()),
                        "chunk_id": f"{repo_id}_chunk_{i}",
                        "repo_id": repo_id,
                        "repo_name": params.get("repo_name", repo_id),
                        "chunk_text": txt,
                        "metadata": json.dumps(metadata_dict),
                        "created_at": datetime.now(UTC),
                        "embedding": vec,
                    }
                )

            self.lance.store_catalog_chunks(lance_rows)

            return {
                "success": True,
                "message": f"Catalog entry saved for {repo_id} (SQLite + {len(lance_rows)} chunks)",
            }

        except Exception as e:
            traceback.print_exc()
            return {"error": str(e), "success": False}
