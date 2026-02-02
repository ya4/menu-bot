"""
Claude AI client for recipe extraction and meal planning assistance.
Uses the Anthropic API for natural language understanding.
"""

import os
import json
import base64
import httpx
from typing import Optional
from anthropic import Anthropic

from src.integrations.firestore_client import Recipe, Ingredient


class ClaudeClient:
    """Client for Claude AI interactions."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Claude client."""
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.client = Anthropic(api_key=self.api_key)
        self.model = "claude-sonnet-4-20250514"  # Good balance of quality and cost

    def extract_recipe_from_url(self, url: str) -> Optional[Recipe]:
        """
        Extract structured recipe data from a URL.
        First tries to find JSON-LD structured data, then falls back to Claude parsing.
        """
        import re
        import logging
        logger = logging.getLogger(__name__)

        # Fetch the page content
        try:
            response = httpx.get(url, follow_redirects=True, timeout=30.0)
            response.raise_for_status()
            html_content = response.text
        except Exception as e:
            logger.error(f"Failed to fetch URL {url}: {e}")
            return None

        # First, try to extract JSON-LD structured data (most recipe sites have this)
        recipe_data = self._extract_jsonld_recipe(html_content)
        if recipe_data:
            logger.info(f"Found JSON-LD recipe data for {url}")
            return self._jsonld_to_recipe(recipe_data, url)

        # Fall back to Claude parsing with cleaned/reduced HTML
        cleaned_content = self._clean_html_for_parsing(html_content)
        if len(cleaned_content) < 500:
            logger.warning(f"Not enough content extracted from {url}")
            return None

        logger.info(f"Using Claude to parse recipe from {url} ({len(cleaned_content)} chars)")
        return self._parse_recipe_with_claude(cleaned_content, url)

    def _extract_jsonld_recipe(self, html: str) -> Optional[dict]:
        """Extract recipe data from JSON-LD structured data."""
        import re

        # Find all JSON-LD script blocks
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
                        if isinstance(item, dict) and item.get("@type") in ["Recipe", "recipe"]:
                            return item
                elif isinstance(data, dict):
                    if data.get("@type") in ["Recipe", "recipe"]:
                        return data
                    # Some sites nest recipe in a list type
                    if isinstance(data.get("@type"), list) and "Recipe" in data.get("@type"):
                        return data
            except json.JSONDecodeError:
                continue

        return None

    def _jsonld_to_recipe(self, data: dict, source_url: str) -> Recipe:
        """Convert JSON-LD recipe data to our Recipe format."""
        # Parse ingredients
        ingredients = []
        raw_ingredients = data.get("recipeIngredient", [])
        for ing_text in raw_ingredients:
            # Parse "1 cup flour" style strings
            ingredient = self._parse_ingredient_string(ing_text)
            ingredients.append(ingredient)

        # Parse instructions
        instructions = []
        raw_instructions = data.get("recipeInstructions", [])
        for inst in raw_instructions:
            if isinstance(inst, str):
                instructions.append(inst)
            elif isinstance(inst, dict):
                instructions.append(inst.get("text", ""))

        # Parse times
        prep_time = self._parse_duration(data.get("prepTime"))
        cook_time = self._parse_duration(data.get("cookTime"))

        # Parse servings
        servings = 4
        yield_val = data.get("recipeYield")
        if yield_val:
            if isinstance(yield_val, list):
                yield_val = yield_val[0]
            if isinstance(yield_val, str):
                # Extract number from "4 servings" or just "4"
                import re
                match = re.search(r'\d+', str(yield_val))
                if match:
                    servings = int(match.group())
            elif isinstance(yield_val, int):
                servings = yield_val

        # Build tags from category and cuisine
        tags = []
        if data.get("recipeCategory"):
            cat = data.get("recipeCategory")
            if isinstance(cat, list):
                tags.extend(cat)
            else:
                tags.append(cat)
        if data.get("recipeCuisine"):
            cuisine = data.get("recipeCuisine")
            if isinstance(cuisine, list):
                tags.extend(cuisine)
            else:
                tags.append(cuisine)

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

    def _parse_ingredient_string(self, text: str) -> Ingredient:
        """Parse an ingredient string like '1 cup all-purpose flour'."""
        import re

        text = text.strip()

        # Try to extract quantity, unit, and name
        # Pattern: optional quantity (number or fraction), optional unit, then ingredient name
        pattern = r'^([\d./\s]+)?\s*(cup|cups|tbsp|tablespoon|tablespoons|tsp|teaspoon|teaspoons|oz|ounce|ounces|lb|lbs|pound|pounds|g|kg|ml|l|clove|cloves|each|piece|pieces|can|cans|package|packages)?\s*(.+)$'
        match = re.match(pattern, text, re.IGNORECASE)

        if match:
            qty_str, unit, name = match.groups()
            quantity = 0
            if qty_str:
                qty_str = qty_str.strip()
                # Handle fractions like "1/2" or "1 1/2"
                try:
                    if '/' in qty_str:
                        parts = qty_str.split()
                        total = 0
                        for part in parts:
                            if '/' in part:
                                num, denom = part.split('/')
                                total += float(num) / float(denom)
                            else:
                                total += float(part)
                        quantity = total
                    else:
                        quantity = float(qty_str)
                except ValueError:
                    quantity = 0

            return Ingredient(
                name=name.strip() if name else text,
                quantity=quantity,
                unit=(unit or "").lower(),
                category=self._guess_ingredient_category(name or text),
            )

        return Ingredient(name=text, quantity=0, unit="", category="pantry")

    def _guess_ingredient_category(self, name: str) -> str:
        """Guess the category of an ingredient based on its name."""
        name_lower = name.lower()

        produce = ["lettuce", "tomato", "onion", "garlic", "pepper", "carrot", "celery", "potato", "broccoli", "spinach", "kale", "cabbage", "mushroom", "zucchini", "squash", "corn", "bean", "pea", "cucumber", "avocado", "lemon", "lime", "orange", "apple", "banana", "berry", "ginger", "scallion", "shallot", "leek"]
        meat = ["chicken", "beef", "pork", "lamb", "turkey", "bacon", "sausage", "ham", "ground", "steak", "roast", "thigh", "breast", "wing", "rib"]
        seafood = ["fish", "salmon", "tuna", "shrimp", "prawn", "crab", "lobster", "scallop", "mussel", "clam", "oyster", "cod", "tilapia"]
        dairy = ["milk", "cream", "butter", "yogurt", "sour cream", "half and half", "buttermilk"]
        cheese = ["cheese", "parmesan", "cheddar", "mozzarella", "feta", "goat cheese", "cream cheese", "ricotta"]
        herbs = ["basil", "cilantro", "parsley", "thyme", "rosemary", "oregano", "dill", "mint", "chive", "sage", "bay leaf", "tarragon"]
        spices = ["salt", "pepper", "cumin", "paprika", "cinnamon", "nutmeg", "curry", "chili", "cayenne", "turmeric", "coriander", "cardamom"]
        bread = ["bread", "bun", "roll", "tortilla", "pita", "naan", "baguette", "croissant"]

        for item in produce:
            if item in name_lower:
                return "produce"
        for item in meat:
            if item in name_lower:
                return "meat"
        for item in seafood:
            if item in name_lower:
                return "seafood"
        for item in dairy:
            if item in name_lower:
                return "dairy"
        for item in cheese:
            if item in name_lower:
                return "cheese"
        for item in herbs:
            if item in name_lower:
                return "fresh_herbs"
        for item in spices:
            if item in name_lower:
                return "spices"
        for item in bread:
            if item in name_lower:
                return "bread"

        return "pantry"

    def _parse_duration(self, duration: Optional[str]) -> Optional[int]:
        """Parse ISO 8601 duration (PT30M) to minutes."""
        if not duration:
            return None

        import re
        # Match patterns like PT30M, PT1H30M, PT1H, etc.
        hours = 0
        minutes = 0

        h_match = re.search(r'(\d+)H', duration, re.IGNORECASE)
        m_match = re.search(r'(\d+)M', duration, re.IGNORECASE)

        if h_match:
            hours = int(h_match.group(1))
        if m_match:
            minutes = int(m_match.group(1))

        total = hours * 60 + minutes
        return total if total > 0 else None

    def _clean_html_for_parsing(self, html: str) -> str:
        """Clean HTML to reduce size for Claude parsing."""
        import re

        # Remove script and style tags
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<noscript[^>]*>.*?</noscript>', '', html, flags=re.DOTALL | re.IGNORECASE)

        # Remove comments
        html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)

        # Remove nav, header, footer, aside elements
        html = re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<header[^>]*>.*?</header>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<footer[^>]*>.*?</footer>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<aside[^>]*>.*?</aside>', '', html, flags=re.DOTALL | re.IGNORECASE)

        # Remove all HTML attributes except class and id (helps with size)
        html = re.sub(r'\s+(style|onclick|onload|data-[a-z-]+)="[^"]*"', '', html, flags=re.IGNORECASE)

        # Remove excessive whitespace
        html = re.sub(r'\s+', ' ', html)

        # Limit size
        return html[:15000]

    def _parse_recipe_with_claude(self, content: str, url: str) -> Optional[Recipe]:
        """Use Claude to parse recipe from cleaned HTML content."""
        prompt = f"""Extract the recipe from this webpage content and return it as structured JSON.

URL: {url}

Page content:
{content}

Return a JSON object with exactly this structure (no markdown, just JSON):
{{
    "name": "Recipe name",
    "servings": 4,
    "prep_time_min": 15,
    "cook_time_min": 30,
    "ingredients": [
        {{"name": "ingredient name", "quantity": 1.0, "unit": "cup", "category": "produce"}},
        ...
    ],
    "instructions": [
        "Step 1...",
        "Step 2...",
        ...
    ],
    "tags": ["tag1", "tag2"],
    "seasonal_ingredients": ["tomatoes", "corn"]
}}

For ingredients:
- Use standard units (cup, tbsp, tsp, lb, oz, each, clove, etc.)
- Category should be one of: produce, fresh_herbs, meat, seafood, dairy, cheese, pantry, spices, bread, specialty
- Include ALL ingredients, even salt and pepper

For tags, include relevant ones like:
- "quick" (under 30 min total), "easy", "kid-friendly", "healthy"
- Cuisine type: "italian", "mexican", "asian", etc.
- Cooking method: "grilled", "baked", "slow-cooker", etc.

For seasonal_ingredients, list any produce items that are seasonally dependent.

If you cannot extract a valid recipe, return {{"error": "reason"}}"""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )

        try:
            result_text = response.content[0].text.strip()
            # Handle potential markdown code blocks
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
            result = json.loads(result_text)

            if "error" in result:
                return None

            return self._json_to_recipe(result, source="url", source_url=url)
        except (json.JSONDecodeError, IndexError, KeyError):
            return None

    def extract_recipe_from_text(self, text: str, source_description: str = "text") -> Optional[Recipe]:
        """Extract structured recipe data from plain text."""
        prompt = f"""Extract the recipe from this text and return it as structured JSON.

Source: {source_description}

Text:
{text}

Return a JSON object with exactly this structure (no markdown, just JSON):
{{
    "name": "Recipe name",
    "servings": 4,
    "prep_time_min": 15,
    "cook_time_min": 30,
    "ingredients": [
        {{"name": "ingredient name", "quantity": 1.0, "unit": "cup", "category": "produce"}},
        ...
    ],
    "instructions": [
        "Step 1...",
        "Step 2...",
        ...
    ],
    "tags": ["tag1", "tag2"],
    "seasonal_ingredients": ["tomatoes", "corn"]
}}

For ingredients:
- Use standard units (cup, tbsp, tsp, lb, oz, each, clove, etc.)
- If quantity is vague (like "some" or "to taste"), use 0 for quantity and note it in the unit
- Category should be one of: produce, fresh_herbs, meat, seafood, dairy, cheese, pantry, spices, bread, specialty

For tags, include relevant ones like:
- "quick" (under 30 min total), "easy", "kid-friendly", "healthy"
- Cuisine type: "italian", "mexican", "asian", etc.

If you cannot extract a valid recipe, return {{"error": "reason"}}"""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )

        try:
            result_text = response.content[0].text.strip()
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
            result = json.loads(result_text)

            if "error" in result:
                return None

            return self._json_to_recipe(result, source="text", source_details=source_description)
        except (json.JSONDecodeError, IndexError, KeyError):
            return None

    def extract_recipe_from_image(self, image_data: bytes, media_type: str = "image/jpeg",
                                   source_description: str = "cookbook photo") -> Optional[Recipe]:
        """Extract structured recipe data from an image (e.g., cookbook page photo)."""
        base64_image = base64.standard_b64encode(image_data).decode("utf-8")

        prompt = """Extract the recipe from this image and return it as structured JSON.

Return a JSON object with exactly this structure (no markdown, just JSON):
{
    "name": "Recipe name",
    "servings": 4,
    "prep_time_min": 15,
    "cook_time_min": 30,
    "ingredients": [
        {"name": "ingredient name", "quantity": 1.0, "unit": "cup", "category": "produce"},
        ...
    ],
    "instructions": [
        "Step 1...",
        "Step 2...",
        ...
    ],
    "tags": ["tag1", "tag2"],
    "seasonal_ingredients": ["tomatoes", "corn"]
}

For ingredients:
- Use standard units (cup, tbsp, tsp, lb, oz, each, clove, etc.)
- If quantity is vague (like "some" or "to taste"), use 0 for quantity and note it in the unit
- Category should be one of: produce, fresh_herbs, meat, seafood, dairy, cheese, pantry, spices, bread, specialty

For tags, include relevant ones like:
- "quick" (under 30 min total), "easy", "kid-friendly", "healthy"
- Cuisine type: "italian", "mexican", "asian", etc.

If you cannot read or extract a valid recipe from the image, return {"error": "reason"}"""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64_image,
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }]
        )

        try:
            result_text = response.content[0].text.strip()
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
            result = json.loads(result_text)

            if "error" in result:
                return None

            return self._json_to_recipe(result, source="cookbook", source_details=source_description)
        except (json.JSONDecodeError, IndexError, KeyError):
            return None

    def assess_kid_friendliness(self, recipe: Recipe) -> float:
        """
        Assess how kid-friendly a recipe is (0-1 scale).
        Uses Claude to evaluate based on common kid preferences.
        """
        ingredients_list = ", ".join([i.name for i in recipe.ingredients])

        prompt = f"""Rate how kid-friendly this recipe is on a scale of 0 to 1.

Recipe: {recipe.name}
Ingredients: {ingredients_list}
Tags: {', '.join(recipe.tags)}

Consider:
- Kids often prefer: pasta, pizza, chicken nuggets/tenders, mac and cheese, tacos, grilled cheese, simple flavors
- Kids often dislike: spicy foods, bitter vegetables (brussels sprouts, kale), strong flavors, unfamiliar textures
- Mild, familiar flavors score higher
- Dishes that can be customized/deconstructed score higher

Return ONLY a single decimal number between 0 and 1 (e.g., 0.75). No other text."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}]
        )

        try:
            score = float(response.content[0].text.strip())
            return max(0, min(1, score))  # Clamp to 0-1
        except ValueError:
            return 0.5  # Default middle score

    def assess_health_score(self, recipe: Recipe) -> float:
        """
        Assess how healthy a recipe is (0-1 scale).
        """
        ingredients_list = ", ".join([f"{i.quantity} {i.unit} {i.name}" for i in recipe.ingredients])

        prompt = f"""Rate how healthy this recipe is on a scale of 0 to 1.

