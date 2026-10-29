"""Startup menu parsing and profile evidence checks for StoryArt.

Split from style_pack_manager.py; the manager re-exports every name defined here.
"""

from __future__ import annotations

import argparse
import re
from typing import Sequence

try:
    from tools.style_pack_common import StylePackError
except ModuleNotFoundError:  # Direct execution from tools/
    from style_pack_common import StylePackError


STARTUP_CHOICES = ("OPTION_1", "OPTION_2", "OPTION_3", "CUSTOM")



def body_library_decision_supported(quote: str, decision: str) -> bool:
    """Require direct wording that supports the explicit library decision."""
    if not quote.strip() or not re.search(r"BODY_REFERENCE_LIBRARY|body\s+(?:reference\s+)?library|библиотек", quote, re.IGNORECASE):
        return False
    selected = decision.upper() == "SELECTED"
    if selected:
        return bool(re.search(r"\b(?:select(?:ed)?|use|using|enable|connect|include|with)\b|подключ|включ|использ|да", quote, re.IGNORECASE)) and not bool(
            re.search(r"\b(?:do\s+not|don't|not|without|decline(?:d)?)\b|не\s+(?:подключ|включ|использ)|без", quote, re.IGNORECASE)
        )
    if decision.upper() == "DECLINED":
        return bool(re.search(r"\b(?:do\s+not|don't|not|without|decline(?:d)?)\b|не\s+(?:подключ|включ|использ)|без", quote, re.IGNORECASE))
    return False



def menu_selection_fields(description: str) -> dict[str, str]:
    """Read exact choices shown in a complete profile, without inferring omissions."""
    fields: dict[str, str] = {}
    for part in description.split(";"):
        key, separator, value = part.strip().partition("=")
        if key not in {"style", "reference_policy", "character"}:
            continue
        if not separator or not value.strip() or key in fields:
            raise StylePackError("Menu selection fields must be unique and non-empty.")
        fields[key] = value.strip()
    if fields and set(fields) != {"style", "reference_policy", "character"}:
        raise StylePackError("A complete menu profile must record style, reference_policy, and character together.")
    return fields



def validate_menu_user_selections(
    startup: dict[str, object], selections: dict[str, object], *, allow_legacy_explicit: bool = False
) -> None:
    """Bind executable choices to the exact complete option displayed to the user."""
    if startup.get("selection_source_menu"):
        validate_menu_user_selections(
            startup["selection_source_menu"], selections, allow_legacy_explicit=allow_legacy_explicit
        )
        if startup.get("resolved_user_selections") != startup["selection_source_menu"].get("resolved_user_selections"):
            raise StylePackError("Reused choices differ from their original menu evidence.")
        return
    selected = startup.get("selected")
    option = next((row for row in startup.get("options", []) if row.get("id") == selected), {})
    fields = menu_selection_fields(str(option.get("description", "")))
    if selected in STARTUP_CHOICES[:3] and not fields:
        if not allow_legacy_explicit:
            raise StylePackError("Incomplete legacy menu: show the structured chooser for missing style/reference/character choices.")
        # Older plans only recorded the fidelity/library menu. Explicit current-chat
        # selections may fill the fields that menu never displayed.
        if any(
            not isinstance(selections.get(key), dict)
            or not str(selections[key].get("choice", "")).strip()
            or not str(selections[key].get("user_quote", "")).strip()
            for key in ("style", "reference_policy", "character")
        ):
            raise StylePackError("Legacy menu migration requires explicit style, reference_policy, and character choices with exact user quotes.")
        return
    resolved_fields = startup.get("resolved_user_selections", {})
    if fields != resolved_fields:
        raise StylePackError("Saved menu selections differ from the displayed option.")
    expected_quote = str(
        startup.get("resolved_selection_quote", startup.get("user_choice_quote", ""))
    ).strip()
    for key, value in fields.items():
        supplied = selections.get(key)
        if not isinstance(supplied, dict) or supplied.get("choice") != value:
            raise StylePackError(f"Executable {key} selection differs from the selected menu option.")
        if expected_quote and supplied.get("user_quote") != expected_quote:
            raise StylePackError(f"Executable {key} quote differs from the exact selected menu reply.")



