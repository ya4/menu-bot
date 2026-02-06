"""
Google Sheets client for recipe and meal plan management.
Provides a human-friendly UI layer while Firestore handles metrics.
"""

import os
import logging
from typing import Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# Sheet names
RECIPES_SHEET = "Recipes"
MEAL_PLANS_SHEET = "Meal Plans"
FAMILY_SHEET = "Family"
CONFIG_SHEET = "Config"


@dataclass
class SheetRecipe:
    """Recipe as stored in Google Sheets."""
    name: str
    source_url: str = ""
    approved: bool = False
    kid_score: float = 0.0
    health_score: float = 0.0
    times_used: int = 0
    last_used: str = ""
    prep_time_min: int = 0
    cook_time_min: int = 0
    servings: int = 4
    tags: str = ""  # Comma-separated
    ingredients: str = ""  # Newline-separated
    instructions: str = ""  # Newline-separated
    notes: str = ""
    created_date: str = ""
    row_number: int = 0  # For updates

    @classmethod
    def from_row(cls, row: list, row_number: int) -> "SheetRecipe":
        """Create from a sheet row."""
        def safe_get(idx, default=""):
            return row[idx] if idx < len(row) else default

        def safe_bool(val):
            return str(val).upper() in ("TRUE", "YES", "1", "X", "✓")

        def safe_float(val):
            try:
                return float(val) if val else 0.0
            except (ValueError, TypeError):
                return 0.0

        def safe_int(val):
            try:
                return int(val) if val else 0
            except (ValueError, TypeError):
                return 0

        return cls(
            name=safe_get(0),
            source_url=safe_get(1),
            approved=safe_bool(safe_get(2)),
            kid_score=safe_float(safe_get(3)),
            health_score=safe_float(safe_get(4)),
            times_used=safe_int(safe_get(5)),
            last_used=safe_get(6),
            prep_time_min=safe_int(safe_get(7)),
            cook_time_min=safe_int(safe_get(8)),
            servings=safe_int(safe_get(9)) or 4,
            tags=safe_get(10),
            ingredients=safe_get(11),
            instructions=safe_get(12),
            notes=safe_get(13),
            created_date=safe_get(14),
            row_number=row_number,
        )

    def to_row(self) -> list:
        """Convert to a sheet row."""
        return [
            self.name,
            self.source_url,
            "TRUE" if self.approved else "FALSE",
            self.kid_score,
            self.health_score,
            self.times_used,
            self.last_used,
            self.prep_time_min,
            self.cook_time_min,
            self.servings,
            self.tags,
            self.ingredients,
            self.instructions,
            self.notes,
            self.created_date,
        ]


