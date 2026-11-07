"""The fortune deck: card number (the ArUco tag id) -> name and meaning."""

CARDS = {
    19: ("The Sun", "warmth, clarity, success, joy that is easy to share"),
    18: ("The Moon", "intuition, dreams, uncertainty; trust your inner voice over appearances"),
    17: ("The Star", "hope, renewal, quiet confidence after a hard time"),
    10: ("Wheel of Fortune", "change, luck, a turning point; ride the cycle rather than fight it"),
    6: ("The Lovers", "connection, a meaningful choice, aligning with your values"),
    8: ("Strength", "courage, patience, gentle power over fear"),
    21: ("The World", "completion, achievement, the start of a bigger journey"),
}


def describe(card_id: int) -> str:
    name, meaning = CARDS[card_id]
    return f"{name}: {meaning}"
