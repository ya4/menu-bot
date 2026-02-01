"""
Google Tasks API integration for grocery list synchronization.
Uses OAuth 2.0 for user authorization with minimal required scopes.
"""

import os
from typing import Optional
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from src.integrations.firestore_client import GroceryList, GroceryItem


# Minimal scope - only access to Tasks
SCOPES = ["https://www.googleapis.com/auth/tasks"]


class GoogleTasksClient:
    """Client for Google Tasks API operations."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = None,
    ):
        """Initialize the Google Tasks client."""
        self.client_id = client_id or os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        self.redirect_uri = redirect_uri or os.environ.get(
            "GOOGLE_OAUTH_REDIRECT_URI",
            "https://your-app.run.app/oauth/callback"
        )

        self.client_config = {
            "web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [self.redirect_uri],
            }
        }

    def get_authorization_url(self, state: str) -> str:
        """
        Get the OAuth authorization URL for a user to connect their Google account.

        Args:
            state: State parameter to pass through OAuth flow (e.g., Slack user ID)

        Returns:
            Authorization URL to redirect the user to
        """
        flow = Flow.from_client_config(
            self.client_config,
            scopes=SCOPES,
            redirect_uri=self.redirect_uri,
        )

        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            state=state,
            prompt="consent",  # Always show consent to ensure refresh token
        )

        return auth_url

    def exchange_code_for_tokens(self, authorization_code: str) -> dict:
        """
        Exchange an authorization code for access and refresh tokens.

        Args:
            authorization_code: The code from the OAuth callback

        Returns:
            Dictionary with 'access_token' and 'refresh_token'
        """
        flow = Flow.from_client_config(
            self.client_config,
            scopes=SCOPES,
            redirect_uri=self.redirect_uri,
        )

        flow.fetch_token(code=authorization_code)
        credentials = flow.credentials

        return {
            "access_token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
        }

    def get_credentials_from_refresh_token(self, refresh_token: str) -> Credentials:
        """
        Get valid credentials from a refresh token.

        Args:
            refresh_token: The stored refresh token

        Returns:
            Valid Credentials object
        """
        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=SCOPES,
        )

        # Refresh to get a valid access token
        credentials.refresh(Request())

        return credentials

    def _get_service(self, refresh_token: str):
        """Get an authenticated Tasks API service."""
        credentials = self.get_credentials_from_refresh_token(refresh_token)
        return build("tasks", "v1", credentials=credentials)

    def get_or_create_tasklist(self, refresh_token: str, list_name: str = "Grocery List") -> str:
        """
        Get or create a task list for groceries.

        Args:
            refresh_token: User's refresh token
            list_name: Name of the task list

        Returns:
            Task list ID
        """
        service = self._get_service(refresh_token)

        # Check if list already exists
        results = service.tasklists().list(maxResults=100).execute()
        task_lists = results.get("items", [])

        for task_list in task_lists:
            if task_list.get("title") == list_name:
                return task_list["id"]

        # Create new list
        new_list = service.tasklists().insert(body={"title": list_name}).execute()
        return new_list["id"]

    def sync_grocery_list(
        self,
        refresh_token: str,
        grocery_list: GroceryList,
        list_name: Optional[str] = None,
    ) -> str:
        """
        Sync a grocery list to Google Tasks.
        Creates tasks organized by store.

        Args:
            refresh_token: User's refresh token
            grocery_list: The grocery list to sync
            list_name: Optional custom name for the task list

        Returns:
            Google Tasks list ID
        """
        if list_name is None:
            list_name = f"Groceries - Week of {grocery_list.week_start}"

        service = self._get_service(refresh_token)
        tasklist_id = self.get_or_create_tasklist(refresh_token, list_name)

        # Clear existing tasks in the list
        self._clear_tasklist(service, tasklist_id)

        # Group items by store
        items_by_store = self._group_items_by_store(grocery_list.items)

        # Store display names
        store_names = {
            "meijer": "Meijer",
            "trader_joes": "Trader Joe's",
            "costco": "Costco",
            "buschs": "Busch's",
        }

        # Create tasks for each store section
        for store, items in items_by_store.items():
            store_display = store_names.get(store, store.title())

            # Create a header task for the store
            header_task = service.tasks().insert(
                tasklist=tasklist_id,
                body={
                    "title": f"--- {store_display} ---",
                    "notes": f"{len(items)} items",
                }
            ).execute()

            # Create tasks for each item under this store
            for item in items:
                quantity_str = self._format_quantity(item.quantity, item.unit)
                task_title = f"{item.name} ({quantity_str})"

                service.tasks().insert(
                    tasklist=tasklist_id,
                    body={
                        "title": task_title,
                        "status": "completed" if item.checked else "needsAction",
                    },
                    previous=header_task["id"],  # Place after header
                ).execute()

        return tasklist_id

    def update_task_status(
        self,
        refresh_token: str,
        tasklist_id: str,
        task_title: str,
        completed: bool,
    ) -> bool:
        """
        Update the completion status of a task.

        Args:
            refresh_token: User's refresh token
            tasklist_id: The task list ID
            task_title: Title of the task to update
            completed: New completion status

        Returns:
            True if successful
        """
        service = self._get_service(refresh_token)

        # Find the task
        results = service.tasks().list(tasklist=tasklist_id, maxResults=100).execute()
        tasks = results.get("items", [])

        for task in tasks:
            if task.get("title") == task_title:
                task["status"] = "completed" if completed else "needsAction"
                service.tasks().update(
                    tasklist=tasklist_id,
                    task=task["id"],
                    body=task,
                ).execute()
                return True

        return False

    def get_tasklist_status(self, refresh_token: str, tasklist_id: str) -> dict:
        """
        Get the current status of a grocery list in Google Tasks.

        Returns:
            Dictionary with 'total', 'completed', 'pending' counts
        """
        service = self._get_service(refresh_token)

        results = service.tasks().list(tasklist=tasklist_id, maxResults=100).execute()
        tasks = results.get("items", [])

        # Filter out header tasks (those starting with ---)
        item_tasks = [t for t in tasks if not t.get("title", "").startswith("---")]

        completed = sum(1 for t in item_tasks if t.get("status") == "completed")
        total = len(item_tasks)

        return {
            "total": total,
            "completed": completed,
            "pending": total - completed,
            "completion_pct": completed / total if total > 0 else 0,
        }

    def delete_tasklist(self, refresh_token: str, tasklist_id: str) -> bool:
        """Delete a task list."""
        service = self._get_service(refresh_token)
        service.tasklists().delete(tasklist=tasklist_id).execute()
        return True

    def _clear_tasklist(self, service, tasklist_id: str):
        """Clear all tasks from a task list."""
        results = service.tasks().list(tasklist=tasklist_id, maxResults=100).execute()
        tasks = results.get("items", [])

        for task in tasks:
            service.tasks().delete(tasklist=tasklist_id, task=task["id"]).execute()

    def _group_items_by_store(self, items: list[GroceryItem]) -> dict[str, list[GroceryItem]]:
        """Group grocery items by store."""
        # Order stores by preference
        store_order = ["trader_joes", "costco", "buschs", "meijer"]
        grouped = {store: [] for store in store_order}

        for item in items:
            store = item.store if item.store in grouped else "meijer"
            grouped[store].append(item)

        # Remove empty stores and return
        return {store: items for store, items in grouped.items() if items}

    def _format_quantity(self, quantity: float, unit: str) -> str:
        """Format quantity and unit for display."""
        if quantity == 0:
            return unit if unit else "to taste"

        # Format quantity nicely (remove .0 for whole numbers)
        if quantity == int(quantity):
            qty_str = str(int(quantity))
        else:
            qty_str = f"{quantity:.1f}".rstrip("0").rstrip(".")

        if unit:
            return f"{qty_str} {unit}"
        return qty_str