def preset_profile_from_description(option_id: str, description: str) -> tuple[int, str]:
    menu_selection_fields(description)
    description = ";".join(part for part in description.split(";") if part.strip().partition("=")[0] not in {"style", "reference_policy", "character"})
    fidelity_matches = re.findall(r"(?<!\d)(30|50|70|90|100)\s*%?", description)
    if len(fidelity_matches) != 1:
        raise StylePackError(f"{option_id} must state exactly one fidelity percentage and BODY_REFERENCE_LIBRARY decision.")
    decision = "SELECTED" if body_library_decision_supported(description, "SELECTED") else "DECLINED" if body_library_decision_supported(description, "DECLINED") else ""
    if not decision:
        raise StylePackError(f"{option_id} must explicitly say whether BODY_REFERENCE_LIBRARY is selected or declined.")
    return int(fidelity_matches[0]), decision



def explicit_custom_profile(quote: str) -> tuple[int, str] | None:
    """Return a complete fidelity/library profile explicitly stated in free text."""
    fidelity_matches = re.findall(r"(?<!\d)(30|50|70|90|100)\s*%?(?!\d)", quote)
    if len(fidelity_matches) != 1:
        return None
    label = r"(?:BODY_REFERENCE_LIBRARY|body\s+(?:reference\s+)?library|библиотек\w*)"
    negative_before = rf"\b(?:do\s+not|don't|dont|without|decline(?:d)?|exclude)\b\s+(?:(?:to\s+)?(?:use|select|enable|connect|include)\s+)?(?:the\s+)?{label}|\bне\s+(?:использовать|используй|подключать|подключить|включать|включить|выбирать|выбрать)\s+(?:эту\s+)?{label}|\bбез\s+(?:этой\s+)?{label}|{label}\s+(?:(?:is|should\s+be|must\s+be)\s+)?(?:disabled|off|not\s+used|excluded|не\s+используется|отключена|отключен|отключить)"
    positive_before = rf"\b(?:select(?:ed)?|use|using|enable|enabled|connect|include|choose|activate|with)\b\s+(?:the\s+)?{label}|\b(?:подключить|подключи|подключение|включить|включи|использовать|используй|выбрать|выбираю)\s+(?:эту\s+)?{label}|\bс\s+(?:этой\s+)?{label}|{label}\s+(?:(?:is|should\s+be|must\s+be)\s+)?(?:enabled|on|selected|included|used|подключена|подключен|включена|включен|используется|подключить|использовать)"
    declined = bool(re.search(negative_before, quote, re.IGNORECASE))
    without_negative_library_spans = re.sub(negative_before, " ", quote, flags=re.IGNORECASE)
    selected = bool(re.search(positive_before, without_negative_library_spans, re.IGNORECASE))
    if selected == declined:
        return None
    return int(fidelity_matches[0]), "SELECTED" if selected else "DECLINED"



def parse_startup_option_descriptions(values: Sequence[str]) -> dict[str, str]:
    options: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise StylePackError("--startup-option must use OPTION_N=description.")
        option_id, description = value.split("=", 1)
        option_id = option_id.strip().upper()
        description = description.strip()
        if option_id not in STARTUP_CHOICES[:3] or not description:
            raise StylePackError("Startup menu requires non-empty OPTION_1, OPTION_2, and OPTION_3 entries.")
        if option_id in options:
            raise StylePackError(f"Duplicate startup option: {option_id}")
        options[option_id] = description
    if set(options) != set(STARTUP_CHOICES[:3]):
        raise StylePackError("Show and record exactly three complete startup profiles before CUSTOM.")
    profiles = {key: preset_profile_from_description(key, value) for key, value in options.items()}
    return options



def _normalized_menu_text(value: str) -> str:
    return " ".join(value.strip().casefold().split())



