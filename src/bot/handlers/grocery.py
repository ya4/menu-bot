"""
Grocery list handlers for generating and managing shopping lists.
Includes parent-only approval controls and Google Tasks integration.
"""

from slack_bolt import App
from slack_sdk import WebClient

from src.integrations.firestore_client import FirestoreClient
from src.integrations.google_tasks import GoogleTasksClient
from src.core.grocery_optimizer import GroceryOptimizer
from src.bot.slack_utils import format_grocery_list
from src.bot.access_control import require_parent, require_parent_for_command


class GroceryHandlers:
    """Handlers for grocery list interactions."""

    def __init__(
        self,
        app: App,
        db: FirestoreClient,
        google_tasks: GoogleTasksClient,
    ):
        """Initialize grocery handlers."""
        self.app = app
        self.db = db
        self.google_tasks = google_tasks
        self.optimizer = GroceryOptimizer(firestore_client=db)
        self._register_handlers()

    def _register_handlers(self):
        """Register all grocery-related handlers."""

        @self.app.command("/menu-grocery")
        def handle_grocery_command(ack, body, client, respond):
            """Generate or show grocery list."""
            ack()

            text = body.get("text", "").strip().lower()
            user_id = body["user_id"]

            if text == "new" or text == "generate":
                self._generate_grocery_list(client, respond, user_id)
            elif text == "current" or not text:
                self._show_current_list(respond)
            elif text == "pending":
                self._show_pending_list(respond)
            elif text == "text":
                self._show_list_as_text(respond)
            else:
                respond(
                    "Usage:\n"
                    "- `/menu-grocery` - Show current grocery list\n"
                    "- `/menu-grocery new` - Generate from current meal plan\n"
                    "- `/menu-grocery pending` - Show list awaiting approval\n"
                    "- `/menu-grocery text` - Get list as plain text"
                )

        @self.app.action("grocery_list_approve")
        @require_parent
        def handle_approve_list(ack, body, client, say):
            """Handle grocery list approval (parent only)."""
            list_id = body["actions"][0]["value"]
            user_id = body["user"]["id"]

            # Approve the list
            self.db.approve_grocery_list(list_id, user_id)

            # Get the list for display
            grocery_list = self.db.get_grocery_list(list_id)
            items_by_store = self.optimizer.get_list_by_store(grocery_list)

            # Update the message
            client.chat_update(
                channel=body["channel"]["id"],
                ts=body["message"]["ts"],
                **format_grocery_list(grocery_list, items_by_store, show_actions=True),
            )

            say(f"Grocery list approved by <@{user_id}>! Ready for shopping.")

        @self.app.action("grocery_list_sync_tasks")
        def handle_sync_to_tasks(ack, body, client, say):
            """Sync grocery list to Google Tasks."""
            ack()

            list_id = body["actions"][0]["value"]
            user_id = body["user"]["id"]

            # Check if user has Google Tasks linked
            member = self.db.get_family_member(user_id)
            if not member or not member.google_tasks_linked:
                # Send OAuth link
                self._send_tasks_oauth_prompt(client, body["channel"]["id"], user_id)
                return

            # Sync to Google Tasks
            grocery_list = self.db.get_grocery_list(list_id)
            if not grocery_list:
                say("Couldn't find the grocery list.")
                return

            try:
                tasks_id = self.google_tasks.sync_grocery_list(
                    refresh_token=member.google_refresh_token,
                    grocery_list=grocery_list,
                )

                # Update grocery list with tasks ID
                grocery_list.google_tasks_id = tasks_id
                self.db.save_grocery_list(grocery_list)

                say(
                    "Grocery list synced to Google Tasks! "
                    "Check your Tasks app to see the list organized by store."
                )

            except Exception as e:
                say(f"Failed to sync to Google Tasks: {str(e)}")

        @self.app.action("grocery_list_edit")
        def handle_edit_list(ack, body, client):
            """Open modal to edit grocery list."""
            ack()

            list_id = body["actions"][0]["value"]
            grocery_list = self.db.get_grocery_list(list_id)

            if not grocery_list:
                return

            # Build current items text
            items_text = "\n".join([
                f"{item.name} - {item.quantity} {item.unit} @ {item.store}"
                for item in grocery_list.items
            ])

            client.views_open(
                trigger_id=body["trigger_id"],
                view={
                    "type": "modal",
                    "callback_id": "grocery_edit_modal",
                    "private_metadata": list_id,
                    "title": {"type": "plain_text", "text": "Edit Grocery List"},
                    "submit": {"type": "plain_text", "text": "Save"},
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    "*Current items:*\n"
                                    "Format: `item name - quantity unit @ store`\n"
                                    "Stores: meijer, trader_joes, costco, buschs"
                                ),
                            },
                        },
                        {
                            "type": "input",
                            "block_id": "items_block",
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "items_input",
                                "multiline": True,
                                "initial_value": items_text,
                            },
                            "label": {"type": "plain_text", "text": "Items"},
                        },
                        {
                            "type": "input",
                            "block_id": "add_block",
                            "optional": True,
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "add_input",
                                "multiline": True,
                                "placeholder": {
                                    "type": "plain_text",
                                    "text": "Add extra items (one per line)",
                                },
                            },
                            "label": {"type": "plain_text", "text": "Add Extra Items"},
                        },
                    ],
                },
            )

        @self.app.view("grocery_edit_modal")
        def handle_edit_submission(ack, body, client, view):
            """Handle grocery list edit submission."""
            ack()

            list_id = view["private_metadata"]
            values = view["state"]["values"]

            # Parse items
            items_text = values["items_block"]["items_input"]["value"]
            add_text = values.get("add_block", {}).get("add_input", {}).get("value", "")

            grocery_list = self.db.get_grocery_list(list_id)
            if not grocery_list:
                return

            # Parse and update items
            new_items = self._parse_items_text(items_text)
            if add_text:
                new_items.extend(self._parse_simple_items(add_text))

            grocery_list.items = new_items
            self.db.save_grocery_list(grocery_list)

            # Notify in channel
            prefs = self.db.get_preferences()
            if prefs.planning_channel_id:
                client.chat_postMessage(
                    channel=prefs.planning_channel_id,
                    text="Grocery list updated!",
                )

        @self.app.command("/menu-link-tasks")
        def handle_link_tasks(ack, body, client, respond):
            """Start Google Tasks OAuth flow."""
            ack()

            user_id = body["user_id"]

            # Generate OAuth URL
            auth_url = self.google_tasks.get_authorization_url(state=user_id)

            respond({
                "text": "Link Google Tasks",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                "*Connect Google Tasks*\n\n"
                                "Click the button below to connect your Google account. "
                                "This allows me to sync grocery lists to your Google Tasks.\n\n"
                                "_I only request permission to manage your Tasks - nothing else._"
                            ),
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Connect Google Tasks"},
                                "url": auth_url,
                                "style": "primary",
                            },
                        ],
                    },
                ],
            })

        @self.app.command("/menu-approve-grocery")
        @require_parent_for_command
        def handle_approve_command(ack, body, client, respond):
            """Approve pending grocery list via command."""
            user_id = body["user_id"]

            pending = self.db.get_pending_grocery_list()
            if not pending:
                respond("No grocery list pending approval.")
                return

            self.db.approve_grocery_list(pending.id, user_id)
            respond(f"Grocery list for week of {pending.week_start} approved!")

            prefs = self.db.get_preferences()
            if prefs.planning_channel_id:
                client.chat_postMessage(
                    channel=prefs.planning_channel_id,
                    text=f"<@{user_id}> approved this week's grocery list!",
                )

    def _generate_grocery_list(self, client: WebClient, respond, user_id: str):
        """Generate a grocery list from the current meal plan."""
        # Get current or pending meal plan
        meal_plan = self.db.get_current_meal_plan()
        if not meal_plan:
            meal_plan = self.db.get_pending_meal_plan()

        if not meal_plan:
            respond(
                "No active meal plan to generate a grocery list from. "
                "Use `/menu-plan new` to create a meal plan first!"
            )
            return

        # Check if list already exists for this plan
        existing = self.db.get_grocery_list_for_plan(meal_plan.id)
        if existing:
            items_by_store = self.optimizer.get_list_by_store(existing)
            respond({
                "text": "Grocery list already exists for this meal plan",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "*A grocery list already exists for this meal plan:*",
                        },
                    },
                    *format_grocery_list(existing, items_by_store, show_actions=True)["blocks"],
                ],
            })
            return

        respond("Generating grocery list... This may take a moment.")

        try:
            # Generate the list
            grocery_list = self.optimizer.generate_grocery_list(meal_plan)

            # Save it
            list_id = self.db.save_grocery_list(grocery_list)
            grocery_list.id = list_id

            # Get items by store
            items_by_store = self.optimizer.get_list_by_store(grocery_list)
            summary = self.optimizer.get_store_summary(grocery_list)

            # Build summary text
            summary_parts = []
            for store_id, info in summary.items():
                summary_parts.append(f"{info['name']}: {info['item_count']} items")
            summary_text = " | ".join(summary_parts)

            prefs = self.db.get_preferences()
            channel_id = prefs.planning_channel_id

            if channel_id:
                parents = self.db.get_parents()
                parent_mention = " ".join([f"<@{p.slack_user_id}>" for p in parents])

                client.chat_postMessage(
                    channel=channel_id,
                    text=f"Grocery list ready! {parent_mention}",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"*Grocery list ready for approval!* {parent_mention}\n\n"
                                    f"_{summary_text}_"
                                ),
                            },
                        },
                        *format_grocery_list(grocery_list, items_by_store, show_actions=True)["blocks"],
                    ],
                )
            else:
                respond({
                    "text": "Grocery list ready!",
                    "blocks": [
                        {
                            "type": "context",
                            "elements": [{"type": "mrkdwn", "text": summary_text}],
                        },
                        *format_grocery_list(grocery_list, items_by_store, show_actions=True)["blocks"],
                    ],
                })

        except Exception as e:
            respond(f"An error occurred: {str(e)}")

    def _show_current_list(self, respond):
        """Show the current grocery list."""
        # Find the most recent approved list
        meal_plan = self.db.get_current_meal_plan()
        if not meal_plan:
            respond("No active meal plan. Generate one with `/menu-plan new`")
            return

        grocery_list = self.db.get_grocery_list_for_plan(meal_plan.id)
        if not grocery_list:
            respond(
                "No grocery list for the current meal plan. "
                "Generate one with `/menu-grocery new`"
            )
            return

        items_by_store = self.optimizer.get_list_by_store(grocery_list)
        respond(format_grocery_list(grocery_list, items_by_store, show_actions=True))

    def _show_pending_list(self, respond):
        """Show pending grocery list."""
        grocery_list = self.db.get_pending_grocery_list()
        if not grocery_list:
            respond("No grocery lists pending approval.")
            return

        items_by_store = self.optimizer.get_list_by_store(grocery_list)
        respond({
            "text": "Pending grocery list",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*This grocery list is awaiting parent approval:*",
                    },
                },
                *format_grocery_list(grocery_list, items_by_store, show_actions=True)["blocks"],
            ],
        })

    def _show_list_as_text(self, respond):
        """Show grocery list as plain text."""
        meal_plan = self.db.get_current_meal_plan()
        if not meal_plan:
            respond("No active meal plan.")
            return

        grocery_list = self.db.get_grocery_list_for_plan(meal_plan.id)
        if not grocery_list:
            respond("No grocery list for the current meal plan.")
            return

        text = self.optimizer.format_list_text(grocery_list)
        respond(f"```\n{text}\n```")

    def _send_tasks_oauth_prompt(self, client: WebClient, channel_id: str, user_id: str):
        """Send OAuth prompt for Google Tasks."""
        auth_url = self.google_tasks.get_authorization_url(state=user_id)

        client.chat_postMessage(
            channel=channel_id,
            text="Connect Google Tasks to sync your grocery list!",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "To sync your grocery list to Google Tasks, "
                            "you'll need to connect your Google account first."
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Connect Google Tasks"},
                            "url": auth_url,
                            "style": "primary",
                        },
                    ],
                },
            ],
        )

    def _parse_items_text(self, text: str):
        """Parse items from edit text format."""
        from src.integrations.firestore_client import GroceryItem

        items = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue

            try:
                # Format: name - quantity unit @ store
                parts = line.split(" @ ")
                store = parts[1].strip() if len(parts) > 1 else "meijer"

                name_qty = parts[0].rsplit(" - ", 1)
                name = name_qty[0].strip()

                if len(name_qty) > 1:
                    qty_unit = name_qty[1].strip().split(" ", 1)
                    quantity = float(qty_unit[0])
                    unit = qty_unit[1] if len(qty_unit) > 1 else "each"
                else:
                    quantity = 1
                    unit = "each"

                items.append(GroceryItem(
                    name=name,
                    quantity=quantity,
                    unit=unit,
                    store=store,
                    category="general",
                ))
            except Exception:
                continue

        return items

    def _parse_simple_items(self, text: str):
        """Parse simple item names."""
        from src.integrations.firestore_client import GroceryItem

        items = []
        for line in text.strip().split("\n"):
            name = line.strip()
            if name:
                items.append(GroceryItem(
                    name=name,
                    quantity=1,
                    unit="each",
                    store="meijer",
                    category="general",
                ))
        return items

    def generate_grocery_list_scheduled(self, client: WebClient):
        """Called by scheduled function after meal plan is approved."""
        meal_plan = self.db.get_current_meal_plan()
        if not meal_plan:
            return

        # Check if list already exists
        existing = self.db.get_grocery_list_for_plan(meal_plan.id)
        if existing:
            return

        prefs = self.db.get_preferences()
        if not prefs.planning_channel_id:
            return

        try:
            grocery_list = self.optimizer.generate_grocery_list(meal_plan)
            list_id = self.db.save_grocery_list(grocery_list)
            grocery_list.id = list_id

            items_by_store = self.optimizer.get_list_by_store(grocery_list)

            parents = self.db.get_parents()
            parent_mention = " ".join([f"<@{p.slack_user_id}>" for p in parents])

            client.chat_postMessage(
                channel=prefs.planning_channel_id,
                text=f"Grocery list ready! {parent_mention}",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Grocery list ready for approval!* {parent_mention}",
                        },
                    },
                    *format_grocery_list(grocery_list, items_by_store, show_actions=True)["blocks"],
                ],
            )

        except Exception:
            pass