class SheetsClient:
    """Client for Google Sheets operations."""

    # Column headers for each sheet
    RECIPE_HEADERS = [
        "Name", "Source URL", "Approved", "Kid Score", "Health Score",
        "Times Used", "Last Used", "Prep (min)", "Cook (min)", "Servings",
        "Tags", "Ingredients", "Instructions", "Notes", "Created"
    ]

    MEAL_PLAN_HEADERS = [
        "Week Start", "Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday", "Status", "Notes"
    ]

    FAMILY_HEADERS = [
        "Name", "Role", "Slack ID", "Preferences", "Notes"
    ]

    def __init__(self, spreadsheet_id: Optional[str] = None):
        """Initialize the Sheets client."""
        self.spreadsheet_id = spreadsheet_id or os.environ.get("GOOGLE_SHEET_ID")
        self.service = self._build_service()

    def _build_service(self):
        """Build the Sheets API service."""
        # Try service account first (for Cloud Run)
        sa_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
        if sa_file and os.path.exists(sa_file):
            creds = service_account.Credentials.from_service_account_file(
                sa_file,
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            return build("sheets", "v4", credentials=creds)

        # Fall back to application default credentials
        from google.auth import default
        creds, _ = default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
        return build("sheets", "v4", credentials=creds)

    def initialize_spreadsheet(self) -> bool:
        """
        Initialize the spreadsheet with required sheets and headers.
        Creates sheets if they don't exist, adds headers if missing.
        Returns True if successful.
        """
        if not self.spreadsheet_id:
            logger.error("No spreadsheet ID configured")
            return False

        logger.info(f"Initializing spreadsheet: {self.spreadsheet_id}")

        try:
            # Get existing sheets
            spreadsheet = self.service.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id
            ).execute()
            logger.info(f"Retrieved spreadsheet: {spreadsheet.get('properties', {}).get('title', 'Unknown')}")

            existing_sheets = {
                sheet["properties"]["title"]
                for sheet in spreadsheet.get("sheets", [])
            }

            # Create missing sheets
            sheets_to_create = []
            if RECIPES_SHEET not in existing_sheets:
                sheets_to_create.append({"properties": {"title": RECIPES_SHEET}})
            if MEAL_PLANS_SHEET not in existing_sheets:
                sheets_to_create.append({"properties": {"title": MEAL_PLANS_SHEET}})
            if FAMILY_SHEET not in existing_sheets:
                sheets_to_create.append({"properties": {"title": FAMILY_SHEET}})
            if CONFIG_SHEET not in existing_sheets:
                sheets_to_create.append({"properties": {"title": CONFIG_SHEET}})

            if sheets_to_create:
                self.service.spreadsheets().batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={"requests": [{"addSheet": sheet} for sheet in sheets_to_create]}
                ).execute()
                logger.info(f"Created sheets: {[s['properties']['title'] for s in sheets_to_create]}")

            # Add headers to each sheet
            self._ensure_headers(RECIPES_SHEET, self.RECIPE_HEADERS)
            self._ensure_headers(MEAL_PLANS_SHEET, self.MEAL_PLAN_HEADERS)
            self._ensure_headers(FAMILY_SHEET, self.FAMILY_HEADERS)

            logger.info("Spreadsheet initialized successfully")
            return True

        except HttpError as e:
            logger.error(f"HTTP error initializing spreadsheet: {e.status_code} - {e.reason}")
            logger.error(f"Error details: {e.error_details}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error initializing spreadsheet: {type(e).__name__}: {e}")
            return False

    def _ensure_headers(self, sheet_name: str, headers: list):
        """Ensure a sheet has the correct headers in row 1."""
        range_name = f"{sheet_name}!A1:{chr(64 + len(headers))}1"

        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name
            ).execute()

            existing = result.get("values", [[]])[0] if result.get("values") else []

            if existing != headers:
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=range_name,
                    valueInputOption="RAW",
                    body={"values": [headers]}
                ).execute()
                logger.info(f"Updated headers for {sheet_name}")

        except HttpError as e:
            logger.error(f"Failed to ensure headers for {sheet_name}: {e}")

    # ==================== Recipe Operations ====================

    def get_all_recipes(self) -> list[SheetRecipe]:
        """Get all recipes from the sheet."""
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{RECIPES_SHEET}!A2:O1000"  # Skip header row
            ).execute()

            rows = result.get("values", [])
            recipes = []
            for i, row in enumerate(rows):
                if row and row[0]:  # Has a name
                    recipes.append(SheetRecipe.from_row(row, i + 2))  # +2 for 1-indexed + header

            return recipes

        except HttpError as e:
            logger.error(f"Failed to get recipes: {e}")
            return []

    def get_approved_recipes(self) -> list[SheetRecipe]:
        """Get only approved recipes."""
        return [r for r in self.get_all_recipes() if r.approved]

    def get_recipe_by_name(self, name: str) -> Optional[SheetRecipe]:
        """Find a recipe by name (case-insensitive)."""
        name_lower = name.lower()
        for recipe in self.get_all_recipes():
            if recipe.name.lower() == name_lower:
                return recipe
        return None

    def add_recipe(self, recipe: SheetRecipe) -> bool:
        """Add a new recipe to the sheet."""
        try:
            if not recipe.created_date:
                recipe.created_date = datetime.now().strftime("%Y-%m-%d")

            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{RECIPES_SHEET}!A:O",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [recipe.to_row()]}
            ).execute()

            logger.info(f"Added recipe: {recipe.name}")
            return True

        except HttpError as e:
            logger.error(f"Failed to add recipe: {e}")
            return False

    def update_recipe(self, recipe: SheetRecipe) -> bool:
        """Update an existing recipe by row number."""
        if recipe.row_number < 2:
            logger.error("Invalid row number for update")
            return False

        try:
            range_name = f"{RECIPES_SHEET}!A{recipe.row_number}:O{recipe.row_number}"
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueInputOption="RAW",
                body={"values": [recipe.to_row()]}
            ).execute()

            logger.info(f"Updated recipe: {recipe.name}")
            return True

        except HttpError as e:
            logger.error(f"Failed to update recipe: {e}")
            return False

    def update_recipe_metrics(self, name: str, kid_score: float = None,
                              health_score: float = None, times_used: int = None,
                              last_used: str = None) -> bool:
        """Update computed metrics for a recipe."""
        recipe = self.get_recipe_by_name(name)
        if not recipe:
            logger.warning(f"Recipe not found for metrics update: {name}")
            return False

        if kid_score is not None:
            recipe.kid_score = round(kid_score, 1)
        if health_score is not None:
            recipe.health_score = round(health_score, 1)
        if times_used is not None:
            recipe.times_used = times_used
        if last_used is not None:
            recipe.last_used = last_used

        return self.update_recipe(recipe)

    # ==================== Meal Plan Operations ====================

    def get_current_meal_plan(self) -> Optional[dict]:
        """Get the most recent meal plan."""
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{MEAL_PLANS_SHEET}!A2:J100"
            ).execute()

            rows = result.get("values", [])
            if not rows:
                return None

            # Return the last row (most recent plan)
            row = rows[-1]
            return {
                "week_start": row[0] if len(row) > 0 else "",
                "monday": row[1] if len(row) > 1 else "",
                "tuesday": row[2] if len(row) > 2 else "",
                "wednesday": row[3] if len(row) > 3 else "",
                "thursday": row[4] if len(row) > 4 else "",
                "friday": row[5] if len(row) > 5 else "",
                "saturday": row[6] if len(row) > 6 else "",
                "sunday": row[7] if len(row) > 7 else "",
                "status": row[8] if len(row) > 8 else "",
                "notes": row[9] if len(row) > 9 else "",
                "row_number": len(rows) + 1,
            }

        except HttpError as e:
            logger.error(f"Failed to get meal plan: {e}")
            return None

    def add_meal_plan(self, week_start: str, meals: dict, status: str = "pending") -> bool:
        """Add a new meal plan."""
        try:
            row = [
                week_start,
                meals.get("monday", ""),
                meals.get("tuesday", ""),
                meals.get("wednesday", ""),
                meals.get("thursday", ""),
                meals.get("friday", ""),
                meals.get("saturday", ""),
                meals.get("sunday", ""),
                status,
                "",  # notes
            ]

            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{MEAL_PLANS_SHEET}!A:J",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]}
            ).execute()

            logger.info(f"Added meal plan for week of {week_start}")
            return True

        except HttpError as e:
            logger.error(f"Failed to add meal plan: {e}")
            return False

    # ==================== Family Operations ====================

    def get_family_members(self) -> list[dict]:
        """Get all family members."""
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{FAMILY_SHEET}!A2:E100"
            ).execute()

            rows = result.get("values", [])
            members = []
            for i, row in enumerate(rows):
                if row and row[0]:
                    members.append({
                        "name": row[0] if len(row) > 0 else "",
                        "role": row[1] if len(row) > 1 else "",
                        "slack_id": row[2] if len(row) > 2 else "",
                        "preferences": row[3] if len(row) > 3 else "",
                        "notes": row[4] if len(row) > 4 else "",
                        "row_number": i + 2,
                    })
            return members

        except HttpError as e:
            logger.error(f"Failed to get family members: {e}")
            return []

    def add_family_member(self, name: str, role: str, slack_id: str = "",
                          preferences: str = "") -> bool:
        """Add a family member."""
        try:
            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{FAMILY_SHEET}!A:E",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [[name, role, slack_id, preferences, ""]]}
            ).execute()

            logger.info(f"Added family member: {name}")
            return True

        except HttpError as e:
            logger.error(f"Failed to add family member: {e}")
            return False

    def is_parent(self, slack_id: str) -> bool:
        """Check if a Slack user is a parent."""
        for member in self.get_family_members():
            if member.get("slack_id") == slack_id:
                return member.get("role", "").lower() == "parent"
        return False

    # ==================== Config Operations ====================

    def get_config(self, key: str, default: str = "") -> str:
        """Get a config value."""
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{CONFIG_SHEET}!A:B"
            ).execute()

            for row in result.get("values", []):
                if len(row) >= 2 and row[0] == key:
                    return row[1]
            return default

        except HttpError as e:
            logger.error(f"Failed to get config: {e}")
            return default

    def set_config(self, key: str, value: str) -> bool:
        """Set a config value."""
        try:
            # First, try to find existing key
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{CONFIG_SHEET}!A:B"
            ).execute()

            rows = result.get("values", [])
            for i, row in enumerate(rows):
                if row and row[0] == key:
                    # Update existing
                    self.service.spreadsheets().values().update(
                        spreadsheetId=self.spreadsheet_id,
                        range=f"{CONFIG_SHEET}!A{i+1}:B{i+1}",
                        valueInputOption="RAW",
                        body={"values": [[key, value]]}
                    ).execute()
                    return True

            # Add new
            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{CONFIG_SHEET}!A:B",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [[key, value]]}
            ).execute()
            return True

        except HttpError as e:
            logger.error(f"Failed to set config: {e}")
            return False

    def get_spreadsheet_url(self) -> str:
        """Get the URL to the spreadsheet."""
        return f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}"
