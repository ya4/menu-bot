"""
Bootstrap handlers for initial setup of the Menu Bot.
Walks the family through setting up members and initial meal preferences.
"""

import re
from slack_bolt import App
from slack_sdk import WebClient

from src.integrations.firestore_client import FirestoreClient, FamilyMember, Preferences
from src.bot.slack_utils import format_bootstrap_welcome


class BootstrapHandlers:
    """Handlers for the bootstrap/setup flow."""

    def __init__(self, app: App, db: FirestoreClient):
        """Initialize bootstrap handlers."""
        self.app = app
        self.db = db
        self._register_handlers()

    def _register_handlers(self):
        """Register all bootstrap-related handlers."""

        @self.app.command("/menu-setup")
        def handle_setup_command(ack, body, client, respond):
            """Handle the /menu-setup command to start or continue setup."""
            ack()

            user_id = body["user_id"]
            channel_id = body["channel_id"]

            # Check if bootstrap is already complete
            prefs = self.db.get_preferences()
            if prefs.bootstrap_complete:
                respond(
                    "Setup is already complete! Use `/menu-help` to see available commands, "
                    "or `/menu-settings` to modify your settings."
                )
                return

            # Check if this is the first user - they become a parent
            members = self.db.get_all_family_members()
            if not members:
                # First user is automatically a parent
                self._open_setup_modal(client, body["trigger_id"], user_id, is_first=True)
            else:
                # Check if user is already registered
                existing = self.db.get_family_member(user_id)
                if existing and existing.is_parent:
                    self._open_setup_modal(client, body["trigger_id"], user_id, is_first=False)
                else:
                    respond(
                        "Only parents can run setup. Ask a parent to complete the setup, "
                        "or have them add you as a parent."
                    )

        @self.app.view("bootstrap_family_setup")
        def handle_family_setup_submission(ack, body, client, view):
            """Handle the family setup modal submission."""
            ack()

            user_id = body["user"]["id"]
            values = view["state"]["values"]

            # Extract family members from the form
            members_text = values["members_block"]["members_input"]["value"]
            channel_id = values["channel_block"]["channel_select"]["selected_channel"]

            # Parse members
            members = self._parse_members(members_text, user_id)

            # Save members
            for member in members:
                self.db.save_family_member(member)

            # Save the planning channel
            prefs = self.db.get_preferences()
            prefs.planning_channel_id = channel_id
            self.db.save_preferences(prefs)

            # Notify in channel
            client.chat_postMessage(
                channel=channel_id,
                text=f"Family setup complete! I've registered {len(members)} family members.",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"Family setup complete! I've registered *{len(members)}* family members.\n\n"
                                "*Next step:* Let's add some recipes you already love! "
                                "Share links, photos, or descriptions of your favorite meals.\n\n"
                                "Or use `/menu-add-favorites` to quickly add a list of go-to meals."
                            ),
                        },
                    },
                ],
            )

        @self.app.command("/menu-add-favorites")
        def handle_add_favorites_command(ack, body, client, respond):
            """Handle command to add initial favorite meals."""
            ack()

            user_id = body["user_id"]

            # Only parents can do this
            if not self.db.is_parent(user_id):
                respond("Only parents can add favorite meals during setup.")
                return

            self._open_favorites_modal(client, body["trigger_id"])

        @self.app.view("bootstrap_favorites")
        def handle_favorites_submission(ack, body, client, view):
            """Handle the favorites modal submission."""
            ack()

            values = view["state"]["values"]
            favorites_text = values["favorites_block"]["favorites_input"]["value"]

            # Parse favorites (one per line)
            favorites = [
                line.strip()
                for line in favorites_text.strip().split("\n")
                if line.strip()
            ]

            # Get channel
            prefs = self.db.get_preferences()
            channel_id = prefs.planning_channel_id

            if channel_id:
                client.chat_postMessage(
                    channel=channel_id,
                    text=f"Got it! I've noted {len(favorites)} favorite meals.",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"Got it! I've noted *{len(favorites)}* favorite meals:\n"
                                    + "\n".join([f"- {f}" for f in favorites[:10]])
                                    + (f"\n_...and {len(favorites) - 10} more_" if len(favorites) > 10 else "")
                                    + "\n\nI'll look for recipes matching these meals. "
                                    "You can also share recipe links directly in this channel!"
                                ),
                            },
                        },
                    ],
                )

            # Mark bootstrap as complete
            self.db.set_bootstrap_complete()

        @self.app.command("/menu-add-parent")
        def handle_add_parent(ack, body, client, respond):
            """Add another parent to the system."""
            ack()

            user_id = body["user_id"]
            text = body.get("text", "").strip()

            if not self.db.is_parent(user_id):
                respond("Only existing parents can add new parents.")
                return

            if not text:
                respond("Please specify a user: `/menu-add-parent @username`")
                return

            new_parent_id = None
            name = None

            # Try to extract user ID from Slack mention format <@U12345|username>
            match = re.search(r"<@([A-Z0-9]+)\|?[^>]*>", text)
            if match:
                new_parent_id = match.group(1)
            else:
                # Try to find user by username/display name
                # Remove @ prefix if present
                search_name = text.lstrip("@")
                try:
                    # Search for the user in the workspace
                    users_response = client.users_list()
                    for user in users_response.get("members", []):
                        if user.get("deleted") or user.get("is_bot"):
                            continue
                        # Check various name fields
                        if (user.get("name", "").lower() == search_name.lower() or
                            user.get("profile", {}).get("display_name", "").lower() == search_name.lower() or
                            user.get("profile", {}).get("real_name", "").lower() == search_name.lower()):
                            new_parent_id = user["id"]
                            name = user.get("real_name") or user.get("name")
                            break
                except Exception as e:
                    respond(f"Error searching for user: {str(e)}")
                    return

            if not new_parent_id:
                respond(
                    f"Couldn't find user '{text}'. Try mentioning them directly "
                    f"(type @ and select from the dropdown) or check the username."
                )
                return

            # Get user info if we don't have it yet
            if not name:
                try:
                    user_info = client.users_info(user=new_parent_id)
                    name = user_info["user"]["real_name"] or user_info["user"]["name"]
                except Exception:
                    name = "Unknown"

            # Check if already a member
            existing = self.db.get_family_member(new_parent_id)
            if existing:
                existing.is_parent = True
                self.db.save_family_member(existing)
                respond(f"Updated <@{new_parent_id}> to parent status!")
            else:
                member = FamilyMember(
                    slack_user_id=new_parent_id,
                    name=name,
                    user_type="adult",
                    is_parent=True,
                    preference_weight=1.0,
                )
                self.db.save_family_member(member)
                respond(f"Added <@{new_parent_id}> as a parent!")

        @self.app.command("/menu-add-kid")
        def handle_add_kid(ack, body, client, respond):
            """Add a kid to the system."""
            ack()

            user_id = body["user_id"]
            text = body.get("text", "").strip()

            if not self.db.is_parent(user_id):
                respond("Only parents can add family members.")
                return

            # Kids might not have Slack accounts, so accept just a name
            if text.startswith("<@"):
                # Slack user mentioned
                match = re.search(r"<@([A-Z0-9]+)\|?[^>]*>", text)
                if match:
                    kid_id = match.group(1)
                    try:
                        user_info = client.users_info(user=kid_id)
                        name = user_info["user"]["real_name"] or user_info["user"]["name"]
                    except Exception:
                        name = text.replace(f"<@{kid_id}>", "").strip() or "Unknown"

                    member = FamilyMember(
                        slack_user_id=kid_id,
                        name=name,
                        user_type="kid",
                        is_parent=False,
                        preference_weight=1.5,  # Kids get higher weight
                    )
                    self.db.save_family_member(member)
                    respond(f"Added <@{kid_id}> as a kid! Their meal preferences will be prioritized.")
            else:
                # Just a name, no Slack account
                # Generate a placeholder ID
                kid_id = f"kid_{text.lower().replace(' ', '_')}"
                member = FamilyMember(
                    slack_user_id=kid_id,
                    name=text,
                    user_type="kid",
                    is_parent=False,
                    preference_weight=1.5,
                )
                self.db.save_family_member(member)
                respond(f"Added {text} as a kid! Their meal preferences will be prioritized.")

    def _open_setup_modal(self, client: WebClient, trigger_id: str, user_id: str, is_first: bool):
        """Open the family setup modal."""
        # Get user's name for pre-filling
        try:
            user_info = client.users_info(user=user_id)
            user_name = user_info["user"]["real_name"] or user_info["user"]["name"]
        except Exception:
            user_name = "Parent 1"

        initial_text = f"@{user_name} - parent\n"

        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "callback_id": "bootstrap_family_setup",
                "title": {"type": "plain_text", "text": "Family Setup"},
                "submit": {"type": "plain_text", "text": "Save"},
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                "Let's set up your family! List each family member, "
                                "one per line, in this format:\n"
                                "`@username - parent` or `@username - kid` or `Name - kid`"
                            ),
                        },
                    },
                    {
                        "type": "input",
                        "block_id": "members_block",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "members_input",
                            "multiline": True,
                            "initial_value": initial_text,
                            "placeholder": {
                                "type": "plain_text",
                                "text": "@mom - parent\n@dad - parent\nEmma - kid\nJack - kid",
                            },
                        },
                        "label": {"type": "plain_text", "text": "Family Members"},
                    },
                    {
                        "type": "input",
                        "block_id": "channel_block",
                        "element": {
                            "type": "channels_select",
                            "action_id": "channel_select",
                            "placeholder": {
                                "type": "plain_text",
                                "text": "Select a channel",
                            },
                        },
                        "label": {"type": "plain_text", "text": "Meal Planning Channel"},
                        "hint": {
                            "type": "plain_text",
                            "text": "Where should I post meal plans and grocery lists?",
                        },
                    },
                ],
            },
        )

    def _open_favorites_modal(self, client: WebClient, trigger_id: str):
        """Open the favorites input modal."""
        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "callback_id": "bootstrap_favorites",
                "title": {"type": "plain_text", "text": "Favorite Meals"},
                "submit": {"type": "plain_text", "text": "Save"},
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                "List some meals your family already loves! "
                                "One meal per line. These will help me learn your preferences.\n\n"
                                "Don't worry about being specific - I'll help find recipes later."
                            ),
                        },
                    },
                    {
                        "type": "input",
                        "block_id": "favorites_block",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "favorites_input",
                            "multiline": True,
                            "placeholder": {
                                "type": "plain_text",
                                "text": "Tacos\nSpaghetti and meatballs\nChicken stir-fry\nPizza\nGrilled cheese",
                            },
                        },
                        "label": {"type": "plain_text", "text": "Favorite Meals"},
                    },
                ],
            },
        )

    def _parse_members(self, text: str, submitter_id: str) -> list[FamilyMember]:
        """Parse family members from the setup text."""
        members = []
        lines = text.strip().split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Parse line format: @username - type or Name - type
            parts = line.rsplit("-", 1)
            if len(parts) != 2:
                continue

            name_part = parts[0].strip()
            type_part = parts[1].strip().lower()

            is_parent = type_part == "parent"
            user_type = "adult" if is_parent else "kid"
            weight = 1.0 if user_type == "adult" else 1.5

            # Check for Slack mention
            match = re.search(r"<@([A-Z0-9]+)\|?([^>]*)>", name_part)
            if match:
                slack_id = match.group(1)
                name = match.group(2) or name_part.replace(f"<@{slack_id}>", "").strip()
                if not name:
                    name = f"User {slack_id[:4]}"
            elif name_part.startswith("@"):
                # @username format without full mention
                name = name_part[1:]
                slack_id = f"pending_{name.lower().replace(' ', '_')}"
            else:
                # Just a name
                name = name_part
                slack_id = f"member_{name.lower().replace(' ', '_')}"

            members.append(FamilyMember(
                slack_user_id=slack_id,
                name=name,
                user_type=user_type,
                is_parent=is_parent,
                preference_weight=weight,
            ))

        # Ensure submitter is a parent if this is first setup
        submitter_found = any(m.slack_user_id == submitter_id for m in members)
        if not submitter_found:
            members.append(FamilyMember(
                slack_user_id=submitter_id,
                name="Parent",
                user_type="adult",
                is_parent=True,
                preference_weight=1.0,
            ))

        return members
