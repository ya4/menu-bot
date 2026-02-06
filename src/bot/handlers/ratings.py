"""
Rating handlers for collecting meal feedback from family members.
"""

import re
from datetime import datetime
from slack_bolt import App
from slack_sdk import WebClient

from typing import Optional

from src.integrations.firestore_client import FirestoreClient, Rating
from src.integrations.metrics_client import MetricsClient
from src.bot.slack_utils import format_rating_prompt
from src.bot.access_control import get_user_type


class RatingHandlers:
    """Handlers for meal rating interactions."""

    def __init__(self, app: App, db: FirestoreClient, metrics: Optional[MetricsClient] = None):
        """Initialize rating handlers."""
        self.app = app
        self.db = db
        self.metrics = metrics  # Optional - for hybrid architecture
        self._register_handlers()

    def _register_handlers(self):
        """Register all rating-related handlers."""

        # Adult rating dropdown
        @self.app.action(re.compile(r"rating_adult_(.+)"))
        def handle_adult_rating(ack, body, client):
            """Handle adult star rating selection."""
            ack()

            action = body["actions"][0]
            recipe_id = action["action_id"].replace("rating_adult_", "")
            rating_value = int(action["selected_option"]["value"])
            user_id = body["user"]["id"]

            self._save_rating(
                recipe_id=recipe_id,
                user_id=user_id,
                rating=rating_value,
                user_type="adult",
            )

            # Update message to show rating recorded
            self._update_rating_message(client, body, f"Adult rated: {'*' * rating_value}")

        # Kid emoji ratings
        @self.app.action(re.compile(r"rating_kid_(good|ok|bad)_(.+)"))
        def handle_kid_rating(ack, body, client):
            """Handle kid emoji rating."""
            ack()

            action = body["actions"][0]
            match = re.match(r"rating_kid_(good|ok|bad)_(.+)", action["action_id"])
            if not match:
                return

            sentiment = match.group(1)
            recipe_id = match.group(2)
            user_id = body["user"]["id"]

            # Map sentiment to rating
            rating_map = {"good": 5, "ok": 3, "bad": 1}
            rating_value = rating_map.get(sentiment, 3)

            emoji_map = {"good": "Yummy!", "ok": "It's okay", "bad": "Yucky"}
            emoji = emoji_map.get(sentiment, "")

            self._save_rating(
                recipe_id=recipe_id,
                user_id=user_id,
                rating=rating_value,
                user_type="kid",
            )

            self._update_rating_message(client, body, f"Kid rated: {emoji}")

        # Would repeat buttons
        @self.app.action(re.compile(r"rating_repeat_(yes|no|maybe)_(.+)"))
        def handle_repeat_rating(ack, body, client):
            """Handle would-repeat rating."""
            ack()

            action = body["actions"][0]
            match = re.match(r"rating_repeat_(yes|no|maybe)_(.+)", action["action_id"])
            if not match:
                return

            answer = match.group(1)
            recipe_id = match.group(2)
            user_id = body["user"]["id"]

            # Map answer to boolean
            would_repeat = {"yes": True, "no": False, "maybe": None}.get(answer)

            # Update existing rating or create new one
            self._update_would_repeat(recipe_id, user_id, would_repeat)

            emoji_map = {"yes": "Yes", "no": "No", "maybe": "Maybe"}
            self._update_rating_message(client, body, f"Make again: {emoji_map.get(answer, '')}")

        @self.app.command("/menu-rate")
        def handle_rate_command(ack, body, client, respond):
            """Handle the /menu-rate command to rate a recent meal."""
            ack()

            # Get the current meal plan
            meal_plan = self.db.get_current_meal_plan()
            if not meal_plan:
                respond("No active meal plan to rate. Ratings are collected after meals are made!")
                return

            # Find today's or yesterday's meal
            today = datetime.now().strftime("%Y-%m-%d")
            yesterday = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)).strftime("%Y-%m-%d")

            recent_meal = None
            for meal in meal_plan.meals:
                if meal.date in [today, yesterday]:
                    recent_meal = meal
                    break

            if not recent_meal:
                respond("No recent meals to rate. I'll prompt you for feedback after dinner!")
                return

            respond(**format_rating_prompt(recent_meal.recipe_name, recent_meal.recipe_id))

        @self.app.command("/menu-feedback")
        def handle_feedback_command(ack, body, client, respond):
            """Handle detailed feedback for a meal."""
            ack()

            text = body.get("text", "").strip()
            user_id = body["user_id"]

            if not text:
                respond(
                    "Share your feedback about a meal! Usage:\n"
                    "`/menu-feedback The pasta was great but could use more garlic`"
                )
                return

            # Try to find the most recent meal to attach feedback to
            meal_plan = self.db.get_current_meal_plan()
            if meal_plan and meal_plan.meals:
                # Get most recent past meal
                today = datetime.now().strftime("%Y-%m-%d")
                recent_meal = None
                for meal in reversed(meal_plan.meals):
                    if meal.date <= today:
                        recent_meal = meal
                        break

                if recent_meal:
                    # Save as a rating note
                    self._save_rating(
                        recipe_id=recent_meal.recipe_id,
                        user_id=user_id,
                        rating=3,  # Neutral rating
                        user_type=get_user_type(user_id),
                        notes=text,
                    )
                    respond(f"Got it! I've saved your feedback about {recent_meal.recipe_name}.")
                    return

            respond("Thanks for the feedback! I'll keep that in mind for future meal planning.")

    def _save_rating(
        self,
        recipe_id: str,
        user_id: str,
        rating: int,
        user_type: str,
        notes: str = None,
        would_repeat: bool = None,
    ):
        """Save a rating to the database."""
        # Get user info
        member = self.db.get_family_member(user_id)
        user_name = member.name if member else "Unknown"
        if member:
            user_type = member.user_type

        # Get current meal plan for context
        meal_plan = self.db.get_current_meal_plan()
        meal_plan_id = meal_plan.id if meal_plan else None

        rating_obj = Rating(
            recipe_id=recipe_id,
            user_id=user_id,
            user_name=user_name,
            user_type=user_type,
            rating=rating,
            would_repeat=would_repeat,
            notes=notes,
            meal_plan_id=meal_plan_id,
            created_at=datetime.utcnow(),
        )

        self.db.save_rating(rating_obj)

        # Update recipe's kid_friendly_score based on ratings
        self._update_recipe_scores(recipe_id)

    def _update_would_repeat(self, recipe_id: str, user_id: str, would_repeat: bool):
        """Update the would_repeat field for an existing rating."""
        # For simplicity, save a new rating with just this field
        # In production, you might want to update the existing rating
        member = self.db.get_family_member(user_id)
        user_type = member.user_type if member else "adult"

        rating_obj = Rating(
            recipe_id=recipe_id,
            user_id=user_id,
            user_name=member.name if member else "Unknown",
            user_type=user_type,
            rating=3,  # Neutral
            would_repeat=would_repeat,
            created_at=datetime.utcnow(),
        )

        self.db.save_rating(rating_obj)

    def _update_recipe_scores(self, recipe_id: str):
        """Update the recipe's computed scores based on ratings."""
        ratings = self.db.get_ratings_for_recipe(recipe_id)
        recipe = self.db.get_recipe(recipe_id)

        if not recipe or not ratings:
            return

        # Calculate kid-friendly score from kid ratings
        kid_ratings = [r.rating for r in ratings if r.user_type == "kid"]
        if kid_ratings:
            # Normalize to 0-1 scale
            recipe.kid_friendly_score = sum(kid_ratings) / (len(kid_ratings) * 5)

        self.db.save_recipe(recipe)

    def _update_rating_message(self, client: WebClient, body: dict, status_text: str):
        """Update the rating message to show current status."""
        message = body["message"]
        blocks = message.get("blocks", [])

        # Add or update status context
        status_block = {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"_{status_text}_"}],
        }

        # Check if there's already a context block at the end
        if blocks and blocks[-1].get("type") == "context":
            # Update existing
            existing_text = blocks[-1]["elements"][0].get("text", "")
            if status_text not in existing_text:
                blocks[-1]["elements"][0]["text"] = f"{existing_text} | {status_text}"
        else:
            blocks.append(status_block)

        try:
            client.chat_update(
                channel=body["channel"]["id"],
                ts=message["ts"],
                text=message.get("text", "Rating"),
                blocks=blocks,
            )
        except Exception:
            pass  # Ignore update errors

    def send_rating_prompt(self, client: WebClient, channel_id: str, recipe_name: str, recipe_id: str):
        """Send a rating prompt to the channel."""
        client.chat_postMessage(
            channel=channel_id,
            **format_rating_prompt(recipe_name, recipe_id),
        )

    def collect_weekly_feedback(self, client: WebClient):
        """Send feedback requests for the past week's meals."""
        prefs = self.db.get_preferences()
        if not prefs.planning_channel_id:
            return

        # Get meal plans that need feedback
        plans = self.db.get_meal_plans_for_feedback()

        for plan in plans:
            # Send a summary feedback request
            meals_text = "\n".join([
                f"- {m.recipe_name}"
                for m in plan.meals
            ])

            client.chat_postMessage(
                channel=prefs.planning_channel_id,
                text="How was last week's meals?",
                blocks=[
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": "Weekly Feedback Time!",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"Last week we had:\n{meals_text}\n\nWhich meals should we make again?",
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Rate Meals"},
                                "action_id": f"weekly_feedback_start_{plan.id}",
                                "value": plan.id,
                            },
                        ],
                    },
                ],
            )

            # Mark feedback as requested (will be marked collected when done)
            plan.feedback_collected = True
            self.db.save_meal_plan(plan)