Recipe: {recipe.name}
Servings: {recipe.servings}
Ingredients: {ingredients_list}

Consider:
- Vegetable content and variety
- Lean proteins vs fatty/processed meats
- Whole grains vs refined
- Added sugars and sodium
- Portion sizes
- Overall nutritional balance

Return ONLY a single decimal number between 0 and 1 (e.g., 0.65). No other text."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}]
        )

        try:
            score = float(response.content[0].text.strip())
            return max(0, min(1, score))
        except ValueError:
            return 0.5

    def generate_meal_plan_explanation(self, meals: list[dict], context: dict) -> str:
        """
        Generate a natural language explanation of why these meals were chosen.
        """
        meals_text = "\n".join([f"- {m['day']}: {m['name']}" for m in meals])

        prompt = f"""Briefly explain (2-3 sentences) why this week's meal plan is good for this family.

Meals:
{meals_text}

Context:
- Season: {context.get('season', 'unknown')}
- Kid-friendly balance: {context.get('kid_friendly_pct', 0):.0%} of meals are kid favorites
- Seasonal produce used: {', '.join(context.get('seasonal_items', []))}

Keep it warm and conversational, like you're talking to the family. Focus on the positive aspects."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )

        return response.content[0].text.strip()

    def suggest_recipe_modifications(self, recipe: Recipe, feedback: str) -> str:
        """
        Suggest modifications to a recipe based on family feedback.
        """
        prompt = f"""Based on this family feedback, suggest 2-3 simple modifications to improve the recipe.

