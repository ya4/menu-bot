"""
Meal planning handlers for generating and managing weekly meal plans.
Includes parent-only approval controls.
"""

from datetime import datetime
from slack_bolt import App
from slack_sdk import WebClient

from src.integrations.firestore_client import FirestoreClient
from src.integrations.claude_client import ClaudeClient
from src.core.meal_planner import MealPlanner
from src.bot.slack_utils import format_meal_plan
from src.bot.access_control import (
    require_parent,
    require_parent_for_command,
    notify_parents,
    check_parent_status,
)


class PlanningHandlers:
    """Handlers for meal planning interactions."""

    def __init__(
        self,
        app: App,
        db: FirestoreClient,
        claude: ClaudeClient,
    ):
        """Initialize planning handlers."""
        self.app = app
        self.db = db
        self.claude = claude
        self.planner = MealPlanner(
            firestore_client=db,
            claude_client=claude,
        )
        self._register_handlers()

    def _register_handlers(self):
        """Register all planning-related handlers."""

        @self.app.command("/menu-plan")
        def handle_plan_command(ack, body, client, respond):
            """Generate a new meal plan or show the current one."""
            ack()

            text = body.get("text", "").strip().lower()
            user_id = body["user_id"]

            if text == "new" or text == "generate":
                # Generate new plan
                self._generate_new_plan(client, respond, user_id)
            elif text == "current" or not text:
                # Show current plan
                self._show_current_plan(respond)
            elif text == "pending":
                # Show pending plan awaiting approval
                self._show_pending_plan(respond)
            else:
                respond(
                    "Usage:\n"
                    "- `/menu-plan` - Show current meal plan\n"
                    "- `/menu-plan new` - Generate a new plan\n"
                    "- `/menu-plan pending` - Show plan awaiting approval"
                )

        @self.app.action("meal_plan_approve")
        @require_parent
        def handle_approve_plan(ack, body, client, say):
            """Handle meal plan approval (parent only)."""
            plan_id = body["actions"][0]["value"]
            user_id = body["user"]["id"]

            # Approve the plan
            self.db.approve_meal_plan(plan_id, user_id)

            # Get the plan for display
            plan = self.db.get_meal_plan(plan_id)

            # Update the message
            client.chat_update(
                channel=body["channel"]["id"],
                ts=body["message"]["ts"],
                **format_meal_plan(plan, show_actions=False),
            )

            say(f"Meal plan approved by <@{user_id}>! Time to generate the grocery list.")

            # Trigger grocery list generation
            from src.bot.handlers.grocery import GroceryHandlers
            # This would be called from the grocery handler in practice

        @self.app.action("meal_plan_regenerate")
        def handle_regenerate_plan(ack, body, client, say):
            """Handle request to regenerate the entire plan."""
            ack()

            user_id = body["user"]["id"]

            # Generate new plan
            try:
                new_plan = self.planner.generate_weekly_plan()
                plan_id = self.db.save_meal_plan(new_plan)
                new_plan.id = plan_id

                # Get explanation
                explanation = self.planner.get_plan_explanation(new_plan)

                # Update the message
                client.chat_update(
                    channel=body["channel"]["id"],
                    ts=body["message"]["ts"],
                    **format_meal_plan(new_plan, show_actions=True),
                )

                say(f"Here's a fresh plan! {explanation}")

            except Exception as e:
                say(f"Sorry, I couldn't generate a new plan: {str(e)}")

        @self.app.action("meal_plan_swap")
        def handle_swap_meals(ack, body, client):
            """Open modal to swap specific meals."""
            ack()

            plan_id = body["actions"][0]["value"]
            plan = self.db.get_meal_plan(plan_id)

            if not plan:
                return

            # Build options for which day to swap
            day_options = [
                {
                    "text": {"type": "plain_text", "text": f"{m.day_of_week}: {m.recipe_name}"},
                    "value": m.day_of_week,
                }
                for m in plan.meals
            ]

            client.views_open(
                trigger_id=body["trigger_id"],
                view={
                    "type": "modal",
                    "callback_id": "meal_swap_modal",
                    "private_metadata": plan_id,
                    "title": {"type": "plain_text", "text": "Swap Meal"},
                    "submit": {"type": "plain_text", "text": "Swap"},
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "Which meal would you like to swap?",
                            },
                        },
                        {
                            "type": "input",
                            "block_id": "day_block",
                            "element": {
                                "type": "static_select",
                                "action_id": "day_select",
                                "options": day_options,
                            },
                            "label": {"type": "plain_text", "text": "Day to swap"},
                        },
                    ],
                },
            )

        @self.app.view("meal_swap_modal")
        def handle_swap_submission(ack, body, client, view):
            """Handle meal swap modal submission."""
            ack()

            plan_id = view["private_metadata"]
            day_to_swap = view["state"]["values"]["day_block"]["day_select"]["selected_option"]["value"]

            plan = self.db.get_meal_plan(plan_id)
            if not plan:
                return

            # Regenerate just that day
            updated_plan = self.planner.regenerate_meal(plan, day_to_swap)
            self.db.save_meal_plan(updated_plan)

            # Post update to channel
            prefs = self.db.get_preferences()
            if prefs.planning_channel_id:
                client.chat_postMessage(
                    channel=prefs.planning_channel_id,
                    text=f"Swapped {day_to_swap}'s meal!",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"Swapped *{day_to_swap}*'s meal. Updated plan:",
                            },
                        },
                        *format_meal_plan(updated_plan, show_actions=True)["blocks"],
                    ],
                )

        @self.app.command("/menu-approve-plan")
        @require_parent_for_command
        def handle_approve_command(ack, body, client, respond):
            """Approve a pending meal plan via command."""
            user_id = body["user_id"]

            pending = self.db.get_pending_meal_plan()
            if not pending:
                respond("No meal plan pending approval.")
                return

            self.db.approve_meal_plan(pending.id, user_id)
            respond(f"Meal plan for week of {pending.week_start} approved!")

            # Notify in channel
            prefs = self.db.get_preferences()
            if prefs.planning_channel_id:
                client.chat_postMessage(
                    channel=prefs.planning_channel_id,
                    text=f"<@{user_id}> approved this week's meal plan!",
                )

    def _generate_new_plan(self, client: WebClient, respond, user_id: str):
        """Generate a new meal plan."""
        # Check for sufficient recipes
        recipes = self.db.get_all_recipes(approved_only=True)
        if len(recipes) < 7:
            respond(
                f"You only have {len(recipes)} approved recipes. "
                "Add at least 7 recipes before generating a meal plan!"
            )
            return

        respond("Generating your meal plan... This may take a moment.")

        try:
            # Generate the plan
            plan = self.planner.generate_weekly_plan()

            # Save it
            plan_id = self.db.save_meal_plan(plan)
            plan.id = plan_id

            # Get explanation and summary
            explanation = self.planner.get_plan_explanation(plan)
            summary = self.planner.get_plan_summary(plan)

            # Build response
            summary_text = (
                f"_{summary['kid_friendly_meals']}/{summary['total_meals']} kid-friendly meals, "
                f"{summary['quick_meals']} quick meals_"
            )

            prefs = self.db.get_preferences()
            channel_id = prefs.planning_channel_id

            # Post to channel
            if channel_id:
                # Notify parents for approval
                parent_mention = ""
                parents = self.db.get_parents()
                if parents:
                    parent_mention = " ".join([f"<@{p.slack_user_id}>" for p in parents])

                client.chat_postMessage(
                    channel=channel_id,
                    text=f"New meal plan ready for approval! {parent_mention}",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*New meal plan ready!* {parent_mention}\n\n{explanation}\n\n{summary_text}",
                            },
                        },
                        *format_meal_plan(plan, show_actions=True)["blocks"],
                    ],
                )
            else:
                respond({
                    "text": "New meal plan ready!",
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"{explanation}\n\n{summary_text}",
                            },
                        },
                        *format_meal_plan(plan, show_actions=True)["blocks"],
                    ],
                })

        except ValueError as e:
            respond(f"Couldn't generate plan: {str(e)}")
        except Exception as e:
            respond(f"An error occurred: {str(e)}")

    def _show_current_plan(self, respond):
        """Show the current active meal plan."""
        plan = self.db.get_current_meal_plan()

        if not plan:
            respond(
                "No active meal plan. Use `/menu-plan new` to generate one, "
                "or `/menu-plan pending` to check for plans awaiting approval."
            )
            return

        summary = self.planner.get_plan_summary(plan)
        summary_text = (
            f"_{summary['kid_friendly_meals']}/{summary['total_meals']} kid-friendly, "
            f"{summary['quick_meals']} quick meals_"
        )

        respond({
            "text": f"Current meal plan - Week of {plan.week_start}",
            "blocks": [
                *format_meal_plan(plan, show_actions=False)["blocks"],
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": summary_text}],
                },
            ],
        })

    def _show_pending_plan(self, respond):
        """Show any meal plan pending approval."""
        plan = self.db.get_pending_meal_plan()

        if not plan:
            respond("No meal plans pending approval.")
            return

        respond({
            "text": f"Pending meal plan - Week of {plan.week_start}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*This plan is awaiting parent approval:*",
                    },
                },
                *format_meal_plan(plan, show_actions=True)["blocks"],
            ],
        })

    def generate_weekly_plan_scheduled(self, client: WebClient):
        """Called by scheduled Cloud Function to generate weekly plan."""
        prefs = self.db.get_preferences()

        # Check if bootstrap is complete
        if not prefs.bootstrap_complete:
            return

        # Check for sufficient recipes
        recipes = self.db.get_all_recipes(approved_only=True)
        if len(recipes) < 7:
            return

        try:
            plan = self.planner.generate_weekly_plan()
            plan_id = self.db.save_meal_plan(plan)
            plan.id = plan_id

            explanation = self.planner.get_plan_explanation(plan)

            if prefs.planning_channel_id:
                parents = self.db.get_parents()
                parent_mention = " ".join([f"<@{p.slack_user_id}>" for p in parents])

                client.chat_postMessage(
                    channel=prefs.planning_channel_id,
                    text=f"New meal plan for next week! {parent_mention}",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"*Time to plan next week's meals!* {parent_mention}\n\n"
                                    f"{explanation}"
                                ),
                            },
                        },
                        *format_meal_plan(plan, show_actions=True)["blocks"],
                    ],
                )

        except Exception:
            pass  # Log error in production
