"""
Recipe scraper for extracting recipes from popular cooking sites.
Uses HTTP requests and JSON-LD parsing - no AI needed.
"""

import re
import json
import logging
from typing import Optional
from urllib.parse import quote_plus

import httpx

from src.integrations.firestore_client import Recipe, Ingredient

logger = logging.getLogger(__name__)


class RecipeScraper:
    """Scrapes recipes from popular cooking sites without using AI."""

    def __init__(self):
        self.client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; MenuBot/1.0; +https://github.com/menu-bot)"
            }
        )

    def search_and_extract(self, meal_name: str) -> list[Recipe]:
        """
        Search multiple recipe sites and extract recipes.
        Returns a list of Recipe objects found.
        """
        logger.info(f"Searching for recipes: {meal_name}")
        recipes = []

        # Try each site
        for search_func in [
            self._search_allrecipes,
            self._search_seriouseats,
            self._search_budgetbytes,
        ]:
            try:
                found = search_func(meal_name)
                recipes.extend(found)
                if len(recipes) >= 3:  # Stop after finding enough
                    break
            except Exception as e:
                logger.warning(f"Search failed: {e}")
                continue

        logger.info(f"Found {len(recipes)} recipes for '{meal_name}'")
        return recipes

    def extract_from_url(self, url: str) -> Optional[Recipe]:
        """Extract a recipe from a specific URL."""
        logger.info(f"Extracting recipe from: {url}")

        try:
            response = self.client.get(url)
            response.raise_for_status()
            html = response.text
        except Exception as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return None

        # Try JSON-LD extraction first (most reliable)
        recipe_data = self._extract_jsonld(html)
        if recipe_data:
            logger.info(f"Found JSON-LD data for {url}")
            return self._jsonld_to_recipe(recipe_data, url)

        logger.warning(f"No JSON-LD recipe data found at {url}")
        return None

    def _search_allrecipes(self, meal_name: str) -> list[Recipe]:
        """Search AllRecipes and extract recipes."""
        recipes = []
        query = quote_plus(meal_name)
        search_url = f"https://www.allrecipes.com/search?q={query}"

        try:
            response = self.client.get(search_url)
            response.raise_for_status()
            html = response.text

            # Extract recipe URLs from search results
            pattern = r'href="(https://www\.allrecipes\.com/recipe/\d+/[^"]+)"'
            urls = list(set(re.findall(pattern, html)))[:3]

            for url in urls:
                recipe = self.extract_from_url(url)
                if recipe:
                    recipes.append(recipe)
                    if len(recipes) >= 2:
                        break

        except Exception as e:
            logger.warning(f"AllRecipes search failed: {e}")

        return recipes

    def _search_seriouseats(self, meal_name: str) -> list[Recipe]:
        """Search Serious Eats and extract recipes."""
        recipes = []
        query = quote_plus(meal_name)
        search_url = f"https://www.seriouseats.com/search?q={query}"

        try:
            response = self.client.get(search_url)
            response.raise_for_status()
            html = response.text

            # Serious Eats recipe URLs contain 'recipe' in the path
            pattern = r'href="(https://www\.seriouseats\.com/[^"]*-recipe[^"]*)"'
            urls = list(set(re.findall(pattern, html)))[:3]

            for url in urls:
                recipe = self.extract_from_url(url)
                if recipe:
                    recipes.append(recipe)
                    if len(recipes) >= 2:
                        break

        except Exception as e:
            logger.warning(f"Serious Eats search failed: {e}")

        return recipes

    def _search_budgetbytes(self, meal_name: str) -> list[Recipe]:
        """Search Budget Bytes and extract recipes."""
        recipes = []
        query = quote_plus(meal_name)
        search_url = f"https://www.budgetbytes.com/?s={query}"

        try:
            response = self.client.get(search_url)
            response.raise_for_status()
            html = response.text

            # Budget Bytes URLs in search results
            pattern = r'href="(https://www\.budgetbytes\.com/[^"]+/)"'
            matches = re.findall(pattern, html)

            # Filter out category/tag pages
            urls = [
                u for u in matches
                if '/category/' not in u and '/tag/' not in u
            ][:3]

            for url in urls:
                recipe = self.extract_from_url(url)
                if recipe:
                    recipes.append(recipe)
                    if len(recipes) >= 2:
                        break

        except Exception as e:
            logger.warning(f"Budget Bytes search failed: {e}")

        return recipes

    def _extract_jsonld(self, html: str) -> Optional[dict]:
        """Extract recipe data from JSON-LD structured data."""
        pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
        matches = re.findall(pattern, html, re.DOTALL | re.IGNORECASE)

        for match in matches:
            try:
                data = json.loads(match.strip())

                # Handle @graph arrays
                if isinstance(data, dict) and "@graph" in data:
                    data = data["@graph"]

                # Handle arrays
                if isinstance(data, list):
                    for item in data:
                        if self._is_recipe_type(item):
                            return item
                elif isinstance(data, dict):
                    if self._is_recipe_type(data):
                        return data

            except json.JSONDecodeError:
                continue

        return None

    def _is_recipe_type(self, data: dict) -> bool:
        """Check if JSON-LD data is a Recipe type."""
        if not isinstance(data, dict):
            return False

        type_val = data.get("@type")
        if isinstance(type_val, str):
            return type_val.lower() == "recipe"
        elif isinstance(type_val, list):
            return any(t.lower() == "recipe" for t in type_val if isinstance(t, str))
        return False

    def _jsonld_to_recipe(self, data: dict, source_url: str) -> Recipe:
        """Convert JSON-LD recipe data to our Recipe format."""
        # Parse ingredients
        ingredients = []
        for ing_text in data.get("recipeIngredient", []):
            ingredients.append(self._parse_ingredient(ing_text))

        # Parse instructions
        instructions = []
        for inst in data.get("recipeInstructions", []):
            if isinstance(inst, str):
                instructions.append(inst)
            elif isinstance(inst, dict):
                text = inst.get("text", "")
                if text:
                    instructions.append(text)

        # Parse times
        prep_time = self._parse_duration(data.get("prepTime"))
        cook_time = self._parse_duration(data.get("cookTime"))

        # Parse servings
        servings = self._parse_servings(data.get("recipeYield"))

        # Build tags
        tags = []
        for field in ["recipeCategory", "recipeCuisine"]:
            val = data.get(field)
            if isinstance(val, list):
                tags.extend(val)
            elif val:
                tags.append(val)

        return Recipe(
            name=data.get("name", "Unknown Recipe"),
            source="url",
            source_url=source_url,
            ingredients=ingredients,
            instructions=instructions,
            servings=servings,
            prep_time_min=prep_time,
            cook_time_min=cook_time,
            tags=[t.lower() for t in tags if t],
            seasonal_ingredients=[],
        )

    def _parse_ingredient(self, text: str) -> Ingredient:
        """Parse an ingredient string like '1 cup all-purpose flour'."""
        text = text.strip()

        # Pattern: optional quantity, optional unit, then ingredient name
        pattern = r'^([\d./\s½¼¾⅓⅔]+)?\s*(cups?|tbsp|tablespoons?|tsp|teaspoons?|oz|ounces?|lbs?|pounds?|g|kg|ml|l|cloves?|pieces?|cans?|packages?)?\s*(.+)$'
        match = re.match(pattern, text, re.IGNORECASE)

        if match:
            qty_str, unit, name = match.groups()
            quantity = self._parse_quantity(qty_str) if qty_str else 0

            return Ingredient(
                name=(name or text).strip(),
                quantity=quantity,
                unit=(unit or "").lower().rstrip('s'),  # Normalize plural
                category=self._guess_category(name or text),
            )

        return Ingredient(name=text, quantity=0, unit="", category="pantry")

    def _parse_quantity(self, qty_str: str) -> float:
        """Parse quantity string including fractions."""
        if not qty_str:
            return 0

        qty_str = qty_str.strip()

        # Handle unicode fractions
        fraction_map = {'½': 0.5, '¼': 0.25, '¾': 0.75, '⅓': 0.333, '⅔': 0.667}
        for char, val in fraction_map.items():
            qty_str = qty_str.replace(char, f' {val}')

        try:
            parts = qty_str.split()
            total = 0
            for part in parts:
                if '/' in part:
                    num, denom = part.split('/')
                    total += float(num) / float(denom)
                else:
                    total += float(part)
            return total
        except (ValueError, ZeroDivisionError):
            return 0

    def _parse_duration(self, duration: Optional[str]) -> Optional[int]:
        """Parse ISO 8601 duration (PT30M) to minutes."""
        if not duration:
            return None

        hours = minutes = 0
        h_match = re.search(r'(\d+)H', duration, re.IGNORECASE)
        m_match = re.search(r'(\d+)M', duration, re.IGNORECASE)

        if h_match:
            hours = int(h_match.group(1))
        if m_match:
            minutes = int(m_match.group(1))

        total = hours * 60 + minutes
        return total if total > 0 else None

    def _parse_servings(self, yield_val) -> int:
        """Parse servings from recipeYield."""
        if not yield_val:
            return 4

        if isinstance(yield_val, list):
            yield_val = yield_val[0]

        if isinstance(yield_val, int):
            return yield_val

        if isinstance(yield_val, str):
            match = re.search(r'\d+', yield_val)
            if match:
                return int(match.group())

        return 4

    def _guess_category(self, name: str) -> str:
        """Guess the category of an ingredient."""
        name_lower = name.lower()

        categories = {
            "produce": ["lettuce", "tomato", "onion", "garlic", "pepper", "carrot",
                       "celery", "potato", "broccoli", "spinach", "kale", "cabbage",
                       "mushroom", "zucchini", "squash", "corn", "bean", "pea",
                       "cucumber", "avocado", "lemon", "lime", "ginger", "scallion"],
            "meat": ["chicken", "beef", "pork", "lamb", "turkey", "bacon", "sausage",
                    "ham", "ground", "steak", "roast", "thigh", "breast"],
            "seafood": ["fish", "salmon", "tuna", "shrimp", "prawn", "crab", "lobster"],
            "dairy": ["milk", "cream", "butter", "yogurt", "sour cream"],
            "cheese": ["cheese", "parmesan", "cheddar", "mozzarella", "feta"],
            "fresh_herbs": ["basil", "cilantro", "parsley", "thyme", "rosemary", "dill"],
            "spices": ["salt", "pepper", "cumin", "paprika", "cinnamon", "curry"],
            "bread": ["bread", "bun", "roll", "tortilla", "pita", "naan"],
        }

        for category, keywords in categories.items():
            if any(kw in name_lower for kw in keywords):
                return category

        return "pantry"