Recipe: {recipe.name}
Feedback: {feedback}

Keep suggestions practical and family-friendly. Be concise."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )

        return response.content[0].text.strip()

    def suggest_recipe_urls(self, meal_name: str) -> list[str]:
        """
        Search real recipe sites and extract actual recipe URLs.
        Returns a list of recipe URLs found from search results.
        """
        import re
        from urllib.parse import quote_plus

        # Build search URLs for reliable recipe sites
        search_query = quote_plus(meal_name)
        search_urls = [
            f"https://www.allrecipes.com/search?q={search_query}",
            f"https://www.seriouseats.com/search?q={search_query}",
            f"https://www.budgetbytes.com/?s={search_query}",
        ]

        recipe_urls = []

        for search_url in search_urls:
            try:
                response = httpx.get(search_url, follow_redirects=True, timeout=15.0)
                response.raise_for_status()
                html = response.text

                # Extract recipe links based on site patterns
                if "allrecipes.com" in search_url:
                    # AllRecipes recipe URLs look like: /recipe/12345/recipe-name/
                    matches = re.findall(r'href="(https://www\.allrecipes\.com/recipe/\d+/[^"]+)"', html)
                    recipe_urls.extend(matches[:2])

                elif "seriouseats.com" in search_url:
                    # Serious Eats URLs: /recipes/recipe-name
                    matches = re.findall(r'href="(https://www\.seriouseats\.com/[^"]*recipe[^"]*)"', html)
                    recipe_urls.extend(matches[:2])

                elif "budgetbytes.com" in search_url:
                    # Budget Bytes URLs in search results
                    matches = re.findall(r'href="(https://www\.budgetbytes\.com/[^"]+)"', html)
                    # Filter to likely recipe pages (not category/tag pages)
                    recipe_matches = [u for u in matches if '/category/' not in u and '/tag/' not in u and u.count('/') >= 4]
                    recipe_urls.extend(recipe_matches[:2])

            except Exception as e:
                continue

        # Remove duplicates while preserving order
        seen = set()
        unique_urls = []
        for url in recipe_urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)

        return unique_urls[:5]  # Return up to 5 URLs to try

    def find_recipe_for_meal(self, meal_name: str) -> Optional[Recipe]:
        """
        Find a real recipe for a meal name by searching known cooking sites.
        Tries multiple URLs until one works.
        """
        import logging
        logger = logging.getLogger(__name__)

        logger.info(f"Searching for recipe: {meal_name}")
        urls = self.suggest_recipe_urls(meal_name)
        logger.info(f"Found {len(urls)} potential URLs: {urls}")

        for url in urls:
            logger.info(f"Trying URL: {url}")
            recipe = self.extract_recipe_from_url(url)
            if recipe:
                logger.info(f"Successfully extracted recipe: {recipe.name}")
                return recipe
            else:
                logger.info(f"Failed to extract recipe from: {url}")

        logger.warning(f"No recipe found for: {meal_name}")
        return None

    def _json_to_recipe(self, data: dict, source: str, source_url: Optional[str] = None,
                        source_details: Optional[str] = None) -> Recipe:
        """Convert JSON data to a Recipe object."""
        ingredients = []
        for ing in data.get("ingredients", []):
            ingredients.append(Ingredient(
                name=ing.get("name", ""),
                quantity=float(ing.get("quantity", 0)),
                unit=ing.get("unit", ""),
                category=ing.get("category", "general"),
            ))

        return Recipe(
            name=data.get("name", "Unknown Recipe"),
            source=source,
            source_url=source_url,
            source_details=source_details,
            ingredients=ingredients,
            instructions=data.get("instructions", []),
            servings=data.get("servings", 4),
            prep_time_min=data.get("prep_time_min"),
            cook_time_min=data.get("cook_time_min"),
            tags=data.get("tags", []),
            seasonal_ingredients=data.get("seasonal_ingredients", []),
        )
