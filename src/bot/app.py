"""
Main Slack bot application.
Entry point for Cloud Run deployment.
"""

import os
import logging

from flask import Flask, request
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler

from src.integrations.firestore_client import FirestoreClient
from src.integrations.claude_client import ClaudeClient
from src.integrations.google_tasks import GoogleTasksClient
from src.integrations.sheets_client import SheetsClient
from src.integrations.metrics_client import MetricsClient

from src.bot.handlers.bootstrap import BootstrapHandlers
from src.bot.handlers.recipes import RecipeHandlers
from src.bot.handlers.ratings import RatingHandlers
from src.bot.handlers.planning import PlanningHandlers
from src.bot.handlers.grocery import GroceryHandlers

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Slack app
slack_app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
)

# Initialize clients
# Legacy Firestore client (for migration period)
db = FirestoreClient()

# New hybrid architecture clients
sheets = SheetsClient() if os.environ.get("GOOGLE_SHEET_ID") else None
metrics = MetricsClient()
claude = ClaudeClient()
google_tasks = GoogleTasksClient()

# Register handlers (still using legacy db for now)
bootstrap_handlers = BootstrapHandlers(slack_app, db, claude, sheets)
recipe_handlers = RecipeHandlers(slack_app, db, claude)
rating_handlers = RatingHandlers(slack_app, db, metrics)
planning_handlers = PlanningHandlers(slack_app, db, claude)
grocery_handlers = GroceryHandlers(slack_app, db, google_tasks)


# Help command
@slack_app.command("/menu-help")
def handle_help(ack, respond):
    """Show help information."""
    ack()

    respond({
        "text": "Menu Bot Help",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Menu Bot Commands"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Setup & Configuration*\n"
                        "`/menu-setup` - Initial family setup\n"
                        "`/menu-add-parent @user` - Add a parent\n"
                        "`/menu-add-kid @user or name` - Add a kid\n"
                        "`/menu-add-favorites` - Add favorite meals\n"
                        "`/menu-find-recipes` - Find recipes for favorites\n"
                        "`/menu-init-sheet` - Initialize Google Sheet structure\n"
                        "`/menu-link-tasks` - Connect Google Tasks\n"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Recipes*\n"
                        "`/menu-add-recipe` - Add a recipe (URL or text)\n"
                        "`/menu-recipes` - List all recipes\n"
                        "_Or just share a recipe link in the channel!_\n"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Meal Planning*\n"
                        "`/menu-plan` - Show current meal plan\n"
                        "`/menu-plan new` - Generate new plan\n"
                        "`/menu-approve-plan` - Approve pending plan (parents only)\n"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Grocery Lists*\n"
                        "`/menu-grocery` - Show current grocery list\n"
                        "`/menu-grocery new` - Generate from meal plan\n"
                        "`/menu-grocery text` - Get as plain text\n"
                        "`/menu-approve-grocery` - Approve pending list (parents only)\n"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Feedback*\n"
                        "`/menu-rate` - Rate a recent meal\n"
                        "`/menu-feedback [text]` - Share detailed feedback\n"
                    ),
                },
            },
        ],
    })


