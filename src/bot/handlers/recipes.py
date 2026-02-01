"""
Recipe handlers for ingesting and managing recipes via Slack.
"""

import re
from slack_bolt import App
from slack_sdk import WebClient

from src.integrations.firestore_client import FirestoreClient
from src.integrations.claude_client import ClaudeClient
from src.core.recipe_extractor import RecipeExtractor
from src.bot.slack_utils import format_recipe_preview
from src.bot.access_control import check_parent_status


class RecipeHandlers:
    """Handlers for recipe-related Slack interactions."""

    def __init__(
        self,
        app: App,
        db: FirestoreClient,
        claude: ClaudeClient,
    ):
        """Initialize recipe handlers."""
        self.app = app
        self.db = db
        self.claude = claude
        self.extractor = RecipeExtractor(claude_client=claude, firestore_client=db)
        self._register_handlers()

    def _register_handlers(self):
        """Register all recipe-related handlers."""

        @self.app.event("message")
        def handle_message(event, client, say):
            """Handle messages that might contain recipes."""
            # Ignore bot messages and message edits
            if event.get("subtype") in ["bot_message", "message_changed", "message_deleted"]:
                return

            # Check if this is in the planning channel
            prefs = self.db.get_preferences()
            if prefs.planning_channel_id and event.get("channel") != prefs.planning_channel_id:
                return

            text = event.get("text", "")
            files = event.get("files", [])
            user_id = event.get("user", "")

            # Check for URLs that might be recipes
            if self._contains_recipe_url(text):
                self._process_potential_recipe(client, say, text, files, user_id, event)
                return

            # Check for image attachments (cookbook photos)
            if files and any(self._is_image_file(f) for f in files):
                # Only process if there's a hint it's a recipe
                if self._text_suggests_recipe(text):
                    self._process_potential_recipe(client, say, text, files, user_id, event)
                    return

        @self.app.command("/menu-add-recipe")
        def handle_add_recipe_command(ack, body, client, respond):
            """Handle the /menu-add-recipe command."""
            ack()

            text = body.get("text", "").strip()
            user_id = body["user_id"]

            if not text:
                # Open a modal for recipe input
                self._open_recipe_modal(client, body["trigger_id"])
                return

            # Check if it's a URL
            urls = self._extract_urls(text)
            if urls:
                respond("Extracting recipe from URL... This may take a moment.")
                recipe = self.extractor.extract_from_url(urls[0], user_id)
            else:
                respond("Processing recipe text...")
                recipe = self.extractor.extract_from_text(text, user_id)

            if recipe:
                # Save as draft (needs approval if not parent)
                is_parent = check_parent_status(user_id)
                recipe_id = self.extractor.save_recipe(recipe, approved=is_parent)
                recipe.id = recipe_id

                respond(**format_recipe_preview(recipe, show_actions=True))
            else:
                respond("I couldn't extract a recipe from that. Try sharing a recipe URL or more detailed text.")

        @self.app.view("recipe_input_modal")
        def handle_recipe_modal_submission(ack, body, client, view):
            """Handle recipe modal submission."""
            ack()

            user_id = body["user"]["id"]
            values = view["state"]["values"]

            recipe_text = values["recipe_text_block"]["recipe_text"]["value"]
            source = values.get("source_block", {}).get("source_input", {}).get("value", "manual entry")

            # Extract recipe
            recipe = self.extractor.extract_from_text(recipe_text, user_id)

            if recipe:
                recipe.source_details = source
                is_parent = check_parent_status(user_id)
                recipe_id = self.extractor.save_recipe(recipe, approved=is_parent)
                recipe.id = recipe_id

                # Post to planning channel
                prefs = self.db.get_preferences()
                if prefs.planning_channel_id:
                    client.chat_postMessage(
                        channel=prefs.planning_channel_id,
                        **format_recipe_preview(recipe, show_actions=True),
                    )

        @self.app.action("recipe_save")
        def handle_recipe_save(ack, body, client, say):
            """Handle the Save Recipe button."""
            ack()

            recipe_id = body["actions"][0]["value"]
            user_id = body["user"]["id"]

            # Approve the recipe
            is_parent = check_parent_status(user_id)
            if is_parent:
                self.db.approve_recipe(recipe_id, user_id)
                client.chat_postMessage(
                    channel=body["channel"]["id"],
                    text=f"Recipe saved and approved! It's now available for meal planning.",
                )
            else:
                client.chat_postMessage(
                    channel=body["channel"]["id"],
                    text="Recipe saved! A parent will need to approve it before it can be used in meal plans.",
                )

            # Update the original message to remove buttons
            self._update_message_remove_actions(client, body)

        @self.app.action("recipe_discard")
        def handle_recipe_discard(ack, body, client):
            """Handle the Discard Recipe button."""
            ack()

            # Just update the message to show it was discarded
            client.chat_update(
                channel=body["channel"]["id"],
                ts=body["message"]["ts"],
                text="Recipe discarded.",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "_Recipe discarded._",
                        },
                    },
                ],
            )

        @self.app.action("recipe_edit")
        def handle_recipe_edit(ack, body, client):
            """Handle the Edit Recipe button."""
            ack()

            recipe_id = body["actions"][0]["value"]
            recipe = self.db.get_recipe(recipe_id)

            if recipe:
                self._open_recipe_edit_modal(client, body["trigger_id"], recipe)

        @self.app.view("recipe_edit_modal")
        def handle_recipe_edit_submission(ack, body, client, view):
            """Handle recipe edit modal submission."""
            ack()

            user_id = body["user"]["id"]
            recipe_id = view["private_metadata"]
            values = view["state"]["values"]

            recipe = self.db.get_recipe(recipe_id)
            if not recipe:
                return

            # Update recipe fields
            recipe.name = values["name_block"]["name_input"]["value"]

            # Update servings if provided
            servings_val = values.get("servings_block", {}).get("servings_input", {}).get("value")
            if servings_val:
                try:
                    recipe.servings = int(servings_val)
                except ValueError:
                    pass

            # Update tags
            tags_val = values.get("tags_block", {}).get("tags_input", {}).get("value", "")
            if tags_val:
                recipe.tags = [t.strip() for t in tags_val.split(",") if t.strip()]

            self.db.save_recipe(recipe)

            # Notify in channel
            prefs = self.db.get_preferences()
            if prefs.planning_channel_id:
                client.chat_postMessage(
                    channel=prefs.planning_channel_id,
                    text=f"Recipe '{recipe.name}' has been updated!",
                )

        @self.app.command("/menu-recipes")
        def handle_list_recipes(ack, body, client, respond):
            """List all approved recipes."""
            ack()

            recipes = self.db.get_all_recipes(approved_only=True)

            if not recipes:
                respond("No recipes yet! Share a recipe link to get started.")
                return

            # Group by tags
            recipe_list = "\n".join([
                f"- *{r.name}*" + (f" `{', '.join(r.tags[:2])}`" if r.tags else "")
                for r in recipes[:20]
            ])

            more_text = f"\n_...and {len(recipes) - 20} more_" if len(recipes) > 20 else ""

            respond({
                "text": "Your recipes",
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": f"Your Recipes ({len(recipes)} total)"},
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": recipe_list + more_text},
                    },
                ],
            })

    def _process_potential_recipe(self, client, say, text, files, user_id, event):
        """Process a message that might contain a recipe."""
        # Add a reaction to show we're processing
        try:
            client.reactions_add(
                channel=event["channel"],
                timestamp=event["ts"],
                name="hourglass_flowing_sand",
            )
        except Exception:
            pass

        # Extract recipe
        recipe = self.extractor.extract_from_message(text, files, user_id)

        # Remove processing reaction
        try:
            client.reactions_remove(
                channel=event["channel"],
                timestamp=event["ts"],
                name="hourglass_flowing_sand",
            )
        except Exception:
            pass

        if recipe:
            # Check for duplicates
            existing = self.extractor.check_duplicate(recipe.name)
            if existing:
                say(
                    f"I found a recipe for *{recipe.name}*, but it looks like you already have "
                    f"a similar recipe saved. Would you like to save this as a new version?",
                    thread_ts=event["ts"],
                )
                return

            # Save as draft
            is_parent = check_parent_status(user_id)
            recipe_id = self.extractor.save_recipe(recipe, approved=is_parent)
            recipe.id = recipe_id

            # Add success reaction
            try:
                client.reactions_add(
                    channel=event["channel"],
                    timestamp=event["ts"],
                    name="white_check_mark",
                )
            except Exception:
                pass

            # Post preview
            say(
                **format_recipe_preview(recipe, show_actions=True),
                thread_ts=event["ts"],
            )

    def _contains_recipe_url(self, text: str) -> bool:
        """Check if text contains a URL that's likely a recipe."""
        recipe_domains = [
            "nytimes.com/recipes",
            "cooking.nytimes.com",
            "allrecipes.com",
            "foodnetwork.com",
            "epicurious.com",
            "bonappetit.com",
            "seriouseats.com",
            "food52.com",
            "budgetbytes.com",
            "skinnytaste.com",
            "delish.com",
            "tasty.co",
            "simplyrecipes.com",
        ]

        text_lower = text.lower()
        return any(domain in text_lower for domain in recipe_domains)

    def _text_suggests_recipe(self, text: str) -> bool:
        """Check if text suggests the user is sharing a recipe."""
        recipe_keywords = [
            "recipe", "cook", "make", "ingredients",
            "dinner", "meal", "dish", "try this",
        ]
        text_lower = text.lower()
        return any(kw in text_lower for kw in recipe_keywords)

    def _is_image_file(self, file: dict) -> bool:
        """Check if a file is an image."""
        mimetype = file.get("mimetype", "")
        return mimetype.startswith("image/")

    def _extract_urls(self, text: str) -> list[str]:
        """Extract URLs from text."""
        url_pattern = r'https?://[^\s<>]+'
        return re.findall(url_pattern, text)

    def _open_recipe_modal(self, client: WebClient, trigger_id: str):
        """Open the recipe input modal."""
        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "callback_id": "recipe_input_modal",
                "title": {"type": "plain_text", "text": "Add Recipe"},
                "submit": {"type": "plain_text", "text": "Add"},
                "blocks": [
                    {
                        "type": "input",
                        "block_id": "recipe_text_block",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "recipe_text",
                            "multiline": True,
                            "placeholder": {
                                "type": "plain_text",
                                "text": "Paste a recipe URL or the full recipe text...",
                            },
                        },
                        "label": {"type": "plain_text", "text": "Recipe"},
                    },
                    {
                        "type": "input",
                        "block_id": "source_block",
                        "optional": True,
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "source_input",
                            "placeholder": {
                                "type": "plain_text",
                                "text": "e.g., Grandma's cookbook, NYT Cooking",
                            },
                        },
                        "label": {"type": "plain_text", "text": "Source (optional)"},
                    },
                ],
            },
        )

    def _open_recipe_edit_modal(self, client: WebClient, trigger_id: str, recipe):
        """Open the recipe edit modal."""
        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "callback_id": "recipe_edit_modal",
                "private_metadata": recipe.id,
                "title": {"type": "plain_text", "text": "Edit Recipe"},
                "submit": {"type": "plain_text", "text": "Save"},
                "blocks": [
                    {
                        "type": "input",
                        "block_id": "name_block",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "name_input",
                            "initial_value": recipe.name,
                        },
                        "label": {"type": "plain_text", "text": "Recipe Name"},
                    },
                    {
                        "type": "input",
                        "block_id": "servings_block",
                        "optional": True,
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "servings_input",
                            "initial_value": str(recipe.servings),
                        },
                        "label": {"type": "plain_text", "text": "Servings"},
                    },
                    {
                        "type": "input",
                        "block_id": "tags_block",
                        "optional": True,
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "tags_input",
                            "initial_value": ", ".join(recipe.tags),
                            "placeholder": {
                                "type": "plain_text",
                                "text": "quick, kid-friendly, italian",
                            },
                        },
                        "label": {"type": "plain_text", "text": "Tags (comma-separated)"},
                    },
                ],
            },
        )

    def _update_message_remove_actions(self, client: WebClient, body: dict):
        """Update a message to remove action buttons."""
        message = body["message"]
        blocks = message.get("blocks", [])

        # Remove action blocks
        new_blocks = [b for b in blocks if b.get("type") != "actions"]

        # Add a confirmation
        new_blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "_Recipe saved_"}],
        })

        client.chat_update(
            channel=body["channel"]["id"],
            ts=message["ts"],
            text=message.get("text", "Recipe"),
            blocks=new_blocks,
        )