def validate_menu_choice_quote(quote: str, choice: str, displayed_label: str = "") -> None:
    normalized = quote.strip().casefold()
    if normalized in {"yes", "yeah", "yep", "ok", "okay", "да", "ага", "хорошо", "подходит"}:
        raise StylePackError("A bare yes/ok does not select a profile; record the user's numbered or named menu choice.")
    if choice == "CUSTOM" and explicit_custom_profile(quote) is not None:
        return
    number = STARTUP_CHOICES.index(choice) + 1 if choice in STARTUP_CHOICES[:3] else 4
    choices = {str(number), f"option {number}", f"option_{number}", f"вариант {number}", f"вариант_{number}"}
    if choice == "CUSTOM":
        choices.update({"custom", "указать свой вариант", "свой вариант"})
    elif choice == "OPTION_1":
        choices.update({"option_1 (recommended)", "option 1 (recommended)"})
    accepted = {_normalized_menu_text(value) for value in choices}
    if displayed_label.strip():
        accepted.add(_normalized_menu_text(displayed_label))
        # The chooser's visible label is the human-facing prefix; semicolon fields
        # are machine-readable style/reference/character bindings appended to it.
        visible_label = ";".join(
            part for part in displayed_label.split(";")
            if part.strip().partition("=")[0].strip() not in {"style", "reference_policy", "character"}
        )
        accepted.add(_normalized_menu_text(visible_label))
    if _normalized_menu_text(quote) not in accepted:
        raise StylePackError("Menu selection quote must exactly identify the selected visible option, not merely approve the preceding style name.")



def validate_startup_profile_evidence(startup: dict[str, object], fidelity: int | None = None) -> tuple[int, str]:
    """Validate the saved selection against the evidence type that produced it."""
    resolved = startup.get("resolved_parameters")
    if not isinstance(resolved, dict):
        raise StylePackError("The source plan has no verifiable startup profile parameters.")
    resolved_fidelity = int(resolved.get("fidelity", -1))
    decision = str(resolved.get("aux_body_decision", "")).upper()
    if resolved_fidelity not in {30, 50, 70, 90, 100} or decision not in {"SELECTED", "DECLINED"}:
        raise StylePackError("The source plan has an unresolved legacy profile; same-chat reuse cannot fabricate the missing BODY_REFERENCE_LIBRARY decision.")
    if fidelity is not None and resolved_fidelity != fidelity:
        raise StylePackError("Saved startup profile fidelity does not match the prepared reference plan.")

    state = str(startup.get("selection_state", ""))
    origin_state = str(startup.get("selection_origin_state", state))
    provenance = startup.get("confirmed_provenance")
    library = startup.get("body_library_provenance")
    if not isinstance(provenance, dict) or not all(str(provenance.get(key, "")).strip() for key in ("chat_id", "message_id", "quote")):
        raise StylePackError("Startup profiles require same-chat message and quote provenance.")
    if not isinstance(library, dict) or (
        library.get("chat_id") != provenance.get("chat_id")
        or not str(library.get("message_id", "")).strip()
        or str(library.get("decision", "")).upper() != decision
    ):
        raise StylePackError("The source plan does not contain same-chat evidence for its BODY_REFERENCE_LIBRARY decision.")

    if origin_state in {"USER_CONFIRMED_FROM_TEXT_MENU", "NEW_SELECTION"} and startup.get("menu_contract") == "THREE_AI_PRESETS_PLUS_CUSTOM":
        options = startup.get("options")
        if not isinstance(options, list) or [str(row.get("id", "")).upper() for row in options if isinstance(row, dict)] != [*STARTUP_CHOICES]:
            raise StylePackError("The source menu profile cannot be verified for same-chat reuse.")
        option_map = {str(row.get("id", "")).upper(): str(row.get("description", "")) for row in options if isinstance(row, dict)}
        profiles = {key: preset_profile_from_description(key, option_map[key]) for key in STARTUP_CHOICES[:3]}
        choice = str(startup.get("selected", "")).upper()
        user_choice_quote = str(startup.get("user_choice_quote", "")).strip()
        selected_description = option_map.get(choice, "")
        validate_menu_choice_quote(user_choice_quote, choice, selected_description)
        if str(provenance.get("quote", "")).strip() != user_choice_quote:
            raise StylePackError("The recorded menu-choice evidence differs from the exact user reply.")
        if choice == "CUSTOM":
            custom_quote = str(startup.get("custom_parameters_user_quote", "")).strip()
            profile = (resolved_fidelity, decision)
            if not custom_quote or str(provenance.get("quote", "")).strip() != user_choice_quote:
                raise StylePackError("CUSTOM requires its exact menu choice and a complete user-supplied profile quote.")
            if explicit_custom_profile(custom_quote) != profile:
                raise StylePackError("CUSTOM profile evidence must state its fidelity and BODY_REFERENCE_LIBRARY decision.")
            choice_profile = explicit_custom_profile(user_choice_quote)
            if choice_profile is not None and choice_profile != profile:
                raise StylePackError("Natural CUSTOM choice quote does not match its separately recorded full profile quote.")
            if str(library.get("quote", "")).strip() != custom_quote:
                raise StylePackError("CUSTOM library evidence must retain the user's complete profile quote.")
        elif choice in STARTUP_CHOICES[:3]:
            profile = profiles[choice]
            if str(library.get("quote", "")).strip() != user_choice_quote:
                raise StylePackError("The menu's BODY_REFERENCE_LIBRARY evidence must retain the exact selected-option reply.")
        else:
            raise StylePackError("The saved startup choice is not one of the displayed profiles.")
        if profile != (resolved_fidelity, decision):
            raise StylePackError("The saved menu choice does not match its resolved fidelity and BODY_REFERENCE_LIBRARY profile.")
        return resolved_fidelity, decision

    quote = str(provenance.get("quote", ""))
    library_quote = str(library.get("quote", ""))
    if not re.search(rf"(?<!\d){resolved_fidelity}\s*%?(?!\d)", quote):
        raise StylePackError("Direct confirmation provenance does not support the prepared fidelity.")
    if not body_library_decision_supported(library_quote, decision):
        raise StylePackError("The source plan does not contain same-chat wording supporting its BODY_REFERENCE_LIBRARY decision.")
    return resolved_fidelity, decision



