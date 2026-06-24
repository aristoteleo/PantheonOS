"""HTTP client for Pantheon Store API."""

import os
import httpx
from typing import Optional
from .auth import StoreAuth


DEFAULT_HUB_URL = "https://pantheon.aristoteleo.com"


class StoreClient:
    """HTTP client for Pantheon Store API."""

    def __init__(self, hub_url: str = None, auth: StoreAuth = None):
        self.auth = auth or StoreAuth()
        self.hub_url = (hub_url or os.environ.get("PANTHEON_HUB_URL") or self.auth.hub_url or DEFAULT_HUB_URL).rstrip("/")

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.auth.token:
            headers["Authorization"] = f"Bearer {self.auth.token}"
        return headers

    def _check_auth(self):
        if not self.auth.is_logged_in:
            raise SystemExit("Not logged in. Run: pantheon store login")

    # --- Auth ---

    async def login(self, username: str, password: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.hub_url}/api/auth/login",
                data={"username": username, "password": password},
            )
            if resp.status_code == 401:
                raise SystemExit("Login failed: incorrect username or password")
            resp.raise_for_status()
            data = resp.json()
            user = data.get("user", {})
            self.auth.save(
                self.hub_url, data["access_token"],
                username, user.get("id", ""),
            )
            return data

    # --- Public ---

    async def search(self, q: str = None, type: str = None,
                     category: str = None, source: str = None,
                     limit: int = 20, offset: int = 0) -> dict:
        params = {k: v for k, v in {
            "q": q, "type": type, "category": category, "source": source,
            "limit": limit, "offset": offset,
        }.items() if v is not None}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.hub_url}/api/store/packages", params=params)
            resp.raise_for_status()
            return resp.json()

    async def get_package(self, package_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.hub_url}/api/store/packages/{package_id}")
            resp.raise_for_status()
            return resp.json()

    async def list_versions(self, package_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.hub_url}/api/store/packages/{package_id}/versions")
            resp.raise_for_status()
            return resp.json()

    async def download(self, package_id: str, version: str = None) -> dict:
        url = f"{self.hub_url}/api/store/packages/{package_id}/download"
        if version:
            url += f"/{version}"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()

    # --- Auth-required ---

    async def publish(self, data: dict) -> dict:
        self._check_auth()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.hub_url}/api/store/packages",
                json=data, headers=self._headers(),
            )
            if resp.status_code == 409:
                detail = resp.json().get("detail", "Name already taken")
                raise SystemExit(f"Publish failed: {detail}")
            resp.raise_for_status()
            return resp.json()

    async def publish_version(self, package_id: str, data: dict) -> dict:
        self._check_auth()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.hub_url}/api/store/packages/{package_id}/versions",
                json=data, headers=self._headers(),
            )
            if resp.status_code == 409:
                # version already exists OR content identical to latest — not a real error
                detail = resp.json().get("detail", "No changes to publish")
                raise SystemExit(f"Version skipped: {detail}")
            resp.raise_for_status()
            return resp.json()

    async def update_package(self, package_id: str, data: dict) -> dict:
        self._check_auth()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.put(
                f"{self.hub_url}/api/store/packages/{package_id}",
                json=data, headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def submit_review(self, package_id: str, rating: int, comment: str = None,
                            evaluation: dict = None) -> dict:
        """Post a review (rating + optional comment + structured rubric evaluation)
        for a package as the logged-in user. Used by the automated reviewer to write
        results as the pantheon-reviewer bot."""
        self._check_auth()
        body = {"rating": rating, "comment": comment, "evaluation": evaluation}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.hub_url}/api/store/packages/{package_id}/reviews",
                json=body, headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def get_reviews(self, package_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.hub_url}/api/store/packages/{package_id}/reviews")
            resp.raise_for_status()
            return resp.json()

    async def transfer_reviews(self, to_package_id: str, from_package_id: str) -> dict:
        """Move reviews from one package onto another (rename/move recovery)."""
        self._check_auth()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.hub_url}/api/store/packages/{to_package_id}/reviews/transfer",
                json={"from_package_id": from_package_id}, headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def delete_package(self, package_id: str) -> dict:
        self._check_auth()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(
                f"{self.hub_url}/api/store/packages/{package_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def my_published(self) -> dict:
        self._check_auth()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.hub_url}/api/store/my/published",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def my_installed(self) -> dict:
        self._check_auth()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.hub_url}/api/store/my/installed",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def record_install(self, package_id: str, version: str) -> dict:
        self._check_auth()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.hub_url}/api/store/my/installed",
                json={"package_id": package_id, "version": version},
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def record_uninstall(self, package_id: str) -> dict:
        self._check_auth()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(
                f"{self.hub_url}/api/store/my/installed/{package_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()
