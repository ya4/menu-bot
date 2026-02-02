"""
Sync utility to update Google Sheets with computed metrics from Firestore.
Run periodically or after rating/usage events.
"""

import logging
from typing import Optional

from src.integrations.sheets_client import SheetsClient
from src.integrations.metrics_client import MetricsClient

logger = logging.getLogger(__name__)


class MetricsSync:
    """Syncs computed metrics from Firestore to Google Sheets."""

    def __init__(
        self,
        sheets: Optional[SheetsClient] = None,
        metrics: Optional[MetricsClient] = None,
    ):
        self.sheets = sheets or SheetsClient()
        self.metrics = metrics or MetricsClient()

    def sync_all_recipes(self) -> dict:
        """
        Sync metrics for all recipes in the sheet.
        Returns summary of updates.
        """
        recipes = self.sheets.get_all_recipes()
        updated = 0
        errors = 0

        for recipe in recipes:
            try:
                metrics = self.metrics.compute_recipe_metrics(recipe.name)

                # Only update if we have metrics
                has_changes = False

                if metrics["kid_score"] is not None:
                    recipe.kid_score = round(metrics["kid_score"], 1)
                    has_changes = True

                if metrics["times_used"] is not None:
                    recipe.times_used = metrics["times_used"]
                    has_changes = True

                if metrics["last_used"]:
                    recipe.last_used = metrics["last_used"]
                    has_changes = True

                if has_changes:
                    if self.sheets.update_recipe(recipe):
                        updated += 1
                    else:
                        errors += 1

            except Exception as e:
                logger.error(f"Error syncing {recipe.name}: {e}")
                errors += 1

        logger.info(f"Synced metrics: {updated} updated, {errors} errors")
        return {"updated": updated, "errors": errors, "total": len(recipes)}

    def sync_recipe(self, recipe_name: str) -> bool:
        """Sync metrics for a single recipe."""
        recipe = self.sheets.get_recipe_by_name(recipe_name)
        if not recipe:
            logger.warning(f"Recipe not found: {recipe_name}")
            return False

        try:
            metrics = self.metrics.compute_recipe_metrics(recipe_name)

            if metrics["kid_score"] is not None:
                recipe.kid_score = round(metrics["kid_score"], 1)
            if metrics["times_used"] is not None:
                recipe.times_used = metrics["times_used"]
            if metrics["last_used"]:
                recipe.last_used = metrics["last_used"]

            return self.sheets.update_recipe(recipe)

        except Exception as e:
            logger.error(f"Error syncing {recipe_name}: {e}")
            return False

    def get_stale_recipes_report(self, days: int = 60) -> list[dict]:
        """
        Get a report of stale recipes with their metrics.
        Useful for periodic cleanup suggestions.
        """
        stale_names = self.metrics.get_stale_recipes(days)
        report = []

        for name in stale_names:
            recipe = self.sheets.get_recipe_by_name(name)
            if recipe:
                report.append({
                    "name": name,
                    "last_used": recipe.last_used,
                    "times_used": recipe.times_used,
                    "kid_score": recipe.kid_score,
                    "approved": recipe.approved,
                })

        return sorted(report, key=lambda x: x.get("last_used", ""), reverse=True)

    def get_favorites_report(self) -> list[dict]:
        """
        Get a report of favorite recipes based on usage and ratings.
        """
        favorite_names = self.metrics.get_favorites()
        report = []

        for name in favorite_names:
            recipe = self.sheets.get_recipe_by_name(name)
            if recipe:
                avg_score = self.metrics.get_average_score(name)
                report.append({
                    "name": name,
                    "times_used": recipe.times_used,
                    "kid_score": recipe.kid_score,
                    "average_score": round(avg_score, 1) if avg_score else None,
                    "approved": recipe.approved,
                })

        return sorted(report, key=lambda x: x.get("times_used", 0), reverse=True)