def startup_clarification_requirements(args: argparse.Namespace) -> list[str]:
    """Return only critical startup values that are genuinely unresolved."""
    missing: list[str] = []
    fidelity = getattr(args, "fidelity", None)
    if fidelity not in {30, 50, 70, 90, 100}:
        missing.append("fidelity")
    selection_mode = getattr(args, "startup_selection_mode", "").upper()
    if selection_mode in {"DIRECT_CONFIRMATION", "USER_CONFIRMATION", "NEW"}:
        for argument, label in (
            ("confirmed_chat_id", "chat_id"),
            ("confirmed_message_id", "message_id"),
        ):
            if not str(getattr(args, argument, "")).strip():
                missing.append(label)
    if selection_mode == "DIRECT_CONFIRMATION":
        decision = str(getattr(args, "aux_body_decision", "NOT_SELECTED")).upper()
        if decision not in {"SELECTED", "DECLINED"}:
            missing.append("body_library_decision")
        quote = str(getattr(args, "confirmed_parameters_user_quote", ""))
        library_quote = str(getattr(args, "confirmed_body_library_user_quote", ""))
        if decision in {"SELECTED", "DECLINED"} and not (
            body_library_decision_supported(quote, decision)
            or body_library_decision_supported(library_quote, decision)
        ):
            missing.append("body_library_confirmation")
        for argument, label in (
            ("confirmed_parameters_user_quote", "fidelity_quote"),
        ):
            if not str(getattr(args, argument, "")).strip():
                missing.append(label)
        if library_quote.strip() and not str(getattr(args, "confirmed_body_library_message_id", "")).strip():
            missing.append("body_library_message_id")
        if library_quote.strip() and not str(getattr(args, "confirmed_body_library_chat_id", "")).strip():
            missing.append("body_library_chat_id")
    elif selection_mode in {"USER_CONFIRMATION", "NEW"}:
        if getattr(args, "startup_choice", "").upper() not in STARTUP_CHOICES:
            missing.append("menu_selection")
    return missing

