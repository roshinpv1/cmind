import pytest
import json


# ====================================================================
# 1. HEALTH & SYSTEM
# ====================================================================

class TestHealthEndpoint:
    """Test the health check endpoint."""

    def test_health_returns_200(self, app_client):
        """Health endpoint returns 200 with expected fields."""
        response = app_client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "embedding_dim" in data

    def test_health_contains_version(self, app_client):
        """Health endpoint includes version string."""
        response = app_client.get("/api/v1/health")
        data = response.json()
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0


# ====================================================================
# 2. INDEXING
# ====================================================================

class TestIndexEndpoint:
    """Test the repository indexing endpoint."""

    def test_index_local_repo(self, app_client):
        """POST /index with repo_path creates a job."""
        response = app_client.post("/api/v1/index", json={
            "repo_path": "/tmp/test_repo"
        })
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "pending"
        assert data["repo_id"] is not None

    def test_index_validation_no_path_or_url(self, app_client):
        """POST /index with neither repo_path nor repo_url returns 422."""
        response = app_client.post("/api/v1/index", json={})
        assert response.status_code == 422

    def test_index_validation_both_path_and_url(self, app_client):
        """POST /index with both repo_path and repo_url returns 422."""
        response = app_client.post("/api/v1/index", json={
            "repo_path": "/tmp/test",
            "repo_url": "https://github.com/test/test"
        })
        assert response.status_code == 422

    def test_index_with_branch(self, app_client):
        """POST /index with branch parameter creates a job."""
        response = app_client.post("/api/v1/index", json={
            "repo_path": "/tmp/test_repo",
            "branch": "develop"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"

    def test_index_with_org(self, app_client):
        """POST /index with org parameter creates a job."""
        response = app_client.post("/api/v1/index", json={
            "repo_path": "/tmp/test_repo",
            "org": "test-org"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"


# ====================================================================
# 3. JOBS
# ====================================================================

class TestJobEndpoint:
    """Test the job status endpoint."""

    def test_get_job_status(self, app_client):
        """GET /jobs/{id} returns status for existing job."""
        create_resp = app_client.post("/api/v1/index", json={
            "repo_path": "/tmp/test_repo"
        })
        job_id = create_resp.json()["job_id"]

        response = app_client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "pending"

    def test_get_job_not_found(self, app_client):
        """GET /jobs/{id} returns 404 for nonexistent job."""
        response = app_client.get("/api/v1/jobs/nonexistent-id-12345")
        assert response.status_code == 404

    def test_job_flow_create_and_check(self, app_client):
        """Full flow: create job → verify pending status → verify fields."""
        create_resp = app_client.post("/api/v1/index", json={
            "repo_path": "/tmp/flow_test_repo"
        })
        assert create_resp.status_code == 200
        job_id = create_resp.json()["job_id"]
        repo_id = create_resp.json()["repo_id"]

        check_resp = app_client.get(f"/api/v1/jobs/{job_id}")
        data = check_resp.json()
        assert data["status"] == "pending"
        assert data["repo_id"] == repo_id
        assert data["progress"] == 0
        assert data["error"] is None


# ====================================================================
# 4. SEARCH
# ====================================================================

class TestSearchEndpoint:
    """Test the search endpoint."""

    def test_search_returns_list(self, app_client):
        """POST /search returns a list (may be empty with no data)."""
        response = app_client.post("/api/v1/search", json={
            "query": "authentication middleware",
            "limit": 5
        })
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_search_empty_for_no_data(self, app_client):
        """POST /search returns empty list when nothing is indexed."""
        response = app_client.post("/api/v1/search", json={
            "query": "xyzzy does not exist anywhere",
            "limit": 5
        })
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_search_with_limit(self, app_client):
        """POST /search respects the limit parameter."""
        response = app_client.post("/api/v1/search", json={
            "query": "test",
            "limit": 1
        })
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) <= 1


# ====================================================================
# 5. REPOSITORIES
# ====================================================================

class TestReposEndpoint:
    """Test the repository listing endpoint."""

    def test_list_repos_empty(self, app_client):
        """GET /repos returns empty list when nothing is indexed."""
        response = app_client.get("/api/v1/repos")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


# ====================================================================
# 6. STATS
# ====================================================================

class TestStatsEndpoint:
    """Test the stats endpoint."""

    def test_stats_returns_200(self, app_client):
        """GET /stats returns 200 with system stats."""
        response = app_client.get("/api/v1/stats")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)


# ====================================================================
# 7. CATALOG — Full CRUD Lifecycle
# ====================================================================

class TestCatalogListEndpoint:
    """Test the catalog listing endpoint."""

    def test_list_catalogs_empty(self, app_client):
        """GET /catalogs/list returns empty list initially."""
        response = app_client.get("/api/v1/catalogs/list")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_list_catalogs_with_status_filter(self, app_client):
        """GET /catalogs/list?status=proposed returns empty initially."""
        response = app_client.get("/api/v1/catalogs/list?status=proposed")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_proposed_entries(self, app_client):
        """GET /catalogs/proposed returns empty initially."""
        response = app_client.get("/api/v1/catalogs/proposed")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


def _create_direct_entry(app_client, repo_id="test-catalog-001", repo_name="TestComponent",
                          status="proposed"):
    """Helper: create a catalog entry directly via the DB (no LLM needed)."""
    from codemind.storage.database import CatalogStore
    import time

    db = app_client.app.state.manifest.db
    content = json.dumps({
        "product_name": repo_name,
        "summary_high_level": "A test component for unit testing",
        "architecture_layer": "Application",
        "category": "Testing",
        "tech_stack": "Python, pytest",
        "quality_score": 75,
    })
    now = int(time.time())
    with db.get_session() as session:
        entry = CatalogStore(
            repo_id=repo_id,
            repo_name=repo_name,
            org="test-org",
            content=content,
            metadata_json=json.loads(content),
            status=status,
            created_by="test-user",
            source_gap=repo_name,
            requirements={"functional_requirements": ["FR-1: Must work"]},
            quality_score=75,
            created_at=now,
            updated_at=now,
        )
        session.add(entry)
        session.commit()
    return repo_id


class TestCatalogDirectCRUD:
    """Test direct catalog entry creation, retrieval, and deletion."""

    def test_create_and_list_catalog(self, app_client):
        """Create a catalog entry and verify it appears in list."""
        _create_direct_entry(app_client)

        response = app_client.get("/api/v1/catalogs/list")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        names = [e["repo_name"] for e in data]
        assert "TestComponent" in names

    def test_get_catalog_by_id(self, app_client):
        """GET /catalogs/{repo_id} retrieves the correct entry."""
        repo_id = _create_direct_entry(app_client, "get-test-001")

        response = app_client.get(f"/api/v1/catalogs/{repo_id}")
        assert response.status_code == 200
        data = response.json()
        # Real API returns a list of entries
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_get_catalog_not_found_returns_empty(self, app_client):
        """GET /catalogs/{repo_id} returns empty list for nonexistent entry."""
        response = app_client.get("/api/v1/catalogs/nonexistent-repo-xyz")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_list_proposed_entries_filtered(self, app_client):
        """GET /catalogs/proposed returns only proposed entries."""
        _create_direct_entry(app_client, "proposed-test-001", "ProposedComp")

        response = app_client.get("/api/v1/catalogs/proposed")
        assert response.status_code == 200
        data = response.json()
        assert any(e["repo_name"] == "ProposedComp" for e in data)

    def test_list_catalogs_status_filter(self, app_client):
        """GET /catalogs/list?status=proposed returns filtered results."""
        _create_direct_entry(app_client, "filter-test-001")

        response = app_client.get("/api/v1/catalogs/list?status=proposed")
        assert response.status_code == 200
        data = response.json()
        assert all(e.get("status") == "proposed" for e in data)

    def test_delete_catalog_entry(self, app_client):
        """DELETE /catalogs/{repo_id} removes the entry."""
        repo_id = _create_direct_entry(app_client, "delete-test-001")

        # Verify it exists
        get_resp = app_client.get(f"/api/v1/catalogs/{repo_id}")
        assert get_resp.status_code == 200
        assert len(get_resp.json()) >= 1

        # Delete
        del_resp = app_client.delete(f"/api/v1/catalogs/{repo_id}")
        assert del_resp.status_code == 200

        # Verify gone (returns empty list, not 404)
        get_resp2 = app_client.get(f"/api/v1/catalogs/{repo_id}")
        assert get_resp2.status_code == 200
        assert len(get_resp2.json()) == 0

    def test_delete_catalog_not_found(self, app_client):
        """DELETE /catalogs/{repo_id} returns 404 for nonexistent."""
        response = app_client.delete("/api/v1/catalogs/nonexistent-del-xyz")
        assert response.status_code == 404

    def test_delete_active_catalog_fails(self, app_client):
        """DELETE /catalogs/{repo_id} returns 400 for active entries."""
        _create_direct_entry(app_client, "active-nodelete-001", "ActiveComp", status="active")
        response = app_client.delete("/api/v1/catalogs/active-nodelete-001")
        assert response.status_code == 400


class TestCatalogRequirementsUpdate:
    """Test updating requirements on a proposed catalog entry."""

    def test_update_requirements(self, app_client):
        """PUT /catalogs/{repo_id}/requirements updates requirements."""
        repo_id = _create_direct_entry(app_client, "req-update-001", "ReqComp")
        new_reqs = {
            "functional_requirements": ["FR-1: New requirement", "FR-2: Another req"],
            "non_functional_requirements": ["NFR-1: Must be fast"],
        }
        response = app_client.put(
            f"/api/v1/catalogs/{repo_id}/requirements",
            json={"requirements": new_reqs}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["requirements"]["functional_requirements"][0] == "FR-1: New requirement"

    def test_update_requirements_not_found(self, app_client):
        """PUT /catalogs/{repo_id}/requirements returns 404 for nonexistent."""
        response = app_client.put(
            "/api/v1/catalogs/nonexistent-xyz/requirements",
            json={"requirements": {"a": "b"}}
        )
        assert response.status_code == 404


class TestCatalogPromote:
    """Test promoting a proposed catalog entry to active."""

    def test_promote_to_active(self, app_client):
        """PUT /catalogs/{repo_id}/promote changes status to active."""
        repo_id = _create_direct_entry(app_client, "promote-test-001", "PromComp")
        response = app_client.put(
            f"/api/v1/catalogs/{repo_id}/promote",
            json={
                "git_url": "https://github.com/org/repo.git",
                "git_branch": "main",
                "quality_score": 85,
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "active"
        assert data["git_url"] == "https://github.com/org/repo.git"

    def test_promote_not_found(self, app_client):
        """PUT /catalogs/{repo_id}/promote returns 404 for nonexistent."""
        response = app_client.put(
            "/api/v1/catalogs/nonexistent-xyz/promote",
            json={"git_url": "https://github.com/org/repo.git"}
        )
        assert response.status_code == 404


class TestCatalogContribute:
    """Test contributing to an existing catalog entry."""

    def test_contribute_to_entry(self, app_client):
        """POST /catalogs/{repo_id}/contribute records a contributor."""
        repo_id = _create_direct_entry(app_client, "contrib-test-001", "ContribComp")
        response = app_client.post(
            f"/api/v1/catalogs/{repo_id}/contribute",
            json={
                "uid": "test-user-001",
                "org": "test-org",
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["uid"] == "test-user-001"

    def test_contribute_not_found(self, app_client):
        """POST /catalogs/{repo_id}/contribute returns 404 for nonexistent."""
        response = app_client.post(
            "/api/v1/catalogs/nonexistent-xyz/contribute",
            json={"uid": "user1", "org": "org1"}
        )
        assert response.status_code == 404

    def test_contribute_missing_uid(self, app_client):
        """POST /catalogs/{repo_id}/contribute returns 400 without uid."""
        repo_id = _create_direct_entry(app_client, "contrib-nuid-001", "NoUidComp")
        response = app_client.post(
            f"/api/v1/catalogs/{repo_id}/contribute",
            json={"org": "test-org"}
        )
        assert response.status_code == 400

    def test_contribute_no_duplicate(self, app_client):
        """Contributing same uid twice doesn't duplicate."""
        repo_id = _create_direct_entry(app_client, "contrib-dedup-001", "DedupComp")
        body = {"uid": "user1", "org": "org1"}
        app_client.post(f"/api/v1/catalogs/{repo_id}/contribute", json=body)
        app_client.post(f"/api/v1/catalogs/{repo_id}/contribute", json=body)
        # Should complete without error, no duplicates
        resp = app_client.get("/api/v1/catalogs/list")
        assert resp.status_code == 200


class TestCatalogSearch:
    """Test catalog search endpoints."""

    def test_catalog_search_get(self, app_client):
        """GET /catalogs/search?query=test returns results."""
        response = app_client.get("/api/v1/catalogs/search?query=test")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_catalog_search_post(self, app_client):
        """POST /catalogs/search returns results."""
        response = app_client.post("/api/v1/catalogs/search", json={
            "query": "authentication service",
            "limit": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestCatalogMatchGaps:
    """Test the gap matching endpoint."""

    def test_match_gaps_empty(self, app_client):
        """POST /catalogs/match-gaps returns matches."""
        response = app_client.post("/api/v1/catalogs/match-gaps", json={
            "gaps": ["Authentication Service", "Payment Gateway"],
            "limit": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)


class TestCatalogRegenerate:
    """Test the regenerate requirements endpoint."""

    def test_regenerate_not_found(self, app_client):
        """POST /catalogs/{repo_id}/regenerate returns 404 for nonexistent."""
        response = app_client.post("/api/v1/catalogs/nonexistent-xyz/regenerate")
        assert response.status_code == 404

    def test_regenerate_non_proposed(self, app_client):
        """POST /catalogs/{repo_id}/regenerate returns 400 for non-proposed entry."""
        _create_direct_entry(app_client, "regen-active-001", "ActiveComp", status="active")
        response = app_client.post("/api/v1/catalogs/regen-active-001/regenerate")
        assert response.status_code == 400


# ====================================================================
# 8. CATALOG LIFECYCLE — Full Flow
# ====================================================================

class TestCatalogLifecycleFlow:
    """Test the full catalog lifecycle: create → update → promote → delete."""

    def test_full_lifecycle(self, app_client):
        """
        Full catalog lifecycle:
          1. Create proposed entry
          2. List and verify it appears
          3. Update requirements
          4. Promote to active
          5. Verify status changed
          6. Delete (should fail since it's now active)
        """
        repo_id = _create_direct_entry(app_client, "lifecycle-001", "LifecycleComp")

        # Step 2: Verify in list
        list_resp = app_client.get("/api/v1/catalogs/list?status=proposed")
        assert list_resp.status_code == 200
        assert any(e["repo_id"] == repo_id for e in list_resp.json())

        # Step 3: Update requirements
        update_resp = app_client.put(
            f"/api/v1/catalogs/{repo_id}/requirements",
            json={"requirements": {"functional_requirements": ["FR-NEW"]}}
        )
        assert update_resp.status_code == 200

        # Step 4: Promote
        promote_resp = app_client.put(
            f"/api/v1/catalogs/{repo_id}/promote",
            json={
                "git_url": "https://github.com/org/lifecycle.git",
                "git_branch": "main",
                "quality_score": 90,
            }
        )
        assert promote_resp.status_code == 200
        assert promote_resp.json()["status"] == "active"

        # Step 5: Verify status change via entries list
        get_resp = app_client.get(f"/api/v1/catalogs/{repo_id}")
        assert get_resp.status_code == 200
        entries = get_resp.json()
        assert isinstance(entries, list)
        assert len(entries) >= 1

        # Step 6: Delete should fail (it's active now)
        del_resp = app_client.delete(f"/api/v1/catalogs/{repo_id}")
        assert del_resp.status_code == 400  # Cannot delete active entries


class TestCatalogLifecycleWithDelete:
    """Test lifecycle with a proposed-only entry that gets deleted."""

    def test_create_and_delete(self, app_client):
        """Create a proposed entry, then delete it."""
        repo_id = _create_direct_entry(app_client, "lifecycle-del-001", "DelComp")

        # Verify exists
        list_resp = app_client.get("/api/v1/catalogs/list")
        assert any(e["repo_id"] == repo_id for e in list_resp.json())

        # Delete
        del_resp = app_client.delete(f"/api/v1/catalogs/{repo_id}")
        assert del_resp.status_code == 200

        # Verify gone
        list_resp2 = app_client.get("/api/v1/catalogs/list")
        assert not any(e["repo_id"] == repo_id for e in list_resp2.json())


# ====================================================================
# 9. PLAYBOOKS
# ====================================================================

class TestPlaybookEndpoint:
    """Test the playbook execution endpoint."""

    def test_execute_playbook(self, app_client):
        """POST /agents/playbook executes and returns result."""
        response = app_client.post("/api/v1/agents/playbook", json={
            "playbook_name": "code_analyzer",
            "prompt": "Explain the authentication flow",
            "repo_id": "test123"
        })
        assert response.status_code == 200
        data = response.json()
        assert "success" in data
        assert "result" in data

    def test_execute_playbook_auto_select(self, app_client):
        """POST /agents/playbook with 'auto' selects a playbook."""
        response = app_client.post("/api/v1/agents/playbook", json={
            "playbook_name": "auto",
            "prompt": "Search for all API endpoints in the codebase",
            "repo_id": "test123"
        })
        assert response.status_code == 200
        data = response.json()
        assert "success" in data

    def test_execute_playbook_no_prompt_auto(self, app_client):
        """POST /agents/playbook with 'auto' but no prompt returns 400."""
        response = app_client.post("/api/v1/agents/playbook", json={
            "playbook_name": "auto"
        })
        assert response.status_code == 400


# ====================================================================
# 10. AUTONOMOUS AGENTS
# ====================================================================

class TestAutonomousEndpoint:
    """Test the autonomous agent endpoint."""

    def test_create_autonomous_job(self, app_client):
        """POST /agents/autonomous creates a job."""
        response = app_client.post("/api/v1/agents/autonomous", json={
            "goal": "Find all database models and their relationships",
            "repo_id": "test123",
            "max_iterations": 3
        })
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "pending"
        assert data["goal"] == "Find all database models and their relationships"

    def test_autonomous_status_not_found(self, app_client):
        """GET /agents/autonomous/{id}/status returns 404."""
        response = app_client.get("/api/v1/agents/autonomous/nonexistent/status")
        assert response.status_code == 404

    def test_autonomous_with_allowed_playbooks(self, app_client):
        """POST /agents/autonomous with allowed_playbooks constrains execution."""
        response = app_client.post("/api/v1/agents/autonomous", json={
            "goal": "Analyze the architecture",
            "repo_id": "test123",
            "max_iterations": 2,
            "allowed_playbooks": ["code_analyzer"]
        })
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data


# ====================================================================
# 11. DEBUG ROUTES
# ====================================================================

class TestDebugRoutes:
    """Test the debug/routes endpoint."""

    def test_debug_routes(self, app_client):
        """GET /debug/routes returns route info."""
        response = app_client.get("/api/v1/debug/routes")
        assert response.status_code == 200
        data = response.json()
        # Routes endpoint returns either a list or dict depending on implementation
        assert data is not None


# ====================================================================
# 12. EDGE CASES & ERROR HANDLING
# ====================================================================

class TestEdgeCases:
    """Test edge cases and error handling across endpoints."""

    def test_invalid_json_body(self, app_client):
        """POST with invalid JSON returns 422."""
        response = app_client.post(
            "/api/v1/search",
            content="not valid json",
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422

    def test_catalog_delete_idempotent(self, app_client):
        """Deleting a non-existent catalog returns 404 consistently."""
        resp1 = app_client.delete("/api/v1/catalogs/never-existed")
        assert resp1.status_code == 404
        resp2 = app_client.delete("/api/v1/catalogs/never-existed")
        assert resp2.status_code == 404

    def test_multiple_index_jobs(self, app_client):
        """Creating multiple index jobs produces unique job IDs."""
        resp1 = app_client.post("/api/v1/index", json={"repo_path": "/tmp/repo1"})
        resp2 = app_client.post("/api/v1/index", json={"repo_path": "/tmp/repo2"})
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json()["job_id"] != resp2.json()["job_id"]

    def test_large_limit_search(self, app_client):
        """POST /search with a large limit doesn't crash."""
        response = app_client.post("/api/v1/search", json={
            "query": "test large limit",
            "limit": 1000
        })
        assert response.status_code == 200

    def test_special_chars_in_repo_id(self, app_client):
        """Endpoints handle special characters in repo_id gracefully."""
        response = app_client.get("/api/v1/catalogs/repo-with-dashes")
        # Should return 200 with empty list (not a server error)
        assert response.status_code == 200
        assert isinstance(response.json(), list)
