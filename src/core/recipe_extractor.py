"""
Recipe extraction orchestrator.
Coordinates extraction from various sources using Claude AI.
"""

import re
import httpx
from typing import Optional

from src.integrations.claude_client import ClaudeClient
from src.integrations.firestore_client import Recipe, FirestoreClient


class RecipeExtractor:
    """Orchestrates recipe extraction from various sources."""

    def __init__(
        self,
        claude_client: Optional[ClaudeClient] = None,
        firestore_client: Optional[FirestoreClient] = None,
    ):
        """Initialize the recipe extractor."""
        self.claude = claude_client or ClaudeClient()
        self.db = firestore_client or FirestoreClient()

    def extract_from_message(
        self,
        text: str,
        files: Optional[list[dict]] = None,
        user_id: str = "",
    ) -> Optional[Recipe]:
        """
        Extract a recipe from a Slack message.
        Handles URLs, plain text, and file attachments.

        Args:
            text: The message text
            files: List of file attachments from Slack
            user_id: Slack user ID who shared the recipe

        Returns:
            Extracted Recipe or None if extraction failed
        """
        recipe = None

        # Check for URLs in the message
        urls = self._extract_urls(text)
        if urls:
            # Try the first URL
            recipe = self.claude.extract_recipe_from_url(urls[0])
            if recipe:
                recipe.source = "url"
                recipe.source_url = urls[0]

        # Check for image attachments (cookbook photos)
        if not recipe and files:
            for file in files:
                if self._is_image_file(file):
                    image_data = self._download_slack_file(file)
                    if image_data:
                        media_type = file.get("mimetype", "image/jpeg")
                        recipe = self.claude.extract_recipe_from_image(
                            image_data,
                            media_type=media_type,
                            source_description=file.get("name", "cookbook photo"),
                        )
                        if recipe:
                            recipe.source = "cookbook"
                            recipe.source_details = file.get("name", "photo")
                            break

        # If no URL or image, try to extract from text
        if not recipe and len(text) > 50:  # Minimum length for a recipe
            # Remove URLs from text before processing
            clean_text = re.sub(r'https?://\S+', '', text).strip()
            if len(clean_text) > 50:
                recipe = self.claude.extract_recipe_from_text(clean_text)
                if recipe:
                    recipe.source = "text"

        # Enrich the recipe if extracted
        if recipe:
            recipe.created_by = user_id
            recipe = self._enrich_recipe(recipe)

        return recipe

    def extract_from_url(self, url: str, user_id: str = "") -> Optional[Recipe]:
        """
        Extract a recipe from a URL.

        Args:
            url: The recipe URL
            user_id: Slack user ID who shared the recipe

        Returns:
            Extracted Recipe or None
        """
        recipe = self.claude.extract_recipe_from_url(url)
        if recipe:
            recipe.created_by = user_id
            recipe = self._enrich_recipe(recipe)
        return recipe

    def extract_from_text(self, text: str, user_id: str = "") -> Optional[Recipe]:
        """
        Extract a recipe from plain text.

        Args:
            text: Recipe text
            user_id: Slack user ID

        Returns:
            Extracted Recipe or None
        """
        recipe = self.claude.extract_recipe_from_text(text)
        if recipe:
            recipe.created_by = user_id
            recipe = self._enrich_recipe(recipe)
        return recipe

    def extract_from_image(
        self,
        image_data: bytes,
        media_type: str = "image/jpeg",
        source_description: str = "cookbook photo",
        user_id: str = "",
    ) -> Optional[Recipe]:
        """
        Extract a recipe from an image (e.g., cookbook page photo).

        Args:
            image_data: Raw image bytes
            media_type: MIME type of the image
            source_description: Description of the source
            user_id: Slack user ID

        Returns:
            Extracted Recipe or None
        """
        recipe = self.claude.extract_recipe_from_image(
            image_data,
            media_type=media_type,
            source_description=source_description,
        )
        if recipe:
            recipe.created_by = user_id
            recipe = self._enrich_recipe(recipe)
        return recipe

    def _enrich_recipe(self, recipe: Recipe) -> Recipe:
        """
        Enrich a recipe with additional computed fields.

        Args:
            recipe: The recipe to enrich

        Returns:
            Enriched recipe
        """
        # Assess kid-friendliness
        recipe.kid_friendly_score = self.claude.assess_kid_friendliness(recipe)

        # Assess health score
        recipe.health_score = self.claude.assess_health_score(recipe)

        # Add kid-friendly tag if score is high
        if recipe.kid_friendly_score >= 0.7 and "kid-friendly" not in recipe.tags:
            recipe.tags.append("kid-friendly")

        # Add healthy tag if score is high
        if recipe.health_score >= 0.7 and "healthy" not in recipe.tags:
            recipe.tags.append("healthy")

        # Calculate total time and add quick tag
        total_time = (recipe.prep_time_min or 0) + (recipe.cook_time_min or 0)
        if 0 < total_time <= 30 and "quick" not in recipe.tags:
            recipe.tags.append("quick")

        return recipe

    def _extract_urls(self, text: str) -> list[str]:
        """Extract URLs from text."""
        # Match URLs, including those in Slack's <url|text> format
        slack_url_pattern = r'<(https?://[^|>]+)(?:\|[^>]*)?>'
        plain_url_pattern = r'https?://[^\s<>]+'

        urls = []

        # Extract Slack-formatted URLs first
        slack_urls = re.findall(slack_url_pattern, text)
        urls.extend(slack_urls)

        # Remove Slack-formatted URLs from text and find plain URLs
        clean_text = re.sub(slack_url_pattern, '', text)
        plain_urls = re.findall(plain_url_pattern, clean_text)
        urls.extend(plain_urls)

        return urls

    def _is_image_file(self, file: dict) -> bool:
        """Check if a Slack file is an image."""
        mimetype = file.get("mimetype", "")
        return mimetype.startswith("image/")

    def _download_slack_file(self, file: dict) -> Optional[bytes]:
        """
        Download a file from Slack.

        Args:
            file: Slack file object

        Returns:
            File contents as bytes or None
        """
        import os

        url = file.get("url_private_download") or file.get("url_private")
        if not url:
            return None

        token = os.environ.get("SLACK_BOT_TOKEN")
        if not token:
            return None

        try:
            response = httpx.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                follow_redirects=True,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.content
        except Exception:
            return None

    def save_recipe(self, recipe: Recipe, approved: bool = False) -> str:
        """
        Save a recipe to the database.

        Args:
            recipe: Recipe to save
            approved: Whether the recipe is pre-approved

        Returns:
            Recipe ID
        """
        recipe.approved = approved
        return self.db.save_recipe(recipe)

    def check_duplicate(self, recipe_name: str) -> Optional[Recipe]:
        """
        Check if a recipe with a similar name already exists.

        Args:
            recipe_name: Name to check

        Returns:
            Existing recipe if found, None otherwise
        """
        existing = self.db.search_recipes_by_name(recipe_name)
        if existing:
            # Check for close match
            name_lower = recipe_name.lower()
            for r in existing:
                if r.name.lower() == name_lower:
                    return r
        return None