# Debug command
@slack_app.command("/menu-debug")
def handle_debug(ack, body, respond):
    """Show debug information about the bot's state."""
    ack()

    user_id = body["user_id"]
    member = db.get_family_member(user_id)

    # Only parents can see debug info
    if not member or not member.is_parent:
        respond("Only parents can view debug information.")
        return

    # Gather debug info
    prefs = db.get_preferences()
    all_recipes = db.get_all_recipes()
    approved_recipes = [r for r in all_recipes if r.approved]
    members = db.get_all_family_members()
    current_plan = db.get_current_meal_plan()

    debug_text = f"""*Bot Debug Information*

*Setup Status*
- Bootstrap complete: {prefs.bootstrap_complete}
- Planning channel: {'Set' if prefs.planning_channel_id else 'Not set'}

*Family Members ({len(members)})*
"""
    for m in members:
        role = "Parent" if m.is_parent else "Kid" if m.user_type == "kid" else "Adult"
        debug_text += f"- {m.name} ({role})\n"

    debug_text += f"""
*Recipes*
- Total recipes: {len(all_recipes)}
- Approved recipes: {len(approved_recipes)}
- Ready for meal planning: {'Yes' if len(approved_recipes) >= 7 else f'No (need {7 - len(approved_recipes)} more)'}

*Favorite Meals ({len(prefs.favorite_meals)})*
"""
    if prefs.favorite_meals:
        for fav in prefs.favorite_meals[:5]:
            debug_text += f"- {fav}\n"
        if len(prefs.favorite_meals) > 5:
            debug_text += f"_...and {len(prefs.favorite_meals) - 5} more_\n"
    else:
        debug_text += "_No favorites saved_\n"

    debug_text += f"""
*Current Meal Plan*
- Active plan: {'Yes' if current_plan else 'No'}
"""
    if current_plan:
        debug_text += f"- Week of: {current_plan.week_start}\n"
        debug_text += f"- Status: {current_plan.status}\n"

    debug_text += """
*Troubleshooting Tips*
- If favorites exist but no recipes: Run `/menu-find-recipes`
- If 7+ approved recipes but plan fails: Check Cloud Run logs
- If URLs don't work: Bot may need to be re-invited to channel
"""

    respond(debug_text)


# App home
@slack_app.event("app_home_opened")
def handle_app_home(client, event):
    """Update the App Home tab."""
    user_id = event["user"]

    # Get user info
    member = db.get_family_member(user_id)
    prefs = db.get_preferences()

    if not prefs.bootstrap_complete:
        # Show setup prompt
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Welcome to Menu Bot!"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "I help families plan meals and create grocery lists.\n\n"
                        "To get started, use `/menu-setup` in a channel."
                    ),
                },
            },
        ]
    else:
        # Show status
        current_plan = db.get_current_meal_plan()
        recipes = db.get_all_recipes(approved_only=True)

        status_text = f"*{len(recipes)}* recipes in your collection\n"
        if current_plan:
            status_text += f"*Active meal plan* for week of {current_plan.week_start}\n"
        else:
            status_text += "_No active meal plan_\n"

        role_text = "Parent" if (member and member.is_parent) else "Family Member"

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Menu Bot"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Hello, {member.name if member else 'there'}! ({role_text})",
                },
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": status_text},
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Use `/menu-help` to see all available commands.",
                },
            },
        ]

    client.views_publish(
        user_id=user_id,
        view={
            "type": "home",
            "blocks": blocks,
        },
    )


# Flask app for Cloud Run
flask_app = Flask(__name__)
handler = SlackRequestHandler(slack_app)


@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    """Handle Slack events."""
    return handler.handle(request)


@flask_app.route("/slack/interactions", methods=["POST"])
def slack_interactions():
    """Handle Slack interactions (button clicks, etc.)."""
    return handler.handle(request)


@flask_app.route("/oauth/callback", methods=["GET"])
def oauth_callback():
    """Handle Google OAuth callback."""
    code = request.args.get("code")
    state = request.args.get("state")  # This is the Slack user ID

    if not code or not state:
        return "Missing authorization code or state", 400

    try:
        # Exchange code for tokens
        tokens = google_tasks.exchange_code_for_tokens(code)

        # Save refresh token for the user
        db.update_google_tasks_token(state, tokens["refresh_token"])

        return (
            "<html><body>"
            "<h1>Success!</h1>"
            "<p>Google Tasks connected. You can close this window and return to Slack.</p>"
            "</body></html>"
        )
    except Exception as e:
        logger.error(f"OAuth error: {e}")
        return f"Authorization failed: {str(e)}", 500


@flask_app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return "OK", 200


# Export for Cloud Run
app = flask_app

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
