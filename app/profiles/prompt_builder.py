from __future__ import annotations


def build_system_prompt(base: str, profile: dict, current_date: str) -> str:
    """Build a personalized system prompt from base + user profile data."""
    lines = [base]

    # Date FIRST — before any profile data — with emphatic format so the LLM
    # cannot override it with its training-data prior (RC-1, Plan 50).
    lines.append(
        f"\nIMPORTANT — Today is {current_date}. This is the REAL current date. "
        "Do NOT assume any other date from your training data."
    )

    if name := profile.get("name"):
        lines.append(f"The user's name is {name}.")
    if assistant_name := profile.get("assistant_name"):
        lines.append(f"Your name is {assistant_name}.")
    if occupation := profile.get("occupation"):
        lines.append(f"The user works as: {occupation}.")
    if use_cases := profile.get("use_cases"):
        lines.append(f"They mainly use you for: {use_cases}.")
    if tech_context := profile.get("tech_context"):
        lines.append(f"Technical context: {tech_context}.")
    if interests := profile.get("interests"):
        lines.append(f"Interests: {interests}.")
    if location := profile.get("location"):
        lines.append(f"Location: {location}.")
    if preferences := profile.get("preferences"):
        lines.append(f"Preferences: {preferences}.")

    if profile.get("debug_mode"):
        lines.append(
            "\n[🪲 DEBUG MODE ENABLED]: You are currently in auto-debug mode. If the user reports an error or asks to investigate, proactively use `get_recent_logs` to check internal backend execution errors, and `get_recent_messages` to review past conversation turns. Explain technical root causes explicitly."
        )

    return "\n".join(lines)
