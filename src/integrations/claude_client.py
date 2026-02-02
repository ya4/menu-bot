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
        Fetches the page content and uses Claude to parse it.
        """
        # Fetch the page content
        try:
            response = httpx.get(url, follow_redirects=True, timeout=30.0)
            response.raise_for_status()
            html_content = response.text[:50000]  # Limit content size
        except Exception as e:
            return None

        prompt = f"""Extract the recipe from this webpage content and return it as structured JSON.

URL: {url}

Page content:
{html_content}

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

    def generate_recipe_from_name(self, meal_name: str) -> Optional[Recipe]:
        """
        Generate a complete recipe from just a meal name.
        Used to bootstrap recipes from family favorites.
        """
        prompt = f"""Create a complete, family-friendly recipe for: {meal_name}

Return a JSON object with exactly this structure (no markdown, just JSON):
{{
    "name": "{meal_name}",
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

Guidelines:
- Create a classic, approachable version of this dish that most families would enjoy
- Keep it practical - use common ingredients available at regular grocery stores
- Aim for a reasonable cooking time (under 1 hour total if possible)
- Include ALL ingredients, even basics like salt, pepper, and oil
- For ingredients, use standard units (cup, tbsp, tsp, lb, oz, each, clove, etc.)
- Category should be one of: produce, fresh_herbs, meat, seafood, dairy, cheese, pantry, spices, bread, specialty

For tags, include relevant ones like:
- "quick" (under 30 min total), "easy", "kid-friendly", "healthy"
- Cuisine type: "italian", "mexican", "asian", "american", etc.
- Cooking method: "grilled", "baked", "slow-cooker", "stovetop", etc.

For seasonal_ingredients, list any produce items that are seasonally dependent."""

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

            return self._json_to_recipe(result, source="generated", source_details=f"Generated from favorite: {meal_name}")
        except (json.JSONDecodeError, IndexError, KeyError):
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
